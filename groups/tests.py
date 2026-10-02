from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Group, GroupMembership


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
