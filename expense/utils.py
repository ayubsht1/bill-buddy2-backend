from decimal import Decimal

def simplify_debts(net_balances):
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
            "amount": float(settle_amount.quantize(Decimal('0.01')))
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