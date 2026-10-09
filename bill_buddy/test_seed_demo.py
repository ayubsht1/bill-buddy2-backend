from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from bill_buddy.models import CustomUser, Friendship
from expense.models import Expense, PersonalExpense
from groups.models import Group, GroupMessage
from settlement.models import Settlement


@override_settings(DEBUG=True)
class SeedDemoDataTests(TestCase):
    def test_seed_is_repeatable_and_exposes_demo_pages(self):
        call_command("seed_demo_data", password="Test-Demo-Password-123")
        user = CustomUser.objects.get(email="demo@example.test")

        initial_counts = (
            CustomUser.objects.count(),
            Group.objects.count(),
            PersonalExpense.objects.count(),
            Expense.objects.count(),
            GroupMessage.objects.count(),
            Settlement.objects.count(),
            Friendship.objects.count(),
        )
        call_command("seed_demo_data", password="Test-Demo-Password-123")
        repeated_counts = (
            CustomUser.objects.count(),
            Group.objects.count(),
            PersonalExpense.objects.count(),
            Expense.objects.count(),
            GroupMessage.objects.count(),
            Settlement.objects.count(),
            Friendship.objects.count(),
        )

        self.assertEqual(initial_counts, repeated_counts)
        self.assertTrue(user.check_password("Test-Demo-Password-123"))
        self.assertTrue(user.is_active)
        self.assertEqual(PersonalExpense.objects.filter(user=user).count(), 12 * 9)
        self.assertEqual(Group.objects.filter(members=user).count(), 2)
        self.assertGreaterEqual(Settlement.objects.count(), 1)

        client = APIClient()
        login = client.post(
            "/api/login/",
            {"email": user.email, "password": "Test-Demo-Password-123"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {login.data['data']['access']}"
        )
        endpoints = (
            "/api/expenses/dashboard/",
            "/api/expenses/personal/",
            "/api/groups/",
            "/api/friends/",
            "/api/friends/requests/",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(client.get(endpoint).status_code, 200)

        dashboard = client.get("/api/expenses/dashboard/").data["data"]
        self.assertTrue(dashboard["category_distribution"])
        self.assertTrue(dashboard["monthly_history"])

        trip = Group.objects.get(name="Pokhara Weekend")
        trip_endpoints = (
            f"/api/groups/{trip.id}/",
            f"/api/groups/{trip.id}/chat/",
            f"/api/expenses/group/{trip.id}/",
            f"/api/expenses/group/{trip.id}/balances/",
            f"/api/settlements/group/{trip.id}/",
        )
        for endpoint in trip_endpoints:
            with self.subTest(endpoint=endpoint):
                response = client.get(endpoint)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data["data"])
