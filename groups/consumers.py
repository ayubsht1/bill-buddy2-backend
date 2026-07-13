import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from .models import Group, GroupMessage
from .serializers import GroupMessageSerializer  # Import your serializer here!

class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_id = self.scope['url_route']['kwargs']['group_id']
        self.room_group_name = f"chat_{self.group_id}"
        self.user = self.scope.get("user", AnonymousUser())

        # 1. Block unauthorized connections immediately
        if self.user.is_anonymous:
            await self.close(code=4401)
            return

        # 2. Check group membership
        is_member = await self.check_membership(self.group_id, self.user)
        if not is_member:
            await self.close(code=4403)
            return

        # 3. Join the broadcast room group layer
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group layer safely
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        """Receives structured commands from a client socket."""
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        action = data.get('action', 'send_message')  # Default fallback

        # --- ACTION 1: SEND OR REPLY OR FORWARD ---
        if action == 'send_message':
            message_text = data.get('message', '').strip()
            reply_to_id = data.get('reply_to_id')
            forward_message_id = data.get('forward_message_id')

            # Process forward text if provided
            if forward_message_id:
                forwarded_text = await self.get_forward_text(forward_message_id, self.user)
                if not forwarded_text:
                    return  # Access denied or message missing
                message_text = forwarded_text
                is_forwarded = True
            else:
                is_forwarded = False

            if not message_text and not forward_message_id:
                return

            # Save structural entry
            serialized_data = await self.save_message(
                self.group_id, self.user, message_text, reply_to_id, is_forwarded
            )
            
            # Broadcast out
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'room_event',
                    'event_type': 'chat_message',
                    'data': serialized_data
                }
            )

        # --- ACTION 2: PIN / UNPIN ---
        elif action in ['pin', 'unpin']:
            message_id = data.get('message_id')
            if not message_id:
                return
            
            is_pinned = (action == 'pin')
            serialized_data = await self.toggle_pin_message(message_id, self.group_id, is_pinned)
            
            if serialized_data:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'room_event',
                        'event_type': 'message_update',
                        'data': serialized_data
                    }
                )

        # --- ACTION 3: DELETE ---
        elif action == 'delete':
            message_id = data.get('message_id')
            if not message_id:
                return
            
            serialized_data = await self.delete_message(message_id, self.group_id, self.user)
            if serialized_data:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'room_event',
                        'event_type': 'message_update',
                        'data': serialized_data
                    }
                )

    async def room_event(self, event):
        """Unified transmitter for all structured room data events down to client devices."""
        await self.send(text_data=json.dumps({
            'event_type': event['event_type'],
            'payload': event['data']
        }))


    # --- 🛠️ Async Database Layer Helpers ---

    @database_sync_to_async
    def check_membership(self, group_id, user):
        return Group.objects.filter(id=group_id, members=user).exists()

    @database_sync_to_async
    def get_forward_text(self, message_id, user):
        try:
            msg = GroupMessage.objects.get(id=message_id)
            # Make sure user has access to the group the original message belongs to
            if msg.group.members.filter(id=user.id).exists():
                return msg.message
        except GroupMessage.DoesNotExist:
            return None
        return None

    @database_sync_to_async
    def save_message(self, group_id, user, text, reply_to_id, is_forwarded):
        group = Group.objects.get(id=group_id)
        reply_to_obj = None
        if reply_to_id:
            reply_to_obj = GroupMessage.objects.filter(id=reply_to_id, group=group).first()

        # Modified to handle systems / deleted users gracefully if user is passed as None
        msg = GroupMessage.objects.create(
            group=group, 
            sender=user if user and user.is_authenticated else None, 
            message=text, 
            reply_to=reply_to_obj,
            is_forwarded=is_forwarded
        )
        return GroupMessageSerializer(msg).data

    @database_sync_to_async
    def toggle_pin_message(self, message_id, group_id, should_pin):
        msg = GroupMessage.objects.filter(id=message_id, group_id=group_id).first()
        if msg:
            msg.is_pinned = should_pin
            msg.save()
            return GroupMessageSerializer(msg).data
        return None

    @database_sync_to_async
    def delete_message(self, message_id, group_id, user):
        msg = GroupMessage.objects.filter(id=message_id, group_id=group_id).first()
        if msg:
            # Authorization check: only the sender or group owner/creator can delete
            if msg.sender == user or msg.group.creator == user:
                msg.is_deleted = True
                msg.save()
                return GroupMessageSerializer(msg).data
        return None