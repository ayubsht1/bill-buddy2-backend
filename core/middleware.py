# core/middleware.py
from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from core.authentication import update_active_session

User = get_user_model()

@database_sync_to_async
def get_user(token):
    try:
        validated_token = AccessToken(token)
        user = User.objects.get(id=validated_token['user_id'])
        if not user.is_active or user.email_verified_at is None:
            return AnonymousUser()
        if not update_active_session(validated_token, user):
            return AnonymousUser()
        return user
    except Exception:
        return AnonymousUser()

class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope['query_string'].decode())
        token = query.get('token', [None])[0]
        scope['user'] = await get_user(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)