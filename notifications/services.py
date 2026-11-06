import logging

from django.db import transaction

from .models import EmailDelivery, Notification, NotificationPreference

logger = logging.getLogger(__name__)


def emit_notification(
    *,
    user,
    category,
    title,
    message,
    dedupe_key,
    url='',
    email=False,
    email_subject=None,
):
    preferences, _ = NotificationPreference.objects.get_or_create(user=user)
    created_notification = None
    if preferences.allows(category, 'in_app'):
        created_notification, _ = Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                'user': user,
                'category': category,
                'title': title,
                'message': message,
                'url': url,
            },
        )

    if email and preferences.allows(category, 'email'):
        delivery, created = EmailDelivery.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                'user': user,
                'subject': email_subject or title,
                'message': message,
            },
        )
        if created:
            transaction.on_commit(
                lambda delivery_id=delivery.pk: _queue_email_delivery(delivery_id),
                robust=True,
            )
    return created_notification


def notify_group_members(
    *,
    group,
    actor,
    category,
    title,
    message,
    dedupe_key,
    url='',
    email=False,
):
    return [
        emit_notification(
            user=member,
            category=category,
            title=title,
            message=message,
            dedupe_key=f'{dedupe_key}:{member.pk}',
            url=url,
            email=email,
        )
        for member in group.members.exclude(pk=actor.pk)
    ]


def _queue_email_delivery(delivery_id):
    from .tasks import deliver_email_task

    try:
        deliver_email_task.delay(delivery_id)
    except Exception:
        logger.exception('Could not queue email delivery %s; it remains pending.', delivery_id)
