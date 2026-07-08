from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
from decimal import Decimal

from groups.models import Group
from bill_buddy.response import custom_response
from .models import Settlement
from .serializers import SettlementSerializer
from expense.models import Expense, ExpenseShare

# Real-time WebSocket support
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

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

        # Ensure the person making the API request is part of the group
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="You are not a member of this group.", status_code=status.HTTP_403_FORBIDDEN
            )

        serializer = SettlementSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return custom_response(
                success=False, message="Validation error", errors=serializer.errors, status_code=status.HTTP_400_BAD_REQUEST
            )

        # 👥 1. RESOLVE PAYER: Get paid_by from JSON data, or fall back to request.user
        paid_by_user = serializer.validated_data.get('paid_by', request.user)
        if not group.members.filter(id=paid_by_user.id).exists():
            return custom_response(
                success=False, message="The payer is not a member of this group.", status_code=status.HTTP_400_BAD_REQUEST
            )

        # 👥 2. RESOLVE RECIPIENT
        paid_to_user = serializer.validated_data['paid_to']
        if not group.members.filter(id=paid_to_user.id).exists():
            return custom_response(
                success=False, message="The recipient is not a member of this group.", status_code=status.HTTP_400_BAD_REQUEST
            )

        amount_to_settle = Decimal(str(serializer.validated_data['amount']))

        # 🔒 3. BOUNDARY SAFETY CHECK: Calculate current net balance of the target payer
        user_balance = Decimal('0.00')
        for exp in Expense.objects.filter(group=group, paid_by=paid_by_user):
            user_balance += exp.amount
        for share in ExpenseShare.objects.filter(expense__group=group, user=paid_by_user):
            user_balance -= share.amount
        for s in Settlement.objects.filter(group=group):
            if s.paid_by == paid_by_user:
                user_balance += s.amount
            if s.paid_to == paid_by_user:
                user_balance -= s.amount

        # Debt is the inverse of a negative balance
        current_debt = -user_balance if user_balance < 0 else Decimal('0.00')

        if amount_to_settle > current_debt:
            return custom_response(
                success=False, 
                message=f"Validation error: Cannot settle ${amount_to_settle} because {paid_by_user.username} only owes ${current_debt.quantize(Decimal('0.01'))}.", 
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 💾 4. COMMIT EXECUTION
        with transaction.atomic():
            settlement = serializer.save(paid_by=paid_by_user, group=group)

            # Build contextual system log text based on proxy logging
            if paid_by_user != request.user:
                system_msg = f"✅ {request.user.username} recorded a settlement: {paid_by_user.username} paid ${settlement.amount} to {paid_to_user.username}."
            else:
                system_msg = f"✅ {request.user.username} settled ${settlement.amount} with {paid_to_user.username}."
                
            from groups.models import GroupMessage
            GroupMessage.objects.create(group=group, sender=None, message=system_msg)

        # ⚡ REAL-TIME: Broadcast to WebSockets
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",  
            {
                "type": "chat_message",
                "username": "SYSTEM",
                "message": system_msg
            }
        )

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
        recipient_name = settlement.paid_to.username
        amount = settlement.amount

        with transaction.atomic():
            settlement.delete()
            
            # Log the rollback to the database chat history log too
            delete_msg = f"⚠️ {request.user.username} deleted the settlement record of ${amount} to {recipient_name}."
            from groups.models import GroupMessage
            GroupMessage.objects.create(group_id=group_id, sender=None, message=delete_msg)

        # ⚡ REAL-TIME: Notify room channel layer that a payment was undone
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group_id}",  
            {
                "type": "chat_message",
                "username": "SYSTEM",
                "message": delete_msg
            }
        )

        return custom_response(
            success=True,
            message="Settlement record removed successfully."
        )