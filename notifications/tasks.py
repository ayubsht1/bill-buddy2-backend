import logging
import smtplib
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import EmailDelivery, Notification, Reminder

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(OSError, smtplib.SMTPException),
    retry_backoff=True,
    retry_kwargs={'max_retries': 5},
)
def deliver_email_task(delivery_id):
    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update().select_related('user').get(pk=delivery_id)
        if delivery.status != EmailDelivery.Status.PENDING:
            return
        delivery.status = EmailDelivery.Status.SENDING
        delivery.sending_at = timezone.now()
        delivery.save(update_fields=['status', 'sending_at'])
        recipient = delivery.user.email
        subject = delivery.subject
        message = delivery.message
    try:
        sent_count = send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            fail_silently=False,
        )
        if sent_count != 1:
            raise smtplib.SMTPException(f'Email delivery {delivery_id} was not accepted.')
    except (OSError, smtplib.SMTPException) as exc:
        EmailDelivery.objects.filter(pk=delivery_id, status=EmailDelivery.Status.SENDING).update(
            status=EmailDelivery.Status.PENDING,
            last_error=str(exc),
        )
        raise
    EmailDelivery.objects.filter(
        pk=delivery_id,
        status=EmailDelivery.Status.SENDING,
    ).update(status=EmailDelivery.Status.SENT, sent_at=timezone.now(), last_error='')


@shared_task
def deliver_pending_reminders():
    now = timezone.now()
    for reminder_id in Reminder.objects.filter(
        status=Reminder.Status.PENDING,
        scheduled_at__lte=now,
    ).values_list('id', flat=True)[:500]:
        deliver_reminder_task.delay(str(reminder_id))

    stale_sending = EmailDelivery.objects.filter(
        status=EmailDelivery.Status.SENDING,
        sending_at__lte=now - timedelta(minutes=10),
    )
    stale_sending.update(status=EmailDelivery.Status.PENDING)
    for delivery_id in EmailDelivery.objects.filter(
        status=EmailDelivery.Status.PENDING,
        created_at__lte=now - timedelta(minutes=2),
    ).values_list('id', flat=True)[:500]:
        deliver_email_task.delay(delivery_id)


@shared_task
def deliver_reminder_task(reminder_id):
    with transaction.atomic():
        now = timezone.now()
        reminder = (
            Reminder.objects.select_for_update()
            .select_related('group', 'recipient', 'expense', 'event', 'target_user')
            .filter(pk=reminder_id, status=Reminder.Status.PENDING)
            .first()
        )
        if reminder is None:
            return
        claimed = Reminder.objects.filter(
            pk=reminder_id,
            status=Reminder.Status.PENDING,
        ).update(status=Reminder.Status.PROCESSING)
        if not claimed:
            return
        reminder.status = Reminder.Status.PROCESSING
        if not reminder.target_is_active() or not reminder.group.members.filter(pk=reminder.recipient_id).exists():
            reminder.status = Reminder.Status.CANCELLED
            reminder.save(update_fields=['status'])
            return

        if reminder.target_type == Reminder.TargetType.EXPENSE:
            subject = f'Reminder: {reminder.expense.description}'
            text = f'Reminder for "{reminder.expense.description}" in {reminder.group.name}.'
            url = f'/groups/{reminder.group.public_id}'
        elif reminder.target_type == Reminder.TargetType.EVENT:
            subject = f'Upcoming event: {reminder.event.title}'
            text = f'"{reminder.event.title}" in {reminder.group.name} is coming up.'
            url = f'/groups/{reminder.group.public_id}/events'
        else:
            subject = f'Pending settlement in {reminder.group.name}'
            text = f'Please review your pending settlement in {reminder.group.name}.'
            url = '/settlements'

        from .services import emit_notification

        recipients = (
            [reminder.target_user]
            if reminder.target_type == Reminder.TargetType.SETTLEMENT
            else list(reminder.group.members.all())
        )
        for recipient in recipients:
            emit_notification(
                user=recipient,
                category='reminders',
                title=subject,
                message=text,
                url=url,
                dedupe_key=f'reminder:{reminder.id}:{recipient.pk}',
                email=True,
            )
        reminder.status = Reminder.Status.SENT
        reminder.sent_at = now
        reminder.save(update_fields=['status', 'sent_at'])
