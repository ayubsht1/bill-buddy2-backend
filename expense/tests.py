from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from groups.models import Group, GroupMembership
from .models import Expense, ExpenseShare


User = get_user_model()


class PersonalExpenseApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="tracker@example.test",
            password="test-password",
            username="tracker",
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            email="other-tracker@example.test",
            password="test-password",
            username="other-tracker",
            is_active=True,
        )
        self.client.force_authenticate(self.user)
        self.list_url = reverse("personal-expense-list-create")

    def create_transaction(self, **overrides):
        payload = {
            "transaction_type": "EXPENSE",
            "description": "Market",
            "amount": "25.50",
            "category": "FOOD",
            "date": timezone.localdate().isoformat(),
        }
        payload.update(overrides)
        return self.client.post(self.list_url, payload, format="json")

    def test_create_income_accepts_lowercase_type_and_payment_method(self):
        response = self.create_transaction(
            transaction_type="income",
            description="Monthly salary",
            amount="2500.00",
            category="SALARY",
            payment_method="BANK_TRANSFER",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["data"]["transaction_type"], "INCOME")
        self.assertEqual(response.data["data"]["payment_method"], "BANK_TRANSFER")

    def test_income_and_expense_categories_are_validated(self):
        invalid_income = self.create_transaction(transaction_type="INCOME", category="FOOD")
        self.assertEqual(invalid_income.status_code, 400)

        invalid_expense = self.create_transaction(transaction_type="EXPENSE", category="SALARY")
        self.assertEqual(invalid_expense.status_code, 400)

    def test_filters_cover_type_category_date_range_and_search(self):
        today = timezone.localdate()
        self.create_transaction(
            transaction_type="INCOME",
            description="Consulting",
            amount="100.00",
            category="FREELANCE",
            date=today.isoformat(),
        )
        self.create_transaction(
            description="Market groceries",
            date=today.isoformat(),
        )
        self.create_transaction(
            description="Older market",
            date=(today - timedelta(days=10)).isoformat(),
        )

        response = self.client.get(
            self.list_url,
            {
                "type": "income",
                "category": "freelance",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
                "search": "consult",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["description"] for item in response.data["data"]], ["Consulting"])

    def test_invalid_date_range_is_rejected(self):
        today = timezone.localdate()
        response = self.client.get(
            self.list_url,
            {
                "start_date": today.isoformat(),
                "end_date": (today - timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_date_filter_is_rejected(self):
        response = self.client.get(self.list_url, {"start_date": "2026-02-31"})
        self.assertEqual(response.status_code, 400)

    def test_edit_delete_and_reads_remain_user_scoped(self):
        created = self.create_transaction()
        transaction_id = created.data["data"]["id"]
        detail_url = reverse("personal-expense-detail", args=[transaction_id])
        self.assertEqual(self.client.get(detail_url).status_code, 200)

        updated = self.client.patch(
            detail_url,
            {
                "transaction_type": "INCOME",
                "category": "SALARY",
                "payment_method": "CARD",
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["data"]["transaction_type"], "INCOME")
        self.assertEqual(updated.data["data"]["payment_method"], "CARD")

        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get(self.list_url).data["data"], [])
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertEqual(self.client.delete(detail_url).status_code, 404)

        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(detail_url).status_code, 200)

    def test_dashboard_returns_personal_cash_flow_and_savings(self):
        today = timezone.localdate().isoformat()
        self.create_transaction(
            transaction_type="INCOME",
            description="Salary",
            amount="1000.00",
            category="SALARY",
            date=today,
        )
        self.create_transaction(
            description="Groceries",
            amount="250.00",
            category="FOOD",
            date=today,
        )

        response = self.client.get(reverse("dashboard-analytics"))

        self.assertEqual(response.status_code, 200)
        summary = response.data["data"]["summary"]
        self.assertEqual(summary["total_personal_income_this_month"], 1000.0)
        self.assertEqual(summary["total_personal_spent_this_month"], 250.0)
        self.assertEqual(summary["net_personal_savings"], 750.0)


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
        self.create_url = reverse("create-expense", args=[self.group.public_id])

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

        balances = self.client.get(reverse("group-balances", args=[self.group.public_id]))
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
        self.assertEqual(self.client.get(reverse("group-balances", args=[self.group.public_id])).status_code, 403)

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
