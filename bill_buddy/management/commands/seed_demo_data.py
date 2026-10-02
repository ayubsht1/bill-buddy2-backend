import os
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from bill_buddy.models import Friendship
from expense.models import Expense, ExpenseShare, PersonalExpense
from expense.utils import calculate_expense_shares, group_net_balances, simplify_debts
from groups.models import Group, GroupMembership, GroupMessage
from settlement.models import Settlement


DEMO_PASSWORD = "BillBuddyDemo123!"
DEMO_USERS = (
    ("demo@example.test", "demo", "Alex", "Morgan"),
    ("maya@example.test", "maya", "Maya", "Sharma"),
    ("arjun@example.test", "arjun", "Arjun", "Thapa"),
    ("sam@example.test", "sam", "Sam", "Rai"),
    ("nina@example.test", "nina", "Nina", "Gurung"),
)


def _month_day(today: date, months_ago: int, day: int) -> date:
    month_index = today.year * 12 + today.month - 1 - months_ago
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    target_day = min(day, 28)
    if months_ago == 0:
        target_day = min(target_day, today.day)
    return date(year, month, target_day)


class Command(BaseCommand):
    help = "Create repeatable BillBuddy demo users and sample financial activity."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=os.environ.get("BILLBUDDY_DEMO_PASSWORD", DEMO_PASSWORD),
            help="Password for the demo accounts (or set BILLBUDDY_DEMO_PASSWORD).",
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Allow seeding when Django DEBUG is false. Use only on a disposable database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]
        if not password:
            raise CommandError("A non-empty demo password is required.")
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "Demo seeding is disabled when DEBUG is false. "
                "Use a disposable database or pass --allow-production explicitly."
            )

        users = self._seed_users(password)
        demo, maya, arjun, sam, nina = (users[key] for key, *_ in DEMO_USERS)

        self._seed_friendships(demo, maya, arjun, sam, nina)
        trip = self._seed_group(
            name="Pokhara Weekend",
            description="A demo trip with shared stays, rides, meals, and balances.",
            creator=demo,
            memberships={
                demo: GroupMembership.Role.OWNER,
                maya: GroupMembership.Role.ADMIN,
                arjun: GroupMembership.Role.MEMBER,
                sam: GroupMembership.Role.MEMBER,
            },
        )
        apartment = self._seed_group(
            name="Apartment Crew",
            description="A second demo group for recurring household bills.",
            creator=maya,
            memberships={
                maya: GroupMembership.Role.OWNER,
                demo: GroupMembership.Role.ADMIN,
                sam: GroupMembership.Role.MEMBER,
            },
        )

        today = timezone.localdate()
        self._seed_personal_transactions(demo, today)
        self._seed_group_expenses(trip, today, [
            ("Cabin stay", Decimal("480.00"), demo, "EQUAL", []),
            (
                "Airport taxi",
                Decimal("80.00"),
                maya,
                "EXACT",
                [
                    {"user_id": demo.id, "amount": "20.00"},
                    {"user_id": maya.id, "amount": "20.00"},
                    {"user_id": arjun.id, "amount": "25.00"},
                    {"user_id": sam.id, "amount": "15.00"},
                ],
            ),
            (
                "Dinner by the lake",
                Decimal("160.00"),
                arjun,
                "PERCENT",
                [
                    {"user_id": demo.id, "percentage": "20.00"},
                    {"user_id": maya.id, "percentage": "25.00"},
                    {"user_id": arjun.id, "percentage": "25.00"},
                    {"user_id": sam.id, "percentage": "30.00"},
                ],
            ),
            ("Trip groceries", Decimal("96.00"), sam, "EQUAL", []),
        ])
        self._seed_group_expenses(apartment, today, [
            ("Power and internet", Decimal("120.00"), maya, "EQUAL", []),
            (
                "Cleaning supplies",
                Decimal("45.00"),
                demo,
                "PERCENT",
                [
                    {"user_id": maya.id, "percentage": "40.00"},
                    {"user_id": demo.id, "percentage": "30.00"},
                    {"user_id": sam.id, "percentage": "30.00"},
                ],
            ),
            (
                "Water delivery",
                Decimal("36.00"),
                sam,
                "EXACT",
                [
                    {"user_id": maya.id, "amount": "12.00"},
                    {"user_id": demo.id, "amount": "12.00"},
                    {"user_id": sam.id, "amount": "12.00"},
                ],
            ),
        ])
        self._seed_chat(trip, demo, maya, arjun, sam)
        self._seed_chat(apartment, maya, demo, sam)
        self._seed_settlement(trip)

        self.stdout.write(self.style.SUCCESS(
            "BillBuddy demo data is ready.\n"
            "Login: demo@example.test\n"
            f"Password: {password}\n"
            f"Groups: {trip.name}, {apartment.name}\n"
            "The command can be rerun safely; it only updates its named demo records."
        ))

    def _seed_users(self, password):
        user_model = get_user_model()
        users = {}
        for email, username, first_name, last_name in DEMO_USERS:
            user, _ = user_model.objects.get_or_create(
                email=email,
                defaults={"username": username},
            )
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = True
            user.set_password(password)
            user.save()
            users[email] = user
        return users

    @staticmethod
    def _seed_friendships(demo, maya, arjun, sam, nina):
        friendships = (
            (demo, maya, Friendship.Status.ACCEPTED),
            (arjun, demo, Friendship.Status.ACCEPTED),
            (sam, demo, Friendship.Status.PENDING),
            (demo, nina, Friendship.Status.PENDING),
        )
        for from_user, to_user, status in friendships:
            Friendship.objects.update_or_create(
                from_user=from_user,
                to_user=to_user,
                defaults={"status": status},
            )

    @staticmethod
    def _seed_group(name, description, creator, memberships):
        group, _ = Group.objects.update_or_create(
            name=name,
            creator=creator,
            defaults={"description": description},
        )
        for user, role in memberships.items():
            GroupMembership.objects.update_or_create(
                group=group,
                user=user,
                defaults={"role": role},
            )
        return group

    @staticmethod
    def _seed_personal_transactions(user, today):
        expenses = (
            ("Monthly rent", "OTHER", Decimal("1200.00")),
            ("Groceries", "GROCERIES", Decimal("325.00")),
            ("Utilities", "UTILITIES", Decimal("145.00")),
            ("Coffee and dining", "FOOD", Decimal("86.00")),
            ("Transit pass", "TRANSPORT", Decimal("65.00")),
            ("Weekend cinema", "ENTERTAINMENT", Decimal("42.00")),
            ("Online essentials", "SHOPPING", Decimal("58.00")),
        )
        for months_ago in range(12):
            variation = Decimal("1") + Decimal(months_ago % 4) / Decimal("100")
            transactions = [
                ("Monthly salary", "INCOME", "SALARY", Decimal("4200.00"), 2),
                ("Freelance project", "INCOME", "FREELANCE", Decimal("380.00"), 4),
                *[
                    (description, "EXPENSE", category, amount, index * 2 + 5)
                    for index, (description, category, amount) in enumerate(expenses)
                ],
            ]
            for description, transaction_type, category, amount, day in transactions:
                PersonalExpense.objects.update_or_create(
                    user=user,
                    transaction_type=transaction_type,
                    description=description,
                    date=_month_day(today, months_ago, day),
                    defaults={
                        "category": category,
                        "amount": (amount * variation).quantize(Decimal("0.01")),
                    },
                )

    @staticmethod
    def _seed_group_expenses(group, today, definitions):
        for index, (description, amount, paid_by, split_type, split_data) in enumerate(definitions):
            expense, _ = Expense.objects.update_or_create(
                group=group,
                description=description,
                defaults={
                    "amount": amount,
                    "paid_by": paid_by,
                    "split_type": split_type,
                    "split_data": split_data,
                },
            )
            Expense.objects.filter(pk=expense.pk).update(
                date=_month_day(today, index % 6, 8 + index * 3)
            )
            ExpenseShare.objects.filter(expense=expense).delete()
            shares = calculate_expense_shares(group, amount, split_type, split_data)
            ExpenseShare.objects.bulk_create([
                ExpenseShare(expense=expense, user_id=user_id, amount=share)
                for user_id, share in shares
            ])

    @staticmethod
    def _seed_chat(group, *users):
        messages = (
            (None, f"Welcome to {group.name}!"),
            (users[0], "I added a few sample expenses so we can try the split views."),
            (users[1], "Looks good — the balances and activity are showing up."),
            (users[-1], "I will settle my share after I check the totals."),
        )
        for sender, message in messages:
            GroupMessage.objects.get_or_create(
                group=group,
                sender=sender,
                message=message,
            )

    @staticmethod
    def _seed_settlement(group):
        if Settlement.objects.filter(group=group).exists():
            return
        for suggestion in simplify_debts(group_net_balances(group)):
            amount = min(Decimal("5.00"), Decimal(str(suggestion["amount"])))
            if amount > 0:
                Settlement.objects.create(
                    group=group,
                    paid_by_id=suggestion["from_user_id"],
                    paid_to_id=suggestion["to_user_id"],
                    amount=amount,
                )
                return
