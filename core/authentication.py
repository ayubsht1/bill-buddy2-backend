# core/authentication.py

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def get_user_from_token(token):
    try:
        validated_token = AccessToken(token)
        user_id = validated_token["user_id"]

        User = get_user_model()
        user = User.objects.get(pk=user_id)

        if not user.is_active:
            return AnonymousUser()

        return user

    except (
        TokenError,
        InvalidToken,
        get_user_model().DoesNotExist,
        KeyError,
        ValueError,
        TypeError,
    ):
        return AnonymousUser()


class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_params = parse_qs(
            scope.get("query_string", b"").decode("utf-8")
        )

        token = query_params.get("token", [None])[0]

        if token:
            scope["user"] = await get_user_from_token(token)
        else:
            scope["user"] = AnonymousUser()

        return await self.app(scope, receive, send)