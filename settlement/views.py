from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from decimal import Decimal

from groups.models import Group, GroupMessage # Standardized import path
from bill_buddy.response import custom_response
from .models import Settlement
from .serializers import SettlementSerializer
from expense.utils import group_net_balances, suggested_settlement_amount

# Real-time WebSocket support
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


def _broadcast_system_message(group_id, message):
    channel_layer = get_channel_layer()
    transaction.on_commit(
        lambda: async_to_sync(channel_layer.group_send)(
            f"chat_{group_id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": message,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        ),
        robust=True
    )


class RecordSettlementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        """Returns the settlement history log for a specific group."""
        group = get_object_or_404(Group, id=group_id)
        
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="You are not a member of this group.", status_code=status.HTTP_403_FORBIDDEN
            )
            
        settlements = Settlement.objects.filter(group=group)
        serializer = SettlementSerializer(settlements, many=True)
        return custom_response(
            success=True, 
            message="Settlement history retrieved successfully.", 
            data=serializer.data
        )

    def post(self, request, group_id):
        """Records a new peer-to-peer settlement payment securely."""
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="You are not a member of this group.", status_code=status.HTTP_403_FORBIDDEN
            )

        serializer = SettlementSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return custom_response(
                success=False, message="Validation error", errors=serializer.errors, status_code=status.HTTP_400_BAD_REQUEST
            )

        paid_by_user = request.user
        paid_to_user = serializer.validated_data['paid_to']
        amount_to_settle = serializer.validated_data['amount']

        if paid_by_user == paid_to_user:
            return custom_response(
                success=False,
                message="A user cannot settle with themselves.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return custom_response(
                    success=False,
                    message="You are not a member of this group.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            if not group.members.filter(id=paid_to_user.id).exists():
                return custom_response(
                    success=False,
                    message="The recipient is not a member of this group.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            balances = group_net_balances(group)
            maximum_settlement = suggested_settlement_amount(
                balances,
                debtor_id=paid_by_user.id,
                creditor_id=paid_to_user.id
            )
            if maximum_settlement <= 0 or amount_to_settle > maximum_settlement:
                return custom_response(
                    success=False,
                    message="This payer-recipient pair is not a current suggested settlement, or the amount exceeds the suggested limit.",
                    errors={
                        "amount": (
                            f"Maximum valid payment for this suggested pair is "
                            f"{maximum_settlement.quantize(Decimal('0.01'))}."
                        )
                    },
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            settlement = serializer.save(paid_by=paid_by_user, group=group)

            system_msg = f"{request.user.username} settled ${settlement.amount} with {paid_to_user.username}."
            GroupMessage.objects.create(group=group, sender=None, message=system_msg)

        _broadcast_system_message(group.id, system_msg)

        return custom_response(
            success=True,
            message="Settlement recorded successfully.",
            data=SettlementSerializer(settlement).data,
            status_code=status.HTTP_201_CREATED
        )


class SettlementDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, settlement_id):
        """Allows the person who logged the settlement to undo/delete it."""
        settlement = get_object_or_404(Settlement, id=settlement_id)

        # 🔒 Security: Only the person who made the payment can delete it
        if settlement.paid_by != request.user:
            return custom_response(
                success=False, 
                message="Permission denied. Only the payer can delete this settlement record.", 
                status_code=status.HTTP_403_FORBIDDEN
            )

        group_id = settlement.group.id
        with transaction.atomic():
            Group.objects.select_for_update().get(id=group_id)
            if not Group.objects.filter(
                id=group_id, members__id=request.user.id
            ).exists():
                return custom_response(
                    success=False,
                    message="Only a current group member can remove this settlement.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            settlement = get_object_or_404(
                Settlement.objects.select_for_update().select_related('paid_to', 'paid_by'),
                id=settlement_id
            )
            if settlement.paid_by_id != request.user.id:
                return custom_response(
                    success=False,
                    message="Permission denied. Only the payer can delete this settlement record.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            recipient_name = settlement.paid_to.username
            amount = settlement.amount
            settlement.delete()
            
            # Log the rollback to the database chat history log too
            delete_msg = f"⚠️ {request.user.username} deleted the settlement record of ${amount} to {recipient_name}."
            GroupMessage.objects.create(group_id=group_id, sender=None, message=delete_msg)

        _broadcast_system_message(group_id, delete_msg)

        return custom_response(
            success=True,
            message="Settlement record removed successfully."
        )