from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Group, GroupMembership
from expense.models import Expense


User = get_user_model()


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class GroupApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.test",
            password="StrongPass123!",
            username="owner",
            is_active=True,
        )
        self.member = User.objects.create_user(
            email="member@example.test",
            password="StrongPass123!",
            username="member",
            is_active=True,
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.test",
            password="StrongPass123!",
            username="outsider",
            is_active=True,
        )
        self.group = Group.objects.create(name="Trip", creator=self.owner)
        GroupMembership.objects.create(
            group=self.group, user=self.owner, role=GroupMembership.Role.OWNER
        )
        GroupMembership.objects.create(
            group=self.group, user=self.member, role=GroupMembership.Role.MEMBER
        )

    def test_create_group_adds_owner_membership(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("group-list-create"), {"name": "New trip"}, format="json")

        self.assertEqual(response.status_code, 201)
        created_group = Group.objects.get(name="New trip")
        self.assertEqual(created_group.get_user_role(self.owner), GroupMembership.Role.OWNER)

    def test_group_update_requires_membership_and_admin_role(self):
        url = reverse("group-detail", args=[self.group.id])
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.patch(url, {"name": "Leaked"}, format="json").status_code, 403)

        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.patch(url, {"name": "Unauthorized"}, format="json").status_code, 403)

        self.client.force_authenticate(self.owner)
        response = self.client.patch(url, {"name": "Weekend trip"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Weekend trip")

    def test_member_can_leave_but_cannot_kick_another_member(self):
        self.client.force_authenticate(self.member)
        url = reverse("group-remove-member", args=[self.group.id, self.owner.id])
        self.assertEqual(self.client.delete(url).status_code, 400)

        leave_url = reverse("group-remove-member", args=[self.group.id, self.member.id])
        self.assertEqual(self.client.delete(leave_url).status_code, 200)
        self.assertFalse(self.group.members.filter(id=self.member.id).exists())

    def test_join_by_code_is_idempotently_rejected(self):
        self.client.force_authenticate(self.outsider)
        url = reverse("join-group")
        first = self.client.post(url, {"join_code": self.group.join_code}, format="json")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(url, {"join_code": self.group.join_code}, format="json")
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.group.memberships.filter(user=self.outsider).count(), 1)

    def test_member_can_create_event_with_estimated_costs_without_ledger_expenses(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            reverse("group-event-list", args=[self.group.id]),
            {
                "title": "Weekend trip",
                "description": "Plan the shared trip",
                "location": "Pokhara",
                "starts_at": "2027-06-01T10:00:00Z",
                "ends_at": "2027-06-01T18:00:00Z",
                "budget_items": [
                    {"description": "Transport", "amount": "120.00"},
                    {"description": "Accommodation", "amount": "200.00"},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["data"]["planned_budget"], "320.00")
        self.assertEqual(len(response.data["data"]["budget_items"]), 2)
        self.assertEqual(Expense.objects.filter(group=self.group).count(), 0)

    def test_event_requires_end_after_start(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            reverse("group-event-list", args=[self.group.id]),
            {
                "title": "Invalid event",
                "starts_at": "2027-06-01T18:00:00Z",
                "ends_at": "2027-06-01T18:00:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.group.events.exists())

    def test_event_reading_and_management_respect_group_permissions(self):
        self.client.force_authenticate(self.owner)
        created = self.client.post(
            reverse("group-event-list", args=[self.group.id]),
            {
                "title": "Dinner",
                "starts_at": "2027-06-01T18:00:00Z",
                "ends_at": "2027-06-01T20:00:00Z",
            },
            format="json",
        )
        event_id = created.data["data"]["id"]
        detail_url = reverse("group-event-detail", args=[self.group.id, event_id])

        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.patch(detail_url, {"title": "Unauthorized"}).status_code, 403)

        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(detail_url).status_code, 403)

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.patch(detail_url, {"title": "Team dinner"}).status_code, 200)
        self.assertEqual(self.client.delete(detail_url).status_code, 200)
