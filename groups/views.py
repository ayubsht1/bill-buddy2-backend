from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction

from bill_buddy.response import custom_response
from .models import Group, GroupMessage, GroupMembership
from .serializers import GroupSerializer, GroupMessageSerializer, GroupMembershipSerializer

# Real-time WebSocket support for the REST post method
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from expense.utils import group_net_balances

from django.contrib.auth import get_user_model
User = get_user_model()

class GroupListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Fetch all groups that the logged-in user belongs to."""
        groups = request.user.joined_groups.select_related('creator').prefetch_related(
            'memberships__user'
        ).order_by('-created_at')
        serializer = GroupSerializer(groups, many=True, context={'request': request})
        return custom_response(
            success=True,
            message="User groups retrieved successfully.",
            data=serializer.data
        )

    def post(self, request):
        """Create a new group and automatically attach the creator as member #1 with owner role."""
        serializer = GroupSerializer(data=request.data)
        if not serializer.is_valid():
            return custom_response(
                success=False, message="Validation error", errors=serializer.errors, status_code=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            group = serializer.save(creator=request.user)
            GroupMembership.objects.create(group=group, user=request.user, role=GroupMembership.Role.OWNER)

        return custom_response(
            success=True,
            message="Group created successfully.",
            data=GroupSerializer(group, context={'request': request}).data,
            status_code=status.HTTP_201_CREATED
        )


class GroupDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_group_for_member(self, request, group_id):
        group = get_object_or_404(
            Group.objects.select_related('creator').prefetch_related('memberships__user'),
            id=group_id
        )
        if not group.members.filter(id=request.user.id).exists():
            return None
        return group

    def get(self, request, group_id):
        """Fetch details for a single specific group (essential for dashboard headers)."""
        group = self._get_group_for_member(request, group_id)
        if group is None:
            return custom_response(
                success=False, message="Access denied. You are not a member of this group.", status_code=status.HTTP_403_FORBIDDEN
            )
            
        serializer = GroupSerializer(group, context={'request': request, 'group': group})
        return custom_response(
            success=True,
            message="Group details retrieved successfully.",
            data=serializer.data
        )

    def patch(self, request, group_id):
        group = self._get_group_for_member(request, group_id)
        if group is None:
            return custom_response(
                success=False,
                message="Access denied. You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        if not group.is_admin(request.user):
            return custom_response(
                success=False,
                message="Only group admins can update group details.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        serializer = GroupSerializer(group, data=request.data, partial=True, context={'request': request})
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation error",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return custom_response(
            success=True,
            message="Group updated successfully.",
            data=GroupSerializer(group, context={'request': request, 'group': group}).data
        )

    def put(self, request, group_id):
        return self.patch(request, group_id)

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

        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group.id)
            membership, created = GroupMembership.objects.get_or_create(
                group=group,
                user=request.user,
                defaults={'role': GroupMembership.Role.MEMBER}
            )
            if not created:
                return custom_response(
                    success=False,
                    message="You are already a member of this group.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            join_msg = f"{request.user.username} joined the group!"
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
            data=GroupSerializer(group, context={'request': request}).data
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
        """Allows group admins/owner to add a member via identifier (email or username)."""
        group = get_object_or_404(Group, id=group_id)

        # 🔒 Security: Only admins/owner can add members
        if not group.is_admin(request.user):
            return custom_response(
                success=False,
                message="Access denied. Only group admins can add members.",
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

        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group_id)
            if not group.is_admin(request.user):
                return custom_response(
                    success=False,
                    message="Access denied. Only group admins can add members.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            membership, created = GroupMembership.objects.get_or_create(
                group=group,
                user=target_user,
                defaults={'role': GroupMembership.Role.MEMBER}
            )
            if not created:
                return custom_response(
                    success=False,
                    message="That user is already a member of this group.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            add_msg = f"{request.user.username} added {target_user.username} to the group."
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
            data=GroupSerializer(group, context={'request': request}).data
        )
    
class RemoveGroupMemberView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, group_id, user_id):
        """
        Handles removing a member from a group.
        - If the requester is an Admin/Owner: They can remove anyone (kick).
        - If the requester is a Member: They can only remove themselves (leave).
        - Owner cannot be removed by anyone except themselves (by deleting group).
        """
        group = get_object_or_404(Group, id=group_id)
        user_to_remove = get_object_or_404(User, id=user_id)

        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group_id)
            if not group.members.filter(id=user_to_remove.id).exists():
                return custom_response(
                    success=False,
                    message="The specified user is not a member of this group.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            is_admin = group.is_admin(request.user)
            is_self_removing = user_to_remove == request.user
            if group.is_owner(user_to_remove):
                return custom_response(
                    success=False,
                    message="Cannot remove the group owner. Owner must delete this group to leave.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            if not (is_admin or is_self_removing):
                return custom_response(
                    success=False,
                    message="Access denied. You can only remove members if you are a group admin.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            if group_net_balances(group).get(user_to_remove.id, 0) != 0:
                return custom_response(
                    success=False,
                    message="Settle this member's group balance before removing them.",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            GroupMembership.objects.filter(group=group, user=user_to_remove).delete()
            if is_admin and not is_self_removing:
                broadcast_message = f"{request.user.username} removed {user_to_remove.username} from the group."
                success_message = f"Successfully removed {user_to_remove.username}."
            else:
                broadcast_message = f"{user_to_remove.username} has left the group."
                success_message = "You have successfully left the group."
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
            data=GroupSerializer(group, context={'request': request}).data
        )


class UpdateMemberRoleView(APIView):
    """Update a member's role (promote/demote) - only owner can do this"""
    permission_classes = [IsAuthenticated]

    def patch(self, request, group_id, user_id):
        group = get_object_or_404(Group, id=group_id)
        target_user = get_object_or_404(User, id=user_id)
        new_role = request.data.get('role')

        # Only owner can change roles
        if not group.is_owner(request.user):
            return custom_response(
                success=False,
                message="Access denied. Only the group owner can change member roles.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        # Cannot change owner's role
        if group.is_owner(target_user):
            return custom_response(
                success=False,
                message="Cannot change the owner's role.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Validate role
        valid_roles = [GroupMembership.Role.ADMIN, GroupMembership.Role.MEMBER]
        if new_role not in valid_roles:
            return custom_response(
                success=False,
                message=f"Invalid role. Must be one of: {', '.join(valid_roles)}",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Check if target user is a member
        if not group.members.filter(id=target_user.id).exists():
            return custom_response(
                success=False,
                message="User is not a member of this group.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Update role
        membership = GroupMembership.objects.get(group=group, user=target_user)
        old_role = membership.role
        membership.role = new_role
        membership.save()

        # Log the change
        role_msg = f"🔄 {request.user.username} changed {target_user.username}'s role from {old_role} to {new_role}."
        GroupMessage.objects.create(group=group, sender=None, message=role_msg)

        # Broadcast
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": role_msg,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        )

        return custom_response(
            success=True,
            message=f"Role updated to {new_role}.",
            data=GroupSerializer(group, context={'request': request}).data
        )


class TransferOwnershipView(APIView):
    """Transfer group ownership to another member - only current owner can do this"""
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        target_user = get_object_or_404(User, id=request.data.get('user_id'))

        # Only current owner can transfer ownership
        if not group.is_owner(request.user):
            return custom_response(
                success=False,
                message="Access denied. Only the group owner can transfer ownership.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        # Target must be a member
        if not group.members.filter(id=target_user.id).exists():
            return custom_response(
                success=False,
                message="Target user must be a member of this group.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # Cannot transfer to self
        if target_user == request.user:
            return custom_response(
                success=False,
                message="You are already the owner.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            # Update group creator
            group.creator = target_user
            group.save()

            # Update memberships
            old_owner_membership = GroupMembership.objects.get(group=group, user=request.user)
            old_owner_membership.role = GroupMembership.Role.ADMIN
            old_owner_membership.save()

            new_owner_membership = GroupMembership.objects.get(group=group, user=target_user)
            new_owner_membership.role = GroupMembership.Role.OWNER
            new_owner_membership.save()

        # Log the change
        transfer_msg = f"👑 {request.user.username} transferred ownership to {target_user.username}."
        GroupMessage.objects.create(group=group, sender=None, message=transfer_msg)

        # Broadcast
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": transfer_msg,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        )

        return custom_response(
            success=True,
            message=f"Ownership transferred to {target_user.username}.",
            data=GroupSerializer(group, context={'request': request}).data
        )