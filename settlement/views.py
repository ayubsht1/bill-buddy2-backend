from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404

from groups.models import Group
from bill_buddy.response import custom_response
from .models import Settlement
from .serializers import SettlementSerializer

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
        """Records a new peer-to-peer settlement payment."""
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

        paid_to_user = serializer.validated_data['paid_to']
        if not group.members.filter(id=paid_to_user.id).exists():
            return custom_response(
                success=False, message="The recipient is not a member of this group.", status_code=status.HTTP_400_BAD_REQUEST
            )

        settlement = serializer.save(paid_by=request.user, group=group)

        # ⚡ REAL-TIME: Broadcast to WebSockets
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",  
            {
                "type": "chat_message",
                "username": "SYSTEM",
                "message": f"✅ {request.user.username} settled ${settlement.amount} with {paid_to_user.username}."
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
        payer_name = settlement.paid_by.username
        recipient_name = settlement.paid_to.username
        amount = settlement.amount

        settlement.delete()

        # ⚡ REAL-TIME: Notify room channel layer that a payment was undone
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group_id}",  
            {
                "type": "chat_message",
                "username": "SYSTEM",
                "message": f"⚠️ {request.user.username} deleted the settlement record of ${amount} to {recipient_name}."
            }
        )

        return custom_response(
            success=True,
            message="Settlement record removed successfully."
        )