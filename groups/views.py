from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404

from bill_buddy.response import custom_response
from .models import Group, GroupMessage
from .serializers import GroupSerializer, GroupMessageSerializer

# Real-time WebSocket support for the REST post method
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

class GroupListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Fetch all groups that the logged-in user belongs to."""
        groups = request.user.joined_groups.all().order_by('-created_at')
        serializer = GroupSerializer(groups, many=True)
        return custom_response(
            success=True,
            message="User groups retrieved successfully.",
            data=serializer.data
        )

    def post(self, request):
        """Create a new group and automatically attach the creator as member #1."""
        serializer = GroupSerializer(data=request.data)
        if not serializer.is_valid():
            return custom_response(
                success=False, message="Validation error", errors=serializer.errors, status_code=status.HTTP_400_BAD_REQUEST
            )
        
        # Save group with authenticated user as the creator
        group = serializer.save(creator=request.user)
        # Explicitly append them to the many-to-many members array too!
        group.members.add(request.user)

        return custom_response(
            success=True,
            message="Group created successfully.",
            data=GroupSerializer(group).data,
            status_code=status.HTTP_201_CREATED
        )


class GroupDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        """Fetch details for a single specific group (essential for dashboard headers)."""
        group = get_object_or_404(Group, id=group_id)
        
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="Access denied. You are not a member of this group.", status_code=status.HTTP_403_FORBIDDEN
            )
            
        serializer = GroupSerializer(group)
        return custom_response(
            success=True,
            message="Group details retrieved successfully.",
            data=serializer.data
        )


class JoinGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Allows a user to join an existing group using its unique join_code."""
        join_code = request.data.get('join_code', '').strip().upper()
        
        if not join_code:
            return custom_response(
                success=False, message="Join code is required.", status_code=status.HTTP_400_BAD_REQUEST
            )

        # Look up the group by code
        group = Group.objects.filter(join_code=join_code).first()
        if not group:
            return custom_response(
                success=False, message="Invalid join code. Group not found.", status_code=status.HTTP_404_NOT_FOUND
            )

        # Safety Check: Are they already in it?
        if group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="You are already a member of this group.", status_code=status.HTTP_400_BAD_REQUEST
            )

        # Add user to the group
        group.members.add(request.user)

        # ⚡ REAL-TIME: Notify active websocket chatters that someone new stepped in!
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "chat_message",
                "username": "SYSTEM",
                "message": f"🎉 {request.user.username} joined the group!"
            }
        )

        return custom_response(
            success=True,
            message=f"Successfully joined group: '{group.name}'.",
            data=GroupSerializer(group).data
        )


class GroupChatView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        """Fetch the scrollback/chat history log for a group."""
        group = get_object_or_404(Group, id=group_id)
        
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="Access denied.", status_code=status.HTTP_403_FORBIDDEN
            )
            
        # Get last 100 messages
        messages = GroupMessage.objects.filter(group=group).order_by('-timestamp')[:100]
        serializer = GroupMessageSerializer(reversed(messages), many=True)
        
        return custom_response(
            success=True,
            message="Chat history retrieved.",
            data=serializer.data
        )

    def post(self, request, group_id):
        """🚀 ADDED: Standard REST fallback endpoint to send a message to the group chat."""
        group = get_object_or_404(Group, id=group_id)
        
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False, message="Access denied.", status_code=status.HTTP_403_FORBIDDEN
            )
            
        message_text = request.data.get('message', '').strip()
        if not message_text:
            return custom_response(
                success=False, message="Message content cannot be empty.", status_code=status.HTTP_400_BAD_REQUEST
            )
            
        # 1. Store to Database
        chat_msg = GroupMessage.objects.create(
            group=group,
            sender=request.user,
            message=message_text
        )
        
        # 2. Sync to active Websocket Clients out there live
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "chat_message",
                "username": request.user.username,
                "message": message_text
            }
        )
        
        return custom_response(
            success=True,
            message="Message sent successfully.",
            data=GroupMessageSerializer(chat_msg).data,
            status_code=status.HTTP_201_CREATED
        )