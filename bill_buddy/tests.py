import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Friendship
from .tasks import send_password_reset_email_task, send_verification_email_task
from .utils import send_password_reset_email, send_verification_email


User = get_user_model()


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class FriendshipApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            email="alice@example.test",
            password="StrongPass123!",
            username="alice",
            is_active=True,
        )
        self.bob = User.objects.create_user(
            email="bob@example.test",
            password="StrongPass123!",
            username="bob",
            is_active=True,
        )
        self.client.force_authenticate(self.alice)

    def send_request(self, username):
        return self.client.post(reverse("send-friend-request"), {"username": username}, format="json")

    def test_search_excludes_self_and_inactive_accounts(self):
        inactive = User.objects.create_user(
            email="hidden@example.test",
            password="StrongPass123!",
            username="hidden",
            is_active=False,
        )
        response = self.client.get(reverse("user-search"), {"q": "bo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["data"]], [self.bob.id])
        self.assertNotIn(inactive.id, [item["id"] for item in response.data["data"]])

        self.client.force_authenticate(self.bob)
        bob_response = self.client.get(reverse("user-search"), {"q": "bo"})
        self.assertEqual(bob_response.status_code, 200)
        self.assertEqual(bob_response.data["data"], [])

    def test_send_prevents_self_and_duplicate_requests(self):
        self.assertEqual(self.send_request("alice").status_code, 400)
        self.assertEqual(self.send_request("bob").status_code, 201)
        self.assertEqual(self.send_request("bob").status_code, 400)
        self.assertEqual(Friendship.objects.count(), 1)

    def test_accept_requires_recipient_and_accepted_friend_is_listed(self):
        friendship = Friendship.objects.create(from_user=self.alice, to_user=self.bob)
        unauthorized = self.client.post(reverse("accept-friend-request", args=[friendship.id]))
        self.assertEqual(unauthorized.status_code, 403)

        self.client.force_authenticate(self.bob)
        accepted = self.client.post(reverse("accept-friend-request", args=[friendship.id]))
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data["data"]["status"], Friendship.Status.ACCEPTED)

        listed = self.client.get(reverse("friend-list"))
        self.assertEqual([friend["id"] for friend in listed.data["data"]], [self.alice.id])

    def test_sender_can_cancel_and_recipient_can_reject_only(self):
        friendship = Friendship.objects.create(from_user=self.alice, to_user=self.bob)
        reject_by_sender = self.client.post(reverse("reject-friend-request", args=[friendship.id]))
        self.assertEqual(reject_by_sender.status_code, 403)
        cancelled = self.client.post(reverse("cancel-friend-request", args=[friendship.id]))
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(Friendship.objects.filter(id=friendship.id).exists())

        friendship = Friendship.objects.create(from_user=self.alice, to_user=self.bob)
        self.client.force_authenticate(self.bob)
        rejected = self.client.post(reverse("reject-friend-request", args=[friendship.id]))
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(Friendship.objects.filter(id=friendship.id).exists())

    def test_friend_removal_requires_accepted_friendship(self):
        self.assertEqual(self.client.delete(reverse("remove-friend", args=[self.bob.id])).status_code, 404)
        Friendship.objects.create(
            from_user=self.alice, to_user=self.bob, status=Friendship.Status.ACCEPTED
        )
        self.assertEqual(self.client.delete(reverse("remove-friend", args=[self.bob.id])).status_code, 200)

    def test_google_login_requires_a_verified_id_token(self):
        self.client.force_authenticate(user=None)
        url = reverse("social-login")
        with override_settings(GOOGLE_CLIENT_ID="test-client"):
            email_only = self.client.post(url, {"email": "attacker@example.test"}, format="json")
            self.assertEqual(email_only.status_code, 400)
            with patch(
                "bill_buddy.views.google_id_token.verify_oauth2_token",
                return_value={
                    "email": "google@example.test",
                    "email_verified": True,
                    "given_name": "Google",
                    "family_name": "User",
                },
            ):
                verified = self.client.post(url, {"id_token": "signed-token"}, format="json")

        self.assertEqual(verified.status_code, 200)
        google_user = User.objects.get(email="google@example.test")
        self.assertTrue(google_user.is_active)
        self.assertEqual(verified.data["data"]["user"]["id"], google_user.id)

    def test_friend_endpoints_require_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse("friend-list"))
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.data["success"])
        self.assertIn("message", response.data)

    def test_refresh_rejects_tokens_for_inactive_accounts(self):
        inactive = User.objects.create_user(
            email="disabled@example.test",
            password="StrongPass123!",
            username="disabled",
            is_active=False,
        )
        token = str(RefreshToken.for_user(inactive))
        response = self.client.post(
            reverse("token-refresh"),
            {"refresh": token},
            format="json",
        )
        self.assertEqual(response.status_code, 401)


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class ProfilePictureUploadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="picture@example.test",
            password="Test-password-123!",
            username="picture-user",
            is_active=True,
        )
        self.client.force_authenticate(self.user)

    def test_login_returns_an_absolute_url_for_uploaded_profile_picture(self):
        self.user.profile_picture = "profile_pics/avatar.png"
        self.user.set_password("correct-password")
        self.user.save()
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "correct-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["data"]["user"]["profile_picture"],
            "http://testserver/media/profile_pics/avatar.png",
        )

    def test_multipart_profile_picture_is_saved_and_returned_as_a_url(self):
        image = BytesIO()
        Image.new("RGB", (1, 1)).save(image, format="PNG")
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
                response = self.client.patch(
                    reverse("user-profile"),
                    {
                        "pictureFile": SimpleUploadedFile(
                            "avatar.png", image.getvalue(), content_type="image/png"
                        )
                    },
                    format="multipart",
                )

                self.assertEqual(response.status_code, 200, response.data)
                self.user.refresh_from_db()
                self.assertTrue(self.user.profile_picture.startswith("profile_pics/"))
                self.assertTrue(default_storage.exists(self.user.profile_picture))
                self.assertTrue(
                    response.data["data"]["profilePicture"].startswith(
                        "http://testserver/media/profile_pics/"
                    )
                )


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class AccountEmailTaskTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="email@example.test",
            password="Test-password-123!",
            username="email-user",
            first_name="Test",
            is_active=False,
        )
        mail.outbox = []

    def test_verification_and_reset_emails_are_enqueued_after_token_creation(self):
        request = RequestFactory().get("/api/email-verify/")
        with patch("bill_buddy.utils.send_verification_email_task.delay") as verification_task:
            with self.captureOnCommitCallbacks(execute=True):
                send_verification_email(self.user, request)

        with patch("bill_buddy.utils.send_password_reset_email_task.delay") as reset_task:
            with self.captureOnCommitCallbacks(execute=True):
                send_password_reset_email(self.user, request)

        self.assertEqual(verification_task.call_count, 1)
        self.assertEqual(reset_task.call_count, 1)
        self.assertEqual(mail.outbox, [])

    def test_account_email_tasks_send_to_the_recipient(self):
        send_verification_email_task.run(
            self.user.email,
            self.user.first_name,
            "https://api.example.test/api/email-verify/?token=verify-token",
        )
        send_password_reset_email_task.run(
            self.user.email,
            self.user.first_name,
            "https://app.example.test/auth/reset-password?token=reset-token",
        )

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("verify-token", mail.outbox[0].body)
        self.assertIn("reset-token", mail.outbox[1].body)
