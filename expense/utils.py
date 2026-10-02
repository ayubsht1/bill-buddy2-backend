from decimal import Decimal
from django.db.models import Sum

from .models import Expense, ExpenseShare
from groups.models import GroupMembership
from settlement.models import Settlement


def group_net_balances(group):
    """Return each group member's net balance (positive means owed money)."""
    balances = {
        user_id: Decimal("0.00")
        for user_id in GroupMembership.objects.filter(group=group).values_list("user_id", flat=True)
    }

    expenses = Expense.objects.filter(group=group).values("paid_by_id").annotate(total=Sum("amount"))
    for row in expenses:
        balances[row["paid_by_id"]] = balances.get(row["paid_by_id"], Decimal("0.00")) + row["total"]

    shares = ExpenseShare.objects.filter(expense__group=group).values("user_id").annotate(total=Sum("amount"))
    for row in shares:
        balances[row["user_id"]] = balances.get(row["user_id"], Decimal("0.00")) - row["total"]

    settlements = Settlement.objects.filter(group=group)
    paid = settlements.values("paid_by_id").annotate(total=Sum("amount"))
    for row in paid:
        balances[row["paid_by_id"]] = balances.get(row["paid_by_id"], Decimal("0.00")) + row["total"]
    received = settlements.values("paid_to_id").annotate(total=Sum("amount"))
    for row in received:
        balances[row["paid_to_id"]] = balances.get(row["paid_to_id"], Decimal("0.00")) - row["total"]

    return {user_id: amount.quantize(Decimal("0.01")) for user_id, amount in balances.items()}


def calculate_expense_shares(group, amount, split_type, split_data):
    """Validate a split and return exact, cent-accurate shares by user ID."""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValueError("Expense amount must be greater than zero.")

    allowed_user_ids = set(group.members.values_list("id", flat=True))
    participants = []
    for item in split_data or []:
        user_id = item.get("user_id") if isinstance(item, dict) else item
        if isinstance(user_id, bool) or not str(user_id).isdigit():
            raise ValueError("Each split participant must have a valid user_id.")
        participants.append(int(user_id))

    if not participants and split_type == "EQUAL":
        participants = sorted(allowed_user_ids)
    if not participants:
        raise ValueError("At least one split participant is required.")
    if len(participants) != len(set(participants)):
        raise ValueError("A participant may only appear once in a split.")
    if not set(participants).issubset(allowed_user_ids):
        raise ValueError("All split participants must be current members of this group.")

    if split_type == "EQUAL":
        cents = int(amount * 100)
        base, remainder = divmod(cents, len(participants))
        return [
            (user_id, Decimal(base + (index < remainder)) / 100)
            for index, user_id in enumerate(participants)
        ]

    if split_type == "EXACT":
        shares = []
        for item in split_data:
            if not isinstance(item, dict) or "amount" not in item:
                raise ValueError("Each exact split requires user_id and amount.")
            share_amount = Decimal(str(item["amount"]))
            if share_amount < 0 or share_amount.quantize(Decimal("0.01")) != share_amount:
                raise ValueError("Split amounts must be non-negative with at most two decimal places.")
            shares.append((int(item["user_id"]), share_amount))
        if sum((share for _, share in shares), Decimal("0.00")) != amount:
            raise ValueError("Exact split amounts must equal the expense total.")
        return shares

    if split_type == "PERCENT":
        shares = []
        total_percent = Decimal("0.00")
        for item in split_data:
            if not isinstance(item, dict) or "percentage" not in item:
                raise ValueError("Each percentage split requires user_id and percentage.")
            percentage = Decimal(str(item["percentage"]))
            if percentage < 0 or percentage.quantize(Decimal("0.01")) != percentage:
                raise ValueError("Percentages must be non-negative with at most two decimal places.")
            total_percent += percentage
            share_amount = (amount * percentage / Decimal("100")).quantize(Decimal("0.01"))
            shares.append([int(item["user_id"]), share_amount])
        if total_percent != Decimal("100.00"):
            raise ValueError("Split percentages must total exactly 100%.")
        rounding_difference = amount - sum((share for _, share in shares), Decimal("0.00"))
        shares[-1][1] += rounding_difference
        return [(user_id, share) for user_id, share in shares]

    raise ValueError("Unsupported split type.")

def _match_debts(net_balances):
    """
    net_balances is a dictionary: { user_id: net_amount }
    e.g., { 1: Decimal('-30.00'), 2: Decimal('50.00'), 3: Decimal('-20.00') }
    """
    # Separate into debtors and creditors, dropping any users who are already even (0.00)
    debtors = []   # Elements will be [amount, user_id] -> amount is positive for easier sorting
    creditors = [] # Elements will be [amount, user_id]

    for user_id, balance in net_balances.items():
        if balance < -0.01:
            debtors.append([abs(balance), user_id])
        elif balance > 0.01:
            creditors.append([balance, user_id])

    suggested_settlements = []

    # Greedy Match loop
    while debtors and creditors:
        # Sort both lists so the largest amounts are always at the end (-1 index)
        debtors.sort()
        creditors.sort()

        max_debt_amount, debtor_id = debtors[-1]
        max_credit_amount, creditor_id = creditors[-1]

        # Find the maximum amount that can be settled between these two specific users
        settle_amount = min(max_debt_amount, max_credit_amount)

        # Record the transaction instruction
        suggested_settlements.append({
            "from_user_id": debtor_id,
            "to_user_id": creditor_id,
            "amount": settle_amount.quantize(Decimal('0.01'))
        })

        # Deduct the settled amount from their standing totals
        debtors[-1][0] -= settle_amount
        creditors[-1][0] -= settle_amount

        # Remove users from pool if their balance hits zero
        if debtors[-1][0] < 0.01:
            debtors.pop()
        if creditors[-1][0] < 0.01:
            creditors.pop()

    return suggested_settlements


def suggested_settlement_amount(net_balances, debtor_id, creditor_id):
    """Return the suggested amount for this exact payer/recipient pair, or zero."""
    return next(
        (
            item["amount"]
            for item in _match_debts(net_balances)
            if item["from_user_id"] == debtor_id and item["to_user_id"] == creditor_id
        ),
        Decimal("0.00")
    )


def simplify_debts(net_balances):
    """Return greedy debt matches in the existing JSON-compatible float format."""
    return [
        {
            **item,
            "amount": float(item["amount"])
        }
        for item in _match_debts(net_balances)
    ]