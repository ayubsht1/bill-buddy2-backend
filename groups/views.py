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

from django.contrib.auth import get_user_model
User = get_user_model()

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

    def delete(self, request, group_id):
        """💥 Destroys the group entirely. Restricted exclusively to the Group Owner."""
        group = get_object_or_404(Group, id=group_id)

        # 🔒 Security Guard: Only the group's creator/owner can delete it
        if group.creator != request.user:
            return custom_response(
                success=False,
                message="Access denied. Only the group owner can delete this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        group_name = group.name
        warn_msg = f"🚨 This group has been deleted by its owner ({request.user.username})."

        # ⚡ Real-Time: Warn active socket subscribers right before teardown using our unified layout contract
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": warn_msg,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        )

        # Execute the database cascade wipeout
        group.delete()

        return custom_response(
            success=True,
            message=f"Group '{group_name}' and all its associated financial data have been successfully deleted."
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

        # 🚀 WRITE TO DB: Save the system log for historical scrollback
        join_msg = f"🎉 {request.user.username} joined the group!"
        GroupMessage.objects.create(group=group, sender=None, message=join_msg)

        # ⚡ REAL-TIME: Fixed to structure properly with consumer channel architectures
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": join_msg,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
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
        """Fetch the scrollback history log, excluding hidden deleted items or customizing view."""
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(success=False, message="Access denied.", status_code=status.HTTP_403_FORBIDDEN)
            
        messages = GroupMessage.objects.filter(group=group).order_by('-timestamp')[:100]
        serializer = GroupMessageSerializer(reversed(messages), many=True)
        return custom_response(success=True, message="Chat history retrieved.", data=serializer.data)

    def post(self, request, group_id):
        """Handles standard messaging, along with Reply and Forward mechanics."""
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(success=False, message="Access denied.", status_code=status.HTTP_403_FORBIDDEN)
            
        message_text = request.data.get('message', '').strip()
        reply_id = request.data.get('reply_to_id')
        forward_msg_id = request.data.get('forward_message_id')

        # 1. Handle Forward Mechanic Option
        if forward_msg_id:
            orig_msg = get_object_or_404(GroupMessage, id=forward_msg_id)
            # Verify user has access to read the source group message
            if not orig_msg.group.members.filter(id=request.user.id).exists():
                return custom_response(success=False, message="Cannot forward a message you cannot access.", status_code=status.HTTP_403_FORBIDDEN)
            message_text = orig_msg.message  # Copy content across
            is_forwarded = True
        else:
            is_forwarded = False

        if not message_text and not forward_msg_id:
            return custom_response(success=False, message="Message content cannot be empty.", status_code=status.HTTP_400_BAD_REQUEST)

        # 2. Handle Reply verification link
        reply_to_obj = None
        if reply_id:
            reply_to_obj = GroupMessage.objects.filter(id=reply_id, group=group).first()

        # Build entry
        chat_msg = GroupMessage.objects.create(
            group=group,
            sender=request.user,
            message=message_text,
            reply_to=reply_to_obj,
            is_forwarded=is_forwarded
        )
        
        # Real-time WebSocket broadcast trigger configuration
        self._broadcast_to_sockets(group.id, "chat_message", GroupMessageSerializer(chat_msg).data)
        
        return custom_response(success=True, message="Sent.", data=GroupMessageSerializer(chat_msg).data, status_code=status.HTTP_201_CREATED)

    def patch(self, request, group_id):
        """📌 PIN OR 🗑️ SOFT-DELETE a targeted message inside a chat room."""
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(success=False, message="Access denied.", status_code=status.HTTP_403_FORBIDDEN)

        message_id = request.data.get('message_id')
        action = request.data.get('action') # 'pin', 'unpin', or 'delete'
        
        msg = get_object_or_404(GroupMessage, id=message_id, group=group)

        if action in ['pin', 'unpin']:
            msg.is_pinned = (action == 'pin')
            msg.save()
        elif action == 'delete':
            # Security Rule: Only the sender or group creator can delete a text message
            if msg.sender != request.user and group.creator != request.user:
                return custom_response(success=False, message="Unauthorized action.", status_code=status.HTTP_403_FORBIDDEN)
            msg.is_deleted = True
            msg.save()
        else:
            return custom_response(success=False, message="Invalid action query parameter.", status_code=status.HTTP_400_BAD_REQUEST)

        # Notify active clients about the state update via WebSockets
        serialized_data = GroupMessageSerializer(msg).data
        self._broadcast_to_sockets(group.id, "message_update", serialized_data)

        return custom_response(success=True, message=f"Message action '{action}' executed successfully.", data=serialized_data)

    def _broadcast_to_sockets(self, group_id, event_type, data):
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group_id}",
            {
                "type": "room_event",
                "event_type": event_type,
                "data": data
            }
        )
    
