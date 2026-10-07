from django.contrib.auth import get_user_model
from io import BytesIO
import tempfile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase
from PIL import Image

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

    def test_group_member_can_send_image_attachment_with_optional_text(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), color="green").save(image, format="PNG")
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.client.force_authenticate(self.member)
            response = self.client.post(
                reverse("group-chat", args=[self.group.id]),
                {
                    "message": "Trip photo",
                    "attachment": SimpleUploadedFile(
                        "trip.png", image.getvalue(), content_type="image/png"
                    ),
                },
                format="multipart",
            )

            self.assertEqual(response.status_code, 201, response.data)
            data = response.data["data"]
            self.assertEqual(data["message"], "Trip photo")
            self.assertEqual(data["attachment_name"], "trip.png")
            self.assertEqual(data["attachment_type"], "image/png")
            self.assertTrue(data["attachment_url"].startswith("http://testserver/media/group_messages/"))
            message = self.group.chat_messages.get(id=data["id"])
            self.assertTrue(message.attachment.storage.exists(message.attachment.name))

            history = self.client.get(reverse("group-chat", args=[self.group.id]))
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.data["data"][0]["attachment_url"], data["attachment_url"])

            deleted = self.client.patch(
                reverse("group-chat", args=[self.group.id]),
                {"message_id": data["id"], "action": "delete"},
                format="json",
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertIsNone(deleted.data["data"]["attachment_url"])
            self.assertFalse(default_storage.exists(message.attachment.name))

    def test_chat_attachment_rejects_unsupported_and_oversized_files(self):
        self.client.force_authenticate(self.member)
        url = reverse("group-chat", args=[self.group.id])
        unsupported = self.client.post(
            url,
            {"attachment": SimpleUploadedFile("script.html", b"<script></script>")},
            format="multipart",
        )
        self.assertEqual(unsupported.status_code, 400)

        oversized = self.client.post(
            url,
            {"attachment": SimpleUploadedFile("large.pdf", b"x" * (10 * 1024 * 1024 + 1))},
            format="multipart",
        )
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(self.group.chat_messages.count(), 0)

    def test_non_member_cannot_send_chat_attachment(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            reverse("group-chat", args=[self.group.id]),
            {"attachment": SimpleUploadedFile("trip.jpg", b"image")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.group.chat_messages.count(), 0)
