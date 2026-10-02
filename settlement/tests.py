from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from expense.models import Expense, ExpenseShare
from groups.models import Group, GroupMembership
from .models import Settlement


User = get_user_model()


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class SettlementApiTests(APITestCase):
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
        self.group = Group.objects.create(name="Cabin", creator=self.alice)
        for user in (self.alice, self.bob):
            GroupMembership.objects.create(group=self.group, user=user)
        expense = Expense.objects.create(
            group=self.group,
            description="Cabin",
            amount=Decimal("20.00"),
            paid_by=self.alice,
        )
        ExpenseShare.objects.create(expense=expense, user=self.alice, amount=Decimal("10.00"))
        ExpenseShare.objects.create(expense=expense, user=self.bob, amount=Decimal("10.00"))
        self.url = reverse("record-settlement", args=[self.group.id])
        self.client.force_authenticate(self.bob)

    def test_settlement_only_applies_to_current_debt_and_cannot_be_duplicated(self):
        response = self.client.post(
            self.url,
            {"paid_to": self.alice.id, "amount": "10.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        settlement = Settlement.objects.get()
        self.assertEqual(settlement.paid_by, self.bob)

        duplicate = self.client.post(
            self.url,
            {"paid_to": self.alice.id, "amount": "0.01"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(Settlement.objects.count(), 1)

    def test_settlement_rejects_unrelated_direction_and_nonmember_access(self):
        wrong_direction = self.client.post(
            self.url,
            {"paid_to": self.bob.id, "amount": "1.00"},
            format="json",
        )
        self.assertEqual(wrong_direction.status_code, 400)

        self.client.force_authenticate(self.outsider)
        forbidden = self.client.post(
            self.url,
            {"paid_to": self.alice.id, "amount": "1.00"},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_settlement_requires_greedy_suggested_pair_and_caps_amount(self):
        GroupMembership.objects.create(group=self.group, user=self.charlie)
        second_expense = Expense.objects.create(
            group=self.group,
            description="Supplies",
            amount=Decimal("20.00"),
            paid_by=self.alice,
        )
        ExpenseShare.objects.create(
            expense=second_expense, user=self.alice, amount=Decimal("10.00")
        )
        ExpenseShare.objects.create(
            expense=second_expense, user=self.charlie, amount=Decimal("10.00")
        )

        not_suggested_pair = self.client.post(
            self.url,
            {"paid_to": self.charlie.id, "amount": "1.00"},
            format="json",
        )
        self.assertEqual(not_suggested_pair.status_code, 400)

        over_suggested_amount = self.client.post(
            self.url,
            {"paid_to": self.alice.id, "amount": "10.01"},
            format="json",
        )
        self.assertEqual(over_suggested_amount.status_code, 400)
        self.assertEqual(Settlement.objects.count(), 0)

        valid = self.client.post(
            self.url,
            {"paid_to": self.alice.id, "amount": "10.00"},
            format="json",
        )
        self.assertEqual(valid.status_code, 201)
        self.assertEqual(Settlement.objects.count(), 1)

    def test_only_settlement_payer_can_delete_record(self):
        settlement = Settlement.objects.create(
            group=self.group,
            paid_by=self.bob,
            paid_to=self.alice,
            amount=Decimal("5.00"),
        )
        self.client.force_authenticate(self.alice)
        forbidden = self.client.delete(reverse("settlement-detail", args=[settlement.id]))
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_authenticate(self.bob)
        deleted = self.client.delete(reverse("settlement-detail", args=[settlement.id]))
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Settlement.objects.filter(id=settlement.id).exists())