class AddGroupMemberView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        """Allows ONLY the group creator to directly force-add a member via identifier (email or username)."""
        group = get_object_or_404(Group, id=group_id)

        # 🔒 Security: Only the creator of the group is authorized to invoke this route
        if group.creator != request.user:
            return custom_response(
                success=False, 
                message="Access denied. Only the group creator can directly add members.", 
                status_code=status.HTTP_403_FORBIDDEN
            )

        identifier = request.data.get('identifier', '').strip()
        if not identifier:
            return custom_response(
                success=False, 
                message="Please provide an email or username string as an 'identifier'.", 
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 🔍 Find the targeted user by email or username
        target_user = User.objects.filter(email=identifier).first() or User.objects.filter(username=identifier).first()
        
        if not target_user:
            return custom_response(
                success=False, 
                message="User not found with the provided credential.", 
                status_code=status.HTTP_404_NOT_FOUND
            )

        # Safety Check: Is this user already in the group array?
        if group.members.filter(id=target_user.id).exists():
            return custom_response(
                success=False, 
                message="That user is already a member of this group.", 
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # ➕ Inject them into the ManyToMany field
        group.members.add(target_user)

        # 🚀 WRITE TO DB: Save the system log for historical scrollback
        add_msg = f"🛠️ {request.user.username} added {target_user.username} to the group."
        GroupMessage.objects.create(group=group, sender=None, message=add_msg)

        # ⚡ Real-Time: Announce via WebSockets room channel
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,  # System message placeholder
                    "sender_username": "SYSTEM",
                    "message": add_msg,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        )

        return custom_response(
            success=True,
            message=f"Successfully added {target_user.username} to the group.",
            data=GroupSerializer(group).data
        )
    
class RemoveGroupMemberView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, group_id, user_id):
        """
        Handles removing a member from a group.
        - If the requester is the Creator: They can remove anyone (kick).
        - If the requester is a Member: They can only remove themselves (leave).
        """
        group = get_object_or_404(Group, id=group_id)
        user_to_remove = get_object_or_404(User, id=user_id)

        # 1. Check if the user to remove is actually in the group
        if not group.members.filter(id=user_to_remove.id).exists():
            return custom_response(
                success=False, 
                message="The specified user is not a member of this group.", 
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 2. Authorization Rules
        is_creator = (group.creator == request.user)
        is_self_removing = (user_to_remove == request.user)

        if not (is_creator or is_self_removing):
            return custom_response(
                success=False, 
                message="Access denied. You can only remove members if you are the group creator.", 
                status_code=status.HTTP_403_FORBIDDEN
            )

        # 3. Guard: Prevent the creator from accidentally abandoning their own group
        if is_self_removing and is_creator:
            return custom_response(
                success=False, 
                message="As the creator, you cannot leave the group. You must delete the group entirely or transfer ownership.", 
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 4. Remove the user from the ManyToMany field
        group.members.remove(user_to_remove)

        # 5. Define audit log messaging based on action type
        if is_creator and not is_self_removing:
            broadcast_message = f"❌ {request.user.username} removed {user_to_remove.username} from the group."
            success_message = f"Successfully removed {user_to_remove.username}."
        else:
            broadcast_message = f"🏃 {user_to_remove.username} has left the group."
            success_message = "You have successfully left the group."

        # 🚀 WRITE TO DB: Save the system log for historical scrollback
        GroupMessage.objects.create(group=group, sender=None, message=broadcast_message)

        # ⚡ Real-Time: Broadcast the action via WebSockets room channel
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": broadcast_message,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        )

        return custom_response(
            success=True,
            message=success_message,
            data=GroupSerializer(group).data
        )