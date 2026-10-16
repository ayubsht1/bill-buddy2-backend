from django.db import transaction
from django.urls import reverse
from django.conf import settings
from django.core.mail import send_mail
from .models import PasswordResetToken, EmailVerificationToken, Friendship
from django.core.signing import TimestampSigner
from .tasks import send_password_reset_email_task, send_verification_email_task

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Q

def send_verification_email(user, request):
    signer = TimestampSigner()
    token = signer.sign(user.email)

    # Invalidate previous tokens
    EmailVerificationToken.objects.filter(user=user).delete()

    # Save new token
    EmailVerificationToken.objects.create(user=user, token=token)

    verify_url = request.build_absolute_uri(
        reverse('email-verify') + f'?token={token}'
    )

    transaction.on_commit(
        lambda: send_verification_email_task.delay(
            user.email,
            user.first_name or '',
            verify_url,
        ),
        robust=True,
    )



def send_password_reset_email(user, request):
    signer = TimestampSigner()
    token = signer.sign(user.email)

    # Invalidate previous tokens
    PasswordResetToken.objects.filter(user=user).delete()

    # Save new token
    PasswordResetToken.objects.create(user=user, token=token)

    reset_url = f"{settings.FRONTEND_URL}/auth/reset-password?token={token}"


    transaction.on_commit(
        lambda: send_password_reset_email_task.delay(
            user.email,
            user.first_name or '',
            reset_url,
        ),
        robust=True,
    )


def send_settlement_reminder(user_email, group_name, amount_due):
    subject = f"Reminder: Settlement due in group {group_name}"
    message = f"Hi,\n\nYou have a pending settlement of {amount_due} in the group '{group_name}'. Please settle it at your earliest convenience.\n\nThanks!"
    send_mail(subject, message, None, [user_email])

def are_friends(user_a, user_b):
    return Friendship.objects.filter(
        Q(from_user=user_a, to_user=user_b) | Q(from_user=user_b, to_user=user_a),
        status=Friendship.Status.ACCEPTED,
    ).exists()


def user_group(user_id):
    return f"user_{user_id}"


def push_event(user_ids, event_type, payload):
    """Sync helper for REST views: push an event to each user's websocket inbox."""
    layer = get_channel_layer()
    for uid in set(user_ids):
        async_to_sync(layer.group_send)(
            user_group(uid),
            {'type': 'room_event', 'event_type': event_type, 'data': payload},
        )