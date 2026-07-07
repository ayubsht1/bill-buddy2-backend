import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from .models import Group, GroupMessage

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
        """Receives incoming JSON message strings from a client socket."""
        try:
            data = json.loads(text_data)
            message_text = data.get('message', '').strip()
        except json.JSONDecodeError:
            return

        if not message_text:
            return

        # Save to database asynchronously 
        await self.save_message(self.group_id, self.user, message_text)

        # Broadcast the message outward to everyone attached to this group channel layer
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message', # calls the method below
                'username': self.user.username,
                'message': message_text
            }
        )

    async def chat_message(self, event):
        """Sends the broadcast event downward directly to the client's device."""
        await self.send(text_data=json.dumps({
            'username': event['username'],
            'message': event['message']
        }))

    # --- Database Helpers ---
    @database_sync_to_async
    def check_membership(self, group_id, user):
        return Group.objects.filter(id=group_id, members=user).exists()

    @database_sync_to_async
    def save_message(self, group_id, user, text):
        group = Group.objects.get(id=group_id)
        return GroupMessage.objects.create(group=group, sender=user, message=text)