# core/authentication.py

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from bill_buddy.models import UserSession


def update_active_session(validated_token, user):
    session_id = validated_token.get("session_id")
    if not session_id:
        return True

    try:
        session = UserSession.objects.filter(
            pk=session_id,
            user=user,
            status=UserSession.Status.ACTIVE,
        ).first()
    except (TypeError, ValueError):
        return False
    if session is None:
        return False
    UserSession.objects.filter(pk=session.pk).update(last_activity=timezone.now())
    return True


class EmailVerifiedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if user.email_verified_at is None:
            raise AuthenticationFailed(
                "Email address is not verified.",
                code="email_not_verified",
            )
        if not update_active_session(validated_token, user):
            raise AuthenticationFailed("This session is no longer active.", code="session_revoked")
        return user


@database_sync_to_async
def get_user_from_token(token):
    try:
        validated_token = AccessToken(token)
        user_id = validated_token["user_id"]

        User = get_user_model()
        user = User.objects.get(pk=user_id)

        if not user.is_active or user.email_verified_at is None:
            return AnonymousUser()
        if not update_active_session(validated_token, user):
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