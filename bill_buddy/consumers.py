# consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from .models import PersonalMessage
from .serializers import PersonalMessageSerializer
from .utils import are_friends, user_group


User = get_user_model()


def inbox_group(user_id):
    return f"user_{user_id}"


class PersonalChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user", AnonymousUser())

        if self.user.is_anonymous:
            await self.close(code=4401)
            return

        self.inbox = inbox_group(self.user.id)
        await self.channel_layer.group_add(self.inbox, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'inbox'):
            await self.channel_layer.group_discard(self.inbox, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        action = data.get('action', 'send_message')

        # --- SEND / REPLY ---
        if action == 'send_message':
            receiver_id = data.get('receiver_id')
            message_text = (data.get('message') or '').strip()
            if not receiver_id or not message_text:
                return

            serialized = await self.save_message(
                self.user, receiver_id, message_text,
                data.get('reply_to_id'), data.get('message_type'),
            )
            if serialized:
                await self.broadcast(
                    [self.user.id, int(receiver_id)], 'chat_message', serialized
                )

        # --- MARK CONVERSATION AS READ ---
        elif action == 'mark_read':
            friend_id = data.get('friend_id')
            if not friend_id:
                return
            count = await self.mark_conversation_read(self.user, friend_id)
            if count:
                # tell the friend their messages were seen
                await self.broadcast(
                    [int(friend_id)], 'messages_read',
                    {'reader_id': self.user.id, 'count': count},
                )

        # --- DELETE ---
        elif action == 'delete':
            message_id = data.get('message_id')
            if not message_id:
                return
            result = await self.delete_message(message_id, self.user)
            if result:
                serialized, receiver_id = result
                await self.broadcast(
                    [self.user.id, receiver_id], 'message_update', serialized
                )

        # --- TYPING INDICATOR (not saved to DB) ---
        elif action == 'typing':
            receiver_id = data.get('receiver_id')
            if receiver_id:
                await self.broadcast(
                    [int(receiver_id)], 'typing',
                    {'user_id': self.user.id, 'is_typing': bool(data.get('is_typing', True))},
                )

    async def broadcast(self, user_ids, event_type, payload):
        for uid in set(user_ids):  # set() avoids duplicates if sender == receiver
            await self.channel_layer.group_send(
                inbox_group(uid),
                {'type': 'room_event', 'event_type': event_type, 'data': payload},
            )

    async def room_event(self, event):
        await self.send(text_data=json.dumps({
            'event_type': event['event_type'],
            'payload': event['data'],
        }))

    # --- DB helpers ---

    @database_sync_to_async
    def save_message(self, user, receiver_id, text, reply_to_id, message_type):
        receiver = User.objects.filter(id=receiver_id).first()
        if not receiver or not are_friends(user, receiver):
            return None

        # TODO: verify they are actually friends (use your friendship model here)
        # if not Friendship.objects.are_friends(user, receiver): return None

        reply_to_obj = None
        if reply_to_id:
            # only allow replying to a message within THIS conversation
            reply_to_obj = PersonalMessage.objects.filter(id=reply_to_id).filter(
                Q(sender=user, receiver=receiver) | Q(sender=receiver, receiver=user)
            ).first()

        msg_type = (
            PersonalMessage.MessageType.EMOTICON
            if message_type == PersonalMessage.MessageType.EMOTICON
            else PersonalMessage.MessageType.TEXT
        )

        msg = PersonalMessage.objects.create(
            sender=user, receiver=receiver, message=text,
            reply_to=reply_to_obj, message_type=msg_type,
        )
        return PersonalMessageSerializer(msg).data

    @database_sync_to_async
    def mark_conversation_read(self, user, friend_id):
        friend = User.objects.filter(id=friend_id).first()
        if not friend or not are_friends(user, friend):
            return 0
        return PersonalMessage.objects.filter(
            sender=friend, receiver=user, is_read=False
        ).update(is_read=True)

    @database_sync_to_async
    def delete_message(self, message_id, user):
        msg = PersonalMessage.objects.filter(id=message_id, sender=user).first()
        if not msg:
            return None  # only the sender can delete
        msg.is_deleted = True
        msg.save(update_fields=['is_deleted'])
        return PersonalMessageSerializer(msg).data, msg.receiver_id

    @database_sync_to_async
    def can_message(self, user, other_id):
        other = User.objects.filter(id=other_id).first()
        return bool(other and are_friends(user, other))