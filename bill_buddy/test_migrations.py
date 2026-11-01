from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AccountStatusMigrationTests(TransactionTestCase):
    migrate_from = ("bill_buddy", "0003_customuser_currency_customuser_language_and_more")
    migrate_to = ("bill_buddy", "0004_customuser_deleted_at_customuser_email_verified_at_and_more")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        User = old_apps.get_model("bill_buddy", "CustomUser")
        VerificationToken = old_apps.get_model("bill_buddy", "EmailVerificationToken")

        self.user_ids = {}
        self.user_ids["active_unknown"] = User.objects.create(
            email="migration-active-unknown@example.test",
            username="migration-active-unknown",
            password="!",
            is_active=True,
        ).pk
        self.user_ids["active_verified"] = User.objects.create(
            email="migration-active-verified@example.test",
            username="migration-active-verified",
            password="!",
            is_active=True,
        ).pk
        self.user_ids["pending"] = User.objects.create(
            email="migration-pending@example.test",
            username="migration-pending",
            password="!",
            is_active=False,
        ).pk
        self.user_ids["deactivated"] = User.objects.create(
            email="migration-deactivated@example.test",
            username="migration-deactivated",
            password="!",
            is_active=False,
        ).pk
        self.user_ids["verified_deactivated"] = User.objects.create(
            email="migration-verified-inactive@example.test",
            username="migration-verified-inactive",
            password="!",
            is_active=False,
        ).pk

        VerificationToken.objects.create(
            user_id=self.user_ids["active_verified"],
            token="migration-active-used-token",
            used=True,
        )
        VerificationToken.objects.create(
            user_id=self.user_ids["pending"],
            token="migration-pending-token",
            used=False,
        )
        VerificationToken.objects.create(
            user_id=self.user_ids["verified_deactivated"],
            token="migration-used-token",
            used=True,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_account_states_are_migrated_from_legacy_evidence(self):
        User = self.apps.get_model("bill_buddy", "CustomUser")
        active_unknown = User.objects.get(pk=self.user_ids["active_unknown"])
        active_verified = User.objects.get(pk=self.user_ids["active_verified"])
        pending = User.objects.get(pk=self.user_ids["pending"])
        deactivated = User.objects.get(pk=self.user_ids["deactivated"])
        verified_deactivated = User.objects.get(pk=self.user_ids["verified_deactivated"])

        self.assertTrue(active_unknown.is_active)
        self.assertIsNone(active_unknown.email_verified_at)
        self.assertTrue(active_verified.is_active)
        self.assertIsNotNone(active_verified.email_verified_at)
        self.assertTrue(pending.is_active)
        self.assertIsNone(pending.email_verified_at)
        self.assertFalse(deactivated.is_active)
        self.assertIsNone(deactivated.email_verified_at)
        self.assertFalse(verified_deactivated.is_active)
        self.assertIsNotNone(verified_deactivated.email_verified_at)
