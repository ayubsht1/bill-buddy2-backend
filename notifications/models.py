import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


CATEGORY_CHOICES = [
    ('groups', 'Groups'),
    ('expenses', 'Expenses'),
    ('chat', 'Chat'),
    ('settlements', 'Settlements'),
    ('security', 'Security'),
    ('reminders', 'Reminders'),
]


def default_preferences():
    return {
        category: {'in_app': True, 'email': category in {'groups', 'settlements', 'security', 'reminders'}}
        for category, _ in CATEGORY_CHOICES
    }


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
    )
    categories = models.JSONField(default=default_preferences)
    updated_at = models.DateTimeField(auto_now=True)

    def allows(self, category, channel):
        if category == 'security':
            return True
        preference = self.categories.get(category, {})
        return bool(preference.get(channel, True))


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=160)
    message = models.TextField()
    url = models.CharField(max_length=500, blank=True)
    dedupe_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['user', 'read_at', '-created_at'], name='notification_unread_idx'),
        ]

    @property
    def is_read(self):
        return self.read_at is not None


class EmailDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    dedupe_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    sending_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)


class Reminder(models.Model):
    class TargetType(models.TextChoices):
        EXPENSE = 'expense', 'Expense'
        EVENT = 'event', 'Group event'
        SETTLEMENT = 'settlement', 'Pending settlement'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        SENT = 'sent', 'Sent'
        CANCELLED = 'cancelled', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey('groups.Group', on_delete=models.CASCADE, related_name='reminders')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_reminders',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scheduled_reminders',
    )
    target_type = models.CharField(max_length=16, choices=TargetType.choices)
    expense = models.ForeignKey(
        'expense.Expense',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reminders',
    )
    event = models.ForeignKey(
        'groups.GroupEvent',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='reminders',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='settlement_reminders',
    )
    scheduled_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['scheduled_at']
        indexes = [
            models.Index(fields=['status', 'scheduled_at'], name='reminder_due_idx'),
        ]

    def target_is_active(self):
        if self.target_type == self.TargetType.EXPENSE:
            if self.expense_id is None:
                return False
            from expense.models import ExpenseShare
            from settlement.models import Settlement

            expense = self.expense
            prior_expenses = expense.group.expenses.filter(
                paid_by_id=expense.paid_by_id,
            ).filter(
                Q(date__lt=expense.date)
                | Q(date=expense.date, id__lt=expense.id)
            )
            for user_id, amount in expense.shares.exclude(
                user_id=expense.paid_by_id,
            ).values_list('user_id', 'amount'):
                amount_before = ExpenseShare.objects.filter(
                    expense__in=prior_expenses,
                    user_id=user_id,
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                amount_through = amount_before + amount
                settlements_paid = Settlement.objects.filter(
                    group=expense.group,
                    paid_by_id=user_id,
                    paid_to_id=expense.paid_by_id,
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                still_due_through = max(Decimal('0'), amount_through - settlements_paid)
                still_due_before = max(Decimal('0'), amount_before - settlements_paid)
                if still_due_through > still_due_before:
                    return True
            return False
        if self.target_type == self.TargetType.EVENT:
            return self.event_id is not None and self.event.starts_at > timezone.now()
        if self.target_user_id is None or not self.group.members.filter(pk=self.target_user_id).exists():
            return False
        from expense.utils import group_net_balances

        return group_net_balances(self.group).get(self.target_user_id, 0) < 0
