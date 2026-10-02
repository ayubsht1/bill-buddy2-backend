from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from groups.models import Group, GroupMembership
from .models import Expense, ExpenseShare


User = get_user_model()


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class GroupExpenseApiTests(APITestCase):
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
        self.charlie = User.objects.create_user(
            email="charlie@example.test",
            password="StrongPass123!",
            username="charlie",
            is_active=True,
        )
        self.outsider = User.objects.create_user(
            email="outside@example.test",
            password="StrongPass123!",
            username="outside",
            is_active=True,
        )
        self.group = Group.objects.create(name="Dinner", creator=self.alice)
        for user in (self.alice, self.bob, self.charlie):
            GroupMembership.objects.create(group=self.group, user=user)
        self.client.force_authenticate(self.alice)
        self.create_url = reverse("create-expense", args=[self.group.id])

    def test_equal_split_distributes_cents_exactly_and_balances_sum_to_zero(self):
        response = self.client.post(
            self.create_url,
            {"description": "Dinner", "amount": "10.00", "split_type": "EQUAL"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        expense = Expense.objects.get()
        shares = list(expense.shares.order_by("user_id").values_list("amount", flat=True))
        self.assertEqual(shares, [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")])
        self.assertEqual(sum(shares), expense.amount)

        balances = self.client.get(reverse("group-balances", args=[self.group.id]))
        self.assertEqual(balances.status_code, 200)
        net = [Decimal(str(item["net_balance"])) for item in balances.data["data"]["balances"]]
        self.assertEqual(sum(net), Decimal("0.00"))

    def test_exact_split_validation_rejects_incorrect_total_and_outside_users(self):
        valid = self.client.post(
            self.create_url,
            {
                "description": "Dinner",
                "amount": "10.00",
                "split_type": "EXACT",
                "split_data": [
                    {"user_id": self.alice.id, "amount": "4.00"},
                    {"user_id": self.bob.id, "amount": "6.00"},
                ],
            },
            format="json",
        )
        self.assertEqual(valid.status_code, 201)
        Expense.objects.all().delete()

        mismatch = self.client.post(
            self.create_url,
            {
                "description": "Dinner",
                "amount": "10.00",
                "split_type": "EXACT",
                "split_data": [
                    {"user_id": self.alice.id, "amount": "3.00"},
                    {"user_id": self.bob.id, "amount": "3.00"},
                ],
            },
            format="json",
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(Expense.objects.count(), 0)

        outside = self.client.post(
            self.create_url,
            {
                "description": "Dinner",
                "amount": "10.00",
                "split_type": "EXACT",
                "split_data": [
                    {"user_id": self.alice.id, "amount": "5.00"},
                    {"user_id": self.outsider.id, "amount": "5.00"},
                ],
            },
            format="json",
        )
        self.assertEqual(outside.status_code, 400)
        self.assertEqual(Expense.objects.count(), 0)

    def test_percent_split_rounds_to_total_and_rejects_bad_percentages(self):
        valid = self.client.post(
            self.create_url,
            {
                "description": "Taxi",
                "amount": "10.00",
                "split_type": "PERCENT",
                "split_data": [
                    {"user_id": self.alice.id, "percentage": "33.33"},
                    {"user_id": self.bob.id, "percentage": "33.33"},
                    {"user_id": self.charlie.id, "percentage": "33.34"},
                ],
            },
            format="json",
        )
        self.assertEqual(valid.status_code, 201)
        self.assertEqual(
            sum(Expense.objects.get().shares.values_list("amount", flat=True)),
            Decimal("10.00"),
        )

        invalid = self.client.post(
            self.create_url,
            {
                "description": "Taxi",
                "amount": "10.00",
                "split_type": "PERCENT",
                "split_data": [{"user_id": self.alice.id, "percentage": "99.99"}],
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(Expense.objects.count(), 1)

    def test_expense_reads_require_membership_and_edit_delete_recalculate(self):
        response = self.client.post(
            self.create_url,
            {"description": "Groceries", "amount": "12.00", "split_type": "EQUAL"},
            format="json",
        )
        expense_id = response.data["data"]["id"]
        detail_url = reverse("expense-detail", args=[expense_id])

        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(detail_url).status_code, 403)
        self.assertEqual(self.client.get(reverse("group-balances", args=[self.group.id])).status_code, 403)

        self.client.force_authenticate(self.alice)
        updated = self.client.patch(detail_url, {"amount": "15.00"}, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            sum(ExpenseShare.objects.filter(expense_id=expense_id).values_list("amount", flat=True)),
            Decimal("15.00"),
        )
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertEqual(self.client.delete(detail_url).status_code, 200)
        self.assertFalse(ExpenseShare.objects.filter(expense_id=expense_id).exists())
