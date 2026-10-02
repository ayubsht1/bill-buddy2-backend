import os
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.db import transaction
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from decimal import Decimal
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from decimal import Decimal
from google import genai
from django.utils import timezone

# Absolute imports targeting your clean folder structure
from ..models import Expense, ExpenseShare, PersonalExpense
from settlement.models import Settlement  # Points to your settlement app model
from groups.models import Group, GroupMessage # Imported GroupMessage for logs!
from bill_buddy.response import custom_response  # Clean custom response path!
from ..serializers import ExpenseCreateSerializer
from ..utils import calculate_expense_shares, group_net_balances, simplify_debts

# Real-time message broadcasting
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

User = get_user_model()

class CreateExpenseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False,
                message="You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        expenses = group.expenses.select_related('paid_by').prefetch_related('shares__user').order_by('-date', '-id')
        return custom_response(
            success=True,
            message="Group expenses retrieved successfully.",
            data=ExpenseCreateSerializer(expenses, many=True).data
        )

    def post(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False,
                message="You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        serializer = ExpenseCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation error",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )

        split_type = serializer.validated_data.get('split_type', 'EQUAL')
        split_data = serializer.validated_data.get('split_data', [])
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return custom_response(
                    success=False,
                    message="You are not a member of this group.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            try:
                shares = calculate_expense_shares(
                    group, serializer.validated_data['amount'], split_type, split_data
                )
            except (ValueError, ArithmeticError) as exc:
                return custom_response(
                    success=False,
                    message="Validation error",
                    errors={"split_data": str(exc)},
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            stored_split = split_data or [user_id for user_id, _ in shares]
            expense = serializer.save(
                paid_by=request.user,
                group=group,
                split_type=split_type,
                split_data=stored_split
            )
            ExpenseShare.objects.bulk_create([
                ExpenseShare(expense=expense, user_id=user_id, amount=share)
                for user_id, share in shares
            ])
            system_msg = f"{request.user.username} added an expense: '{expense.description}' for ${expense.amount}."
            GroupMessage.objects.create(group=group, sender=None, message=system_msg)

        self._broadcast_system_message(group.id, system_msg)
        expense.refresh_from_db()
        return custom_response(
            success=True,
            message="Expense added and split successfully.",
            data=ExpenseCreateSerializer(expense).data,
            status_code=status.HTTP_201_CREATED
        )

    @staticmethod
    def _broadcast_system_message(group_id, message):
        channel_layer = get_channel_layer()
        transaction.on_commit(
            lambda: async_to_sync(channel_layer.group_send)(
                f"chat_{group_id}",
                {
                    "type": "room_event",
                    "event_type": "chat_message",
                    "data": {
                        "id": None,
                        "sender_username": "SYSTEM",
                        "message": message,
                        "is_system": True,
                        "is_forwarded": False,
                        "is_pinned": False,
                        "is_deleted": False
                    }
                }
            ),
            robust=True
        )


class GroupBalancesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        if not group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False,
                message="You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return custom_response(
                    success=False,
                    message="You are not a member of this group.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            net_balances = group_net_balances(group)
        members = User.objects.filter(id__in=net_balances).order_by('id')
        member_profiles = {
            member.id: {
                "id": member.id,
                "username": member.username,
                "email": member.email,
                "net_balance": float(net_balances[member.id])
            }
            for member in members
        }

        raw_settlements = simplify_debts(net_balances)
        optimized_instructions = []
        for s in raw_settlements:
            optimized_instructions.append({
                "debtor": member_profiles[s["from_user_id"]],
                "creditor": member_profiles[s["to_user_id"]],
                "amount": s["amount"]
            })

        return custom_response(
            success=True,
            message="Group balances calculated and simplified.",
            data={"balances": list(member_profiles.values()), "suggested_settlements": optimized_instructions}
        )
    

class ExpenseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, expense_id):
        expense = get_object_or_404(
            Expense.objects.select_related('group', 'paid_by').prefetch_related('shares__user'),
            id=expense_id
        )
        if not expense.group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False,
                message="You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        return custom_response(
            success=True,
            message="Expense retrieved successfully.",
            data=ExpenseCreateSerializer(expense).data
        )

    def patch(self, request, expense_id):
        expense = get_object_or_404(Expense, id=expense_id)
        if not expense.group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False,
                message="You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        if expense.paid_by != request.user:
            return custom_response(
                success=False,
                message="Permission denied. Only the person who paid for this expense can edit it.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        serializer = ExpenseCreateSerializer(expense, data=request.data, partial=True)
        if not serializer.is_valid():
            return custom_response(
                success=False, message="Validation error", errors=serializer.errors, status_code=status.HTTP_400_BAD_REQUEST
            )

        split_type = serializer.validated_data.get('split_type', expense.split_type)
        split_data = serializer.validated_data.get('split_data', expense.split_data)
        amount = serializer.validated_data.get('amount', expense.amount)
        recalculate = any(key in serializer.validated_data for key in ('amount', 'split_type', 'split_data'))

        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=expense.group_id)
            if not group.members.filter(id=request.user.id).exists():
                return custom_response(
                    success=False,
                    message="You are not a member of this group.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            if expense.paid_by_id != request.user.id:
                return custom_response(
                    success=False,
                    message="Permission denied. Only the person who paid for this expense can edit it.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            try:
                shares = calculate_expense_shares(group, amount, split_type, split_data) if recalculate else []
            except (ValueError, ArithmeticError) as exc:
                return custom_response(
                    success=False,
                    message="Validation error",
                    errors={"split_data": str(exc)},
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            updated_expense = serializer.save(split_data=split_data, split_type=split_type)
            if recalculate:
                updated_expense.shares.all().delete()
                ExpenseShare.objects.bulk_create([
                    ExpenseShare(expense=updated_expense, user_id=user_id, amount=share)
                    for user_id, share in shares
                ])
            update_msg = f"{request.user.username} updated the expense '{updated_expense.description}'."
            GroupMessage.objects.create(group=expense.group, sender=None, message=update_msg)

        CreateExpenseView._broadcast_system_message(expense.group_id, update_msg)
        updated_expense.refresh_from_db()
        return custom_response(
            success=True,
            message="Expense updated successfully.",
            data=ExpenseCreateSerializer(updated_expense).data
        )
    def put(self, request, expense_id):
        return self.patch(request, expense_id)

    def delete(self, request, expense_id):
        expense = get_object_or_404(Expense, id=expense_id)
        if not expense.group.members.filter(id=request.user.id).exists():
            return custom_response(
                success=False,
                message="You are not a member of this group.",
                status_code=status.HTTP_403_FORBIDDEN
            )
        if expense.paid_by != request.user:
            return custom_response(
                success=False,
                message="Permission denied. Only the person who paid for this expense can delete it.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        group = expense.group
        description = expense.description
        
        delete_msg = f"{request.user.username} deleted the expense: '{description}'."
        with transaction.atomic():
            group = Group.objects.select_for_update().get(id=group.id)
            if not group.members.filter(id=request.user.id).exists() or expense.paid_by_id != request.user.id:
                return custom_response(
                    success=False,
                    message="You are no longer authorized to delete this expense.",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            expense.delete()
            GroupMessage.objects.create(group=group, sender=None, message=delete_msg)
        CreateExpenseView._broadcast_system_message(group.id, delete_msg)

        return custom_response(
            success=True,
            message="Expense deleted successfully."
        )
    
class DashboardAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # 👥 1. CALCULATE NET BALANCES ACROSS ALL SHARED GROUPS
        net_group_balance = Decimal('0.00')
        user_groups = Group.objects.filter(members=user)

        for group in user_groups:
            paid_by_user = Expense.objects.filter(group=group, paid_by=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            user_shares = ExpenseShare.objects.filter(expense__group=group, user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            settlements_paid = Settlement.objects.filter(group=group, paid_by=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            settlements_received = Settlement.objects.filter(group=group, paid_to=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            net_group_balance += (paid_by_user - user_shares + settlements_paid - settlements_received)

        # Base querysets
        personal_qs = PersonalExpense.objects.filter(user=user)
        
        # 🗓️ GET CURRENT YEAR AND MONTH FOR FILTERING CARDS/PIE CHARTS
        today = timezone.now().date()
        current_month_qs = personal_qs.filter(date__year=today.year, date__month=today.month)

        # 🛍️ 2. PERSONAL EXPENSE BREAKDOWN (Locked to current month for accurate Pie/Donut breakdown)
        category_breakdown = (
            current_month_qs.filter(transaction_type='EXPENSE')
            .values('category')
            .annotate(total_amount=Sum('amount'))
            .order_by('-total_amount')
        )
        formatted_categories = {item['category']: float(item['total_amount']) for item in category_breakdown}

        # 📈 3. MONTHLY HISTORICAL TREND LINES (Keeps lifetime historical query for bar charts)
        monthly_trends = (
            personal_qs.annotate(month=TruncMonth('date'))
            .values('month', 'transaction_type')
            .annotate(total=Sum('amount'))
            .order_by('-month')
        )

        trends_map = {}
        for item in monthly_trends:
            if not item['month']:
                continue
            month_str = item['month'].strftime("%B %Y")
            
            if month_str not in trends_map:
                trends_map[month_str] = {"month": month_str, "income": 0.0, "expense": 0.0}
            
            t_type = item['transaction_type'].lower()
            trends_map[month_str][t_type] = float(item['total'])

        formatted_trends = list(trends_map.values())[:6]

        # 💰 4. CALCULATE CURRENT MONTH TOTAL VALUES FOR CARD SUMMARIES ONLY
        total_income_this_month = current_month_qs.filter(transaction_type='INCOME').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        total_expense_this_month = current_month_qs.filter(transaction_type='EXPENSE').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        dashboard_payload = {
            "summary": {
                "net_group_balance": float(net_group_balance),
                "balance_status": "YOU_ARE_OWED" if net_group_balance > 0 else ("OWED_MONEY" if net_group_balance < 0 else "SETTLED"),
                "total_personal_income_this_month": float(total_income_this_month),
                "total_personal_spent_this_month": float(total_expense_this_month),
                "net_personal_savings": float(total_income_this_month - total_expense_this_month)
            },
            "category_distribution": formatted_categories,
            "monthly_history": formatted_trends
        }

        return custom_response(
            success=True,
            message="Dashboard metrics generated successfully.",
            data=dashboard_payload,
            status_code=status.HTTP_200_OK
        )


class DashboardAiInsightsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        import google.genai as genai

        # 👥 1. AGGREGATE NET BALANCES ACROSS ALL SHARED GROUPS
        net_group_balance = Decimal('0.00')
        user_groups = Group.objects.filter(members=user)
        
        for group in user_groups:
            paid_by_user = Expense.objects.filter(group=group, paid_by=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            user_shares = ExpenseShare.objects.filter(expense__group=group, user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            settlements_paid = Settlement.objects.filter(group=group, paid_by=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            settlements_received = Settlement.objects.filter(group=group, paid_to=user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            net_group_balance += (paid_by_user - user_shares + settlements_paid - settlements_received)

        # Base querysets
        personal_qs = PersonalExpense.objects.filter(user=user)
        
        # 🗓️ LOCK AI TO CURRENT CALENDAR MONTH DATA ONLY
        today = timezone.now().date()
        current_month_qs = personal_qs.filter(date__year=today.year, date__month=today.month)
        
        total_personal_income = current_month_qs.filter(transaction_type='INCOME').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        total_personal_expense = current_month_qs.filter(transaction_type='EXPENSE').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        category_breakdown = (
            current_month_qs.filter(transaction_type='EXPENSE')
            .values('category')
            .annotate(total_amount=Sum('amount'))
            .order_by('-total_amount')
        )
        formatted_categories = {item['category']: float(item['total_amount']) for item in category_breakdown}

        finance_context = {
            "username": user.username,
            "net_group_balance": float(net_group_balance),
            "total_income_this_month": float(total_personal_income),
            "total_expense_this_month": float(total_personal_expense),
            "expense_category_distribution": formatted_categories,
        }

        try:
            client = genai.Client()
            
            system_prompt = (
                "You are Bill Buddy's AI Financial Coach. Analyze the provided financial data map for the user.\n"
                "Provide exactly three actionable, highly personalized, bulleted insight sentences for their dashboard widget.\n"
                "Rule 1: Cross-reference income vs expenses. Highlight their net savings pattern or warn them if expenses exceed income.\n"
                "Rule 2: Call out group balances dynamically. Remind them to collect money if owed, or clear tabs if they owe friends.\n"
                "Rule 3: Pinpoint the top category drain from their category distribution details.\n"
                "Tone: Clear, casual, direct, encouraging, and tech-focused. Never use markdown headers (e.g., #, ##) or bullet points symbols in the core text. Return plain text separated by newlines starting with a standard dash or bullet character."
            )

            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=f"Analyze this financial context snapshot: {finance_context}",
                config={"system_instruction": system_prompt, "temperature": 0.7}
            )
            
            ai_text_output = response.text

        except Exception as e:
            ai_text_output = (
                "• Your combined personal transactions and shared group records are successfully mapped.\n"
                "• Keep adding your income streams and bill split records to unlock deep budget analysis calculations.\n"
                "• Ensure your system environment variables contain a valid Gemini API configuration key to unlock real-time financial insights."
            )

        return custom_response(
            success=True,
            message="AI financial health tracking overview insights computed.",
            data={"insights": ai_text_output.strip()}
        )