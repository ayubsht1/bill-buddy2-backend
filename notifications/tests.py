from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from expense.models import Expense, ExpenseShare
from groups.models import Group, GroupEvent, GroupMembership
from settlement.models import Settlement

from .models import EmailDelivery, Notification, NotificationPreference, Reminder
from .services import emit_notification
from .tasks import deliver_email_task, deliver_reminder_task

User = get_user_model()


class NotificationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='notify@example.test',
            password='test-password',
            username='notify',
            email_verified_at=timezone.now(),
        )
        self.other_user = User.objects.create_user(
            email='member@example.test',
            password='test-password',
            username='member',
            email_verified_at=timezone.now(),
        )
        self.group = Group.objects.create(name='Trip', creator=self.user)
        GroupMembership.objects.create(group=self.group, user=self.user, role=GroupMembership.Role.OWNER)
        GroupMembership.objects.create(group=self.group, user=self.other_user)
        self.client.force_authenticate(self.user)

    def test_preferences_are_persisted_and_security_cannot_be_disabled(self):
        response = self.client.patch(
            reverse('notification-preferences'),
            {'categories': {'expenses': {'in_app': False, 'email': False}, 'security': {'in_app': False, 'email': False}}},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        categories = response.data['data']['categories']
        self.assertFalse(categories['expenses']['in_app'])
        self.assertFalse(categories['expenses']['email'])
        self.assertTrue(categories['security']['in_app'])
        self.assertTrue(categories['security']['email'])

        emit_notification(
            user=self.user,
            category='expenses',
            title='Expense',
            message='A new expense.',
            dedupe_key='test:expense:preference',
            email=True,
        )
        self.assertFalse(Notification.objects.filter(dedupe_key='test:expense:preference').exists())
        self.assertFalse(EmailDelivery.objects.filter(dedupe_key='test:expense:preference').exists())

    def test_notifications_are_user_scoped_paginated_and_markable(self):
        for index in range(23):
            Notification.objects.create(
                user=self.user,
                category='groups',
                title=f'Update {index}',
                message='Group changed.',
                dedupe_key=f'test:notification:{index}',
            )
        Notification.objects.create(
            user=self.other_user,
            category='groups',
            title='Private',
            message='Only for another user.',
            dedupe_key='test:notification:private',
        )

        first_page = self.client.get(reverse('notification-list'), {'page_size': 20})
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.data['data']['count'], 23)
        self.assertEqual(first_page.data['data']['unread_count'], 23)
        self.assertEqual(len(first_page.data['data']['results']), 20)

        notification_id = first_page.data['data']['results'][0]['id']
        marked = self.client.patch(reverse('notification-read', args=[notification_id]))
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.data['data']['is_read'])
        self.assertEqual(self.client.post(reverse('notification-read-all')).status_code, 200)
        self.assertEqual(Notification.objects.filter(user=self.user, read_at__isnull=True).count(), 0)

    def test_reminder_can_be_scheduled_edited_and_cancelled(self):
        expense = Expense.objects.create(
            group=self.group,
            description='Train tickets',
            amount='20.00',
            paid_by=self.user,
        )
        due_at = (timezone.now() + timedelta(days=1)).isoformat()
        created = self.client.post(
            reverse('reminder-list-create'),
            {
                'group_id': str(self.group.public_id),
                'target_type': 'expense',
                'expense_id': str(expense.public_id),
                'scheduled_at': due_at,
            },
            format='json',
        )

        self.assertEqual(created.status_code, 201, created.data)
        reminder_id = created.data['data']['id']
        updated = self.client.patch(
            reverse('reminder-detail', args=[reminder_id]),
            {'scheduled_at': (timezone.now() + timedelta(days=2)).isoformat()},
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['data']['status'], Reminder.Status.PENDING)

        cancelled = self.client.delete(reverse('reminder-detail', args=[reminder_id]))
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(
            Reminder.objects.get(pk=reminder_id).status,
            Reminder.Status.CANCELLED,
        )

    def test_reminder_delivery_is_persisted_once_for_group_members(self):
        event = GroupEvent.objects.create(
            group=self.group,
            created_by=self.user,
            title='Dinner',
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        reminder = Reminder.objects.create(
            group=self.group,
            created_by=self.user,
            recipient=self.user,
            target_type=Reminder.TargetType.EVENT,
            event=event,
            scheduled_at=timezone.now() - timedelta(seconds=1),
        )

        deliver_reminder_task.apply(args=[str(reminder.pk)]).get()
        deliver_reminder_task.apply(args=[str(reminder.pk)]).get()

        self.assertEqual(Reminder.objects.get(pk=reminder.pk).status, Reminder.Status.SENT)
        self.assertEqual(Notification.objects.filter(dedupe_key__startswith=f'reminder:{reminder.pk}:').count(), 2)

    def test_paid_expense_reminder_is_not_active(self):
        expense = Expense.objects.create(
            group=self.group,
            description='Train tickets',
            amount='20.00',
            paid_by=self.user,
        )
        ExpenseShare.objects.create(expense=expense, user=self.other_user, amount='20.00')
        Settlement.objects.create(
            group=self.group,
            paid_by=self.other_user,
            paid_to=self.user,
            amount='20.00',
        )
        reminder = Reminder.objects.create(
            group=self.group,
            created_by=self.user,
            recipient=self.user,
            target_type=Reminder.TargetType.EXPENSE,
            expense=expense,
            scheduled_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertFalse(reminder.target_is_active())

    def test_expense_and_group_chat_events_create_in_app_notifications(self):
        created = self.client.post(
            reverse('create-expense', args=[self.group.public_id]),
            {'description': 'Lunch', 'amount': '20.00', 'split_type': 'EQUAL'},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        expense_notification = Notification.objects.filter(
            user=self.other_user,
            category='expenses',
        ).get()
        self.assertIn('Lunch', expense_notification.message)
        self.assertFalse(Notification.objects.filter(user=self.user, category='expenses').exists())

        chat = self.client.post(
            reverse('group-chat', args=[self.group.public_id]),
            {'message': 'Hello @member'},
            format='json',
        )
        self.assertEqual(chat.status_code, 201, chat.data)
        chat_notification = Notification.objects.filter(
            user=self.other_user,
            category='chat',
        ).get()
        self.assertIn('Hello @member', chat_notification.message)

    def test_adding_a_member_creates_an_invitation_notification(self):
        new_user = User.objects.create_user(
            email='invited@example.test',
            password='test-password',
            username='invited',
            email_verified_at=timezone.now(),
        )

        response = self.client.post(
            reverse('group-add-member', args=[self.group.public_id]),
            {'identifier': new_user.username},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        invitation = Notification.objects.get(user=new_user, category='groups')
        self.assertIn(self.group.name, invitation.title)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_email_delivery_task_does_not_send_an_already_sent_email_twice(self):
        delivery = EmailDelivery.objects.create(
            user=self.user,
            subject='Important update',
            message='Please review this update.',
            dedupe_key='test:email:dedupe',
        )

        deliver_email_task.apply(args=[delivery.pk]).get()
        deliver_email_task.apply(args=[delivery.pk]).get()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailDelivery.objects.get(pk=delivery.pk).status, EmailDelivery.Status.SENT)
