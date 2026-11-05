import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.signing import TimestampSigner
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone
from asgiref.sync import async_to_sync
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .models import EmailVerificationToken, Friendship, PasswordResetToken, UserSession, PersonalMessage
from .tasks import send_password_reset_email_task, send_verification_email_task
from .utils import send_password_reset_email, send_verification_email
from expense.models import PersonalExpense
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from core.authentication import get_user_from_token


User = get_user_model()


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class FriendshipApiTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(
            email="alice@example.test",
            password="StrongPass123!",
            username="alice",
            is_active=True,
            email_verified_at=timezone.now(),
        )
        self.bob = User.objects.create_user(
            email="bob@example.test",
            password="StrongPass123!",
            username="bob",
            is_active=True,
            email_verified_at=timezone.now(),
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
        unauthorized = self.client.post(reverse("accept-friend-request", args=[friendship.public_id]))
        self.assertEqual(unauthorized.status_code, 403)

        self.client.force_authenticate(self.bob)
        accepted = self.client.post(reverse("accept-friend-request", args=[friendship.public_id]))
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data["data"]["status"], Friendship.Status.ACCEPTED)

        listed = self.client.get(reverse("friend-list"))
        self.assertEqual([friend["id"] for friend in listed.data["data"]], [self.alice.id])

    @override_settings(MEDIA_URL="/media/")
    def test_friend_endpoints_return_absolute_profile_picture_urls(self):
        self.alice.profile_picture = "profile_pics/alice.png"
        self.alice.save(update_fields=["profile_picture"])
        self.bob.profile_picture = "profile_pics/bob.png"
        self.bob.save(update_fields=["profile_picture"])

        search = self.client.get(reverse("user-search"), {"q": "bob"})
        self.assertEqual(
            search.data["data"][0]["profile_picture"],
            "http://testserver/media/profile_pics/bob.png",
        )

        pending = self.send_request("bob")
        self.assertEqual(
            pending.data["data"]["to_user"]["profile_picture"],
            "http://testserver/media/profile_pics/bob.png",
        )
        requests = self.client.get(reverse("friend-requests"))
        self.assertEqual(
            requests.data["data"]["sent"][0]["to_user"]["profile_picture"],
            "http://testserver/media/profile_pics/bob.png",
        )

        Friendship.objects.filter(public_id=pending.data["data"]["id"]).update(
            status=Friendship.Status.ACCEPTED
        )
        listed = self.client.get(reverse("friend-list"))
        self.assertEqual(
            listed.data["data"][0]["profile_picture"],
            "http://testserver/media/profile_pics/bob.png",
        )

        detail = self.client.get(reverse("friend-detail", args=[self.bob.public_id]))
        self.assertEqual(
            detail.data["data"]["profile_picture"],
            "http://testserver/media/profile_pics/bob.png",
        )

    @override_settings(MEDIA_URL="/media/")
    def test_direct_message_payload_includes_names_public_ids_and_profile_pictures(self):
        self.alice.first_name = "Alice"
        self.alice.last_name = "Friend"
        self.alice.profile_picture = "profile_pics/alice.png"
        self.alice.save(update_fields=["first_name", "last_name", "profile_picture"])
        self.bob.first_name = "Bob"
        self.bob.last_name = "Buddy"
        self.bob.profile_picture = "profile_pics/bob.png"
        self.bob.save(update_fields=["first_name", "last_name", "profile_picture"])
        Friendship.objects.create(
            from_user=self.alice,
            to_user=self.bob,
            status=Friendship.Status.ACCEPTED,
        )
        PersonalMessage.objects.create(
            sender=self.bob,
            receiver=self.alice,
            message="Hello",
        )

        response = self.client.get(
            reverse("personal-messages"),
            {"friend_id": str(self.bob.public_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        message = response.data["data"][0]
        self.assertEqual(message["sender_name"], "Bob Buddy")
        self.assertEqual(message["sender_public_id"], str(self.bob.public_id))
        self.assertEqual(
            message["sender_profile_picture"],
            "http://testserver/media/profile_pics/bob.png",
        )
        self.assertEqual(
            message["receiver_profile_picture"],
            "http://testserver/media/profile_pics/alice.png",
        )

    def test_sender_can_cancel_and_recipient_can_reject_only(self):
        friendship = Friendship.objects.create(from_user=self.alice, to_user=self.bob)
        reject_by_sender = self.client.post(reverse("reject-friend-request", args=[friendship.public_id]))
        self.assertEqual(reject_by_sender.status_code, 403)
        cancelled = self.client.post(reverse("cancel-friend-request", args=[friendship.public_id]))
        self.assertEqual(cancelled.status_code, 200)
        self.assertFalse(Friendship.objects.filter(id=friendship.id).exists())

        friendship = Friendship.objects.create(from_user=self.alice, to_user=self.bob)
        self.client.force_authenticate(self.bob)
        rejected = self.client.post(reverse("reject-friend-request", args=[friendship.public_id]))
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(Friendship.objects.filter(id=friendship.id).exists())

    def test_friend_removal_requires_accepted_friendship(self):
        self.assertEqual(self.client.delete(reverse("remove-friend", args=[self.bob.public_id])).status_code, 404)
        Friendship.objects.create(
            from_user=self.alice, to_user=self.bob, status=Friendship.Status.ACCEPTED
        )
        self.assertEqual(self.client.delete(reverse("remove-friend", args=[self.bob.public_id])).status_code, 200)

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
        self.assertEqual(UserSession.objects.filter(user=google_user).count(), 1)
        self.assertIn("session_id", AccessToken(verified.data["data"]["access"]))

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


class SessionManagementApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="sessions@example.test",
            password="SecurePassphrase!472",
            username="sessions-user",
            is_active=True,
            email_verified_at=timezone.now(),
        )

    def login(self, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0"):
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "SecurePassphrase!472"},
            format="json",
            HTTP_X_SESSION_USER_AGENT=user_agent,
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["data"]

    def authenticate_with(self, access_token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

    def test_login_creates_session_and_active_session_lists_device_metadata(self):
        tokens = self.login()
        self.authenticate_with(tokens["access"])

        response = self.client.get(reverse("active-sessions"))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["data"]), 1)
        session = response.data["data"][0]
        self.assertTrue(session["is_current"])
        self.assertEqual(session["device"], "Desktop")
        self.assertEqual(session["browser"], "Chrome 120.0")
        self.assertEqual(session["operating_system"], "Windows")
        self.assertEqual(session["ip_address"], "127.0.0.1")
        record = UserSession.objects.get(pk=AccessToken(tokens["access"])["session_id"])
        self.assertEqual(record.status, UserSession.Status.ACTIVE)

    def test_revoking_other_sessions_blocks_access_and_refresh(self):
        current = self.login()
        other = self.login("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari/17")
        self.authenticate_with(current["access"])

        revoked = self.client.post(reverse("revoke-other-sessions"), {}, format="json")
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(revoked.data["data"]["revoked_count"], 1)

        active = self.client.get(reverse("active-sessions"))
        self.assertEqual(len(active.data["data"]), 1)
        self.assertTrue(active.data["data"][0]["is_current"])

        self.authenticate_with(other["access"])
        self.assertEqual(self.client.get(reverse("user-profile")).status_code, 401)
        self.assertEqual(
            self.client.post(
                reverse("token-refresh"), {"refresh": other["refresh"]}, format="json"
            ).status_code,
            401,
        )
        history = self.client.get(reverse("session-history"))
        self.assertEqual(history.status_code, 401)

        self.authenticate_with(current["access"])
        history = self.client.get(reverse("session-history"))
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data["data"][0]["status"], UserSession.Status.REVOKED)

    def test_individual_revoke_and_logout_are_recorded(self):
        tokens = self.login()
        record = UserSession.objects.get(pk=AccessToken(tokens["access"])["session_id"])
        self.authenticate_with(tokens["access"])

        logout = self.client.post(
            reverse("logout"),
            {"refresh": tokens["refresh"]},
            format="json",
        )

        self.assertEqual(logout.status_code, 200, logout.data)
        record.refresh_from_db()
        self.assertEqual(record.status, UserSession.Status.LOGGED_OUT)
        self.assertIsNotNone(record.logout_at)
        self.assertTrue(
            UserSession.objects.filter(pk=record.pk, status=UserSession.Status.LOGGED_OUT).exists()
        )

    def test_individual_revocation_blocks_the_revoked_access_token(self):
        current = self.login()
        other = self.login("Mozilla/5.0 (Android 14; Mobile) Chrome/120.0")
        other_session_id = AccessToken(other["access"])["session_id"]
        self.authenticate_with(current["access"])

        response = self.client.post(
            reverse("revoke-session", args=[other_session_id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.authenticate_with(other["access"])
        self.assertEqual(self.client.get(reverse("user-profile")).status_code, 401)


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class ProfilePictureUploadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="picture@example.test",
            password="Test-password-123!",
            username="picture-user",
            is_active=True,
            email_verified_at=timezone.now(),
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


class UserPreferenceApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="preferences@example.test",
            password="SecurePass123!",
            username="preferences-user",
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            email="other-preferences@example.test",
            password="SecurePass123!",
            username="other-preferences-user",
            is_active=True,
        )
        self.client.force_authenticate(self.user)

    def test_profile_returns_defaults_and_persists_preference_updates(self):
        profile_url = reverse("user-profile")
        initial = self.client.get(profile_url)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.data["data"]["currency"], "NPR")
        self.assertEqual(initial.data["data"]["language"], "en")
        self.assertEqual(initial.data["data"]["timezone"], "Asia/Kathmandu")

        updated = self.client.patch(
            profile_url,
            {"currency": "USD", "language": "ne", "timezone": "America/New_York"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["data"]["currency"], "USD")
        self.assertEqual(updated.data["data"]["language"], "ne")
        self.assertEqual(updated.data["data"]["timezone"], "America/New_York")

        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertEqual(
            (self.user.currency, self.user.language, self.user.timezone),
            ("USD", "ne", "America/New_York"),
        )
        self.assertEqual(
            (self.other_user.currency, self.other_user.language, self.other_user.timezone),
            ("NPR", "en", "Asia/Kathmandu"),
        )

    def test_profile_rejects_invalid_currency_language_and_timezone(self):
        profile_url = reverse("user-profile")
        for field, value in (
            ("currency", "XXX"),
            ("language", "fr"),
            ("timezone", "Mars/Olympus"),
        ):
            with self.subTest(field=field):
                response = self.client.patch(profile_url, {field: value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data["errors"])

        self.user.refresh_from_db()
        self.assertEqual(
            (self.user.currency, self.user.language, self.user.timezone),
            ("NPR", "en", "Asia/Kathmandu"),
        )

    def test_profile_preferences_require_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.patch(
            reverse("user-profile"), {"currency": "USD"}, format="json"
        )
        self.assertEqual(response.status_code, 401)


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class AccountLifecycleApiTests(APITestCase):
    def create_user(self, email="lifecycle@example.test", **fields):
        return User.objects.create_user(
            email=email,
            password="SecurePassphrase!472",
            username=email.split("@")[0],
            **fields,
        )

    def test_registration_creates_active_unverified_account(self):
        response = self.client.post(
            reverse("register"),
            {
                "email": "new-account@example.test",
                "username": "new-account",
                "firstName": "New",
                "lastName": "Account",
                "password": "SecurePassphrase!472",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email="new-account@example.test")
        self.assertTrue(user.is_active)
        self.assertIsNone(user.email_verified_at)
        self.assertTrue(EmailVerificationToken.objects.filter(user=user, used=False).exists())

    def test_email_verification_records_timestamp_without_changing_activation(self):
        user = self.create_user(is_active=True)
        token = TimestampSigner().sign(user.email)
        verification_token = EmailVerificationToken.objects.create(user=user, token=token)

        response = self.client.get(reverse("email-verify"), {"token": token})

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        verification_token.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(verification_token.used)

    def test_login_requires_both_active_account_and_verified_email(self):
        unverified = self.create_user(email="unverified@example.test", is_active=True)
        verified = self.create_user(
            email="verified@example.test",
            is_active=True,
            email_verified_at=timezone.now(),
        )
        inactive = self.create_user(
            email="inactive@example.test",
            is_active=False,
            email_verified_at=timezone.now(),
        )
        login_url = reverse("login")

        unverified_response = self.client.post(
            login_url,
            {"email": unverified.email, "password": "SecurePassphrase!472"},
            format="json",
        )
        self.assertEqual(unverified_response.status_code, 401)
        self.assertFalse(unverified_response.data["data"]["email_verified"])

        verified_response = self.client.post(
            login_url,
            {"email": verified.email, "password": "SecurePassphrase!472"},
            format="json",
        )
        self.assertEqual(verified_response.status_code, 200)

        inactive_response = self.client.post(
            login_url,
            {"email": inactive.email, "password": "SecurePassphrase!472"},
            format="json",
        )
        self.assertEqual(inactive_response.status_code, 401)

    def test_google_login_does_not_reactivate_deactivated_accounts(self):
        user = self.create_user(email="disabled-google@example.test", is_active=False)
        with override_settings(GOOGLE_CLIENT_ID="test-client"):
            with patch(
                "bill_buddy.views.google_id_token.verify_oauth2_token",
                return_value={
                    "email": user.email,
                    "email_verified": True,
                    "given_name": "Disabled",
                    "family_name": "Account",
                },
            ):
                response = self.client.post(
                    reverse("social-login"), {"id_token": "signed-token"}, format="json"
                )

        self.assertEqual(response.status_code, 401)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.email_verified_at)

    def test_unverified_tokens_cannot_access_api_or_websocket_authentication(self):
        user = self.create_user(is_active=True)
        refresh = RefreshToken.for_user(user)
        access = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        self.assertEqual(self.client.get(reverse("user-profile")).status_code, 401)
        self.assertTrue(async_to_sync(get_user_from_token)(access).is_anonymous)
        self.assertEqual(
            self.client.post(
                reverse("token-refresh"), {"refresh": str(refresh)}, format="json"
            ).status_code,
            401,
        )

    def test_deletion_disables_account_revokes_tokens_and_preserves_history(self):
        user = self.create_user(
            is_active=True,
            email_verified_at=timezone.now(),
        )
        expense = PersonalExpense.objects.create(
            user=user,
            transaction_type="INCOME",
            description="Historical income",
            amount="120.00",
            category="SALARY",
        )
        reset_token = PasswordResetToken.objects.create(user=user, token="reset-lifecycle-token")
        verification = EmailVerificationToken.objects.create(
            user=user, token="verification-lifecycle-token"
        )
        refresh = RefreshToken.for_user(user)
        access = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.delete(
            reverse("account-delete"),
            {"confirmation": "DELETE", "password": "SecurePassphrase!472"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.deleted_at)
        self.assertTrue(PersonalExpense.objects.filter(pk=expense.pk).exists())
        self.assertFalse(PasswordResetToken.objects.filter(pk=reset_token.pk).exists())
        self.assertFalse(EmailVerificationToken.objects.filter(pk=verification.pk).exists())
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())
        self.assertTrue(async_to_sync(get_user_from_token)(access).is_anonymous)
        self.assertEqual(self.client.get(reverse("user-profile")).status_code, 401)
        self.assertEqual(
            self.client.post(
                reverse("token-refresh"), {"refresh": str(refresh)}, format="json"
            ).status_code,
            401,
        )

    def test_deletion_requires_explicit_confirmation_and_password(self):
        user = self.create_user(is_active=True, email_verified_at=timezone.now())
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        missing_confirmation = self.client.delete(
            reverse("account-delete"),
            {"password": "SecurePassphrase!472"},
            format="json",
        )
        wrong_password = self.client.delete(
            reverse("account-delete"),
            {"confirmation": "DELETE", "password": "wrong"},
            format="json",
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertEqual(wrong_password.status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
