import os
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.db import transaction
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
from ..utils import simplify_debts

# Real-time message broadcasting
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

class CreateExpenseView(APIView):
    permission_classes = [IsAuthenticated]

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
                success=False, message="Validation error", errors=serializer.errors, status_code=status.HTTP_400_BAD_REQUEST
            )

        split_type = serializer.validated_data.get('split_type', 'EQUAL')
        split_data = serializer.validated_data.get('split_data', [])
        total_amount = Decimal(str(serializer.validated_data['amount']))

        try:
            with transaction.atomic():
                # Force save with the URL's contextual group, ignoring body context overrides
                expense = serializer.save(paid_by=request.user, group=group)

                if split_type == 'EQUAL':
                    members_to_split = split_data if split_data else [m.id for m in group.members.all()]
                    num_members = len(members_to_split)
                    
                    if num_members == 0:
                        raise ValueError("No members to split the bill with.")

                    share_amount = (total_amount / num_members).quantize(Decimal('0.01'))

                    for member_id in members_to_split:
                        ExpenseShare.objects.create(expense=expense, user_id=member_id, amount=share_amount)

                elif split_type == 'EXACT':
                    total_split_sum = Decimal('0.00')
                    for item in split_data:
                        user_id = item.get('user_id')
                        user_amount = Decimal(str(item.get('amount', 0)))
                        total_split_sum += user_amount

                        ExpenseShare.objects.create(expense=expense, user_id=user_id, amount=user_amount)
                    
                    # Moved safely INSIDE the transactional context block:
                    if total_split_sum != total_amount:
                        raise ValueError(f"Total split amounts ({total_split_sum}) must equal expense total ({total_amount}).")

                elif split_type == 'PERCENT':
                    total_percentage = Decimal('0.00')
                    for item in split_data:
                        user_id = item.get('user_id')
                        percentage = Decimal(str(item.get('percentage', 0)))
                        total_percentage += percentage

                        share_amount = (total_amount * (percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
                        ExpenseShare.objects.create(expense=expense, user_id=user_id, amount=share_amount)

                    # Moved safely INSIDE the transactional context block:
                    if total_percentage != Decimal('100.00'):
                        raise ValueError(f"Total percentages ({total_percentage}%) must equal exactly 100%.")

            # 🚀 WRITE TO DB: Save the system log text directly to your database history tracking logs
            system_msg = f"📊 {request.user.username} added an expense: '{expense.description}' for ${expense.amount}."
            GroupMessage.objects.create(group=group, sender=None, message=system_msg)

            # ⚡ Real-time WebSocket broadcast formatted to match consumer architecture exactly
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_{group.id}",  
                {
                    "type": "room_event",
                    "event_type": "chat_message",
                    "data": {
                        "id": None,
                        "sender_username": "SYSTEM",
                        "message": system_msg,
                        "is_system": True,
                        "is_forwarded": False,
                        "is_pinned": False,
                        "is_deleted": False
                    }
                }
            )

            expense.refresh_from_db()

            return custom_response(
                success=True, 
                message="Expense added and split successfully.", 
                data=ExpenseCreateSerializer(expense).data, 
                status_code=status.HTTP_201_CREATED
            )

        except ValueError as e:
            return custom_response(success=False, message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return custom_response(
                success=False, 
                message=f"Internal Server Error: {str(e)}", 
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GroupBalancesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        net_balances = {member.id: Decimal('0.00') for member in group.members.all()}

        expenses = Expense.objects.filter(group=group)
        for expense in expenses:
            if expense.paid_by_id in net_balances:
                net_balances[expense.paid_by_id] += expense.amount

        shares = ExpenseShare.objects.filter(expense__group=group)
        for share in shares:
            if share.user_id in net_balances:
                net_balances[share.user_id] -= share.amount

        settlements = Settlement.objects.filter(group=group)
        for settlement in settlements:
            if settlement.paid_by_id in net_balances:
                net_balances[settlement.paid_by_id] += settlement.amount  
            if settlement.paid_to_id in net_balances:
                net_balances[settlement.paid_to_id] -= settlement.amount  

        member_profiles = {}
        for member in group.members.all():
            member_profiles[member.id] = {
                "id": member.id, "username": member.username, "email": member.email, "net_balance": float(net_balances[member.id])
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

    def patch(self, request, expense_id):
        """Allows partial updates (PATCH) to an expense and safely recalculates splits."""
        expense = get_object_or_404(Expense, id=expense_id)

        # 🔒 STRICT SECURITY CHECK: Only the creator (paid_by) can edit
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

        # Fallbacks pulling straight from the database model columns
        split_type = request.data.get('split_type', expense.split_type)
        split_data = request.data.get('split_data', expense.split_data)
        total_amount = Decimal(str(request.data.get('amount', expense.amount)))

        try:
            with transaction.atomic():
                # 1. Update core expense details
                updated_expense = serializer.save()

                # Always clear and recalculate if amount, split type, OR split distribution changes
                if 'amount' in request.data or 'split_type' in request.data or 'split_data' in request.data:
                    expense.shares.all().delete()

                    # 3. Handle split modes
                    if split_type == 'EQUAL':
                        members_to_split = split_data if split_data else [m.id for m in expense.group.members.all()]
                        num_members = len(members_to_split)
                        
                        if num_members == 0:
                            raise ValueError("No members to split the bill with.")

                        share_amount = (total_amount / num_members).quantize(Decimal('0.01'))
                        for member_id in members_to_split:
                            ExpenseShare.objects.create(expense=updated_expense, user_id=member_id, amount=share_amount)

                    elif split_type == 'EXACT':
                        total_split_sum = Decimal('0.00')
                        for item in split_data:
                            user_id = item.get('user_id')
                            user_amount = Decimal(str(item.get('amount', 0)))
                            total_split_sum += user_amount
                            ExpenseShare.objects.create(expense=updated_expense, user_id=user_id, amount=user_amount)
                        
                        if total_split_sum != total_amount:
                            raise ValueError(f"Total split amounts ({total_split_sum}) must equal expense total ({total_amount}).")

                    elif split_type == 'PERCENT':
                        total_percentage = Decimal('0.00')
                        calculated_total_shares = Decimal('0.00')
                        shares_to_create = []

                        for item in split_data:
                            user_id = item.get('user_id')
                            percentage = Decimal(str(item.get('percentage', 0)))
                            total_percentage += percentage

                            share_amount = (total_amount * (percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
                            calculated_total_shares += share_amount
                            shares_to_create.append({'user_id': user_id, 'amount': share_amount})

                        if total_percentage != Decimal('100.00'):
                            raise ValueError(f"Total percentages ({total_percentage}%) must equal exactly 100%.")

                        # Penny rounding problem resolution
                        rounding_difference = total_amount - calculated_total_shares
                        if rounding_difference != Decimal('0.00') and shares_to_create:
                            shares_to_create[-1]['amount'] += rounding_difference

                        for share in shares_to_create:
                            ExpenseShare.objects.create(expense=updated_expense, user_id=share['user_id'], amount=share['amount'])

            # 🚀 WRITE TO DB: Save update historical track log
            update_msg = f"✏️ {request.user.username} updated the expense details for '{updated_expense.description}'."
            GroupMessage.objects.create(group=expense.group, sender=None, message=update_msg)

            # ⚡ REAL-TIME Update Broadcast matching the structural layout contract
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_{expense.group.id}",  
                {
                    "type": "room_event",
                    "event_type": "chat_message",
                    "data": {
                        "id": None,
                        "sender_username": "SYSTEM",
                        "message": update_msg,
                        "is_system": True,
                        "is_forwarded": False,
                        "is_pinned": False,
                        "is_deleted": False
                    }
                }
            )

            updated_expense.refresh_from_db() 

            return custom_response(
                success=True,
                message="Expense updated and splits recalculated successfully.",
                data=ExpenseCreateSerializer(updated_expense).data
            )

        except ValueError as e:
            return custom_response(success=False, message=str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return custom_response(
                success=False, 
                message=f"Internal Server Error: {str(e)}", 
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def put(self, request, expense_id):
        return self.patch(request, expense_id)

    def delete(self, request, expense_id):
        """Allows the owner to completely delete the expense and its child shares."""
        expense = get_object_or_404(Expense, id=expense_id)

        if expense.paid_by != request.user:
            return custom_response(
                success=False,
                message="Permission denied. Only the person who paid for this expense can delete it.",
                status_code=status.HTTP_403_FORBIDDEN
            )

        group = expense.group
        description = expense.description
        
        expense.delete()

        # 🚀 WRITE TO DB: Save deletion tracking log
        delete_msg = f"🗑️ {request.user.username} deleted the expense: '{description}'."
        GroupMessage.objects.create(group=group, sender=None, message=delete_msg)

        # ⚡ REAL-TIME Deletion Broadcast matching the layout contract 
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"chat_{group.id}",  
            {
                "type": "room_event",
                "event_type": "chat_message",
                "data": {
                    "id": None,
                    "sender_username": "SYSTEM",
                    "message": delete_msg,
                    "is_system": True,
                    "is_forwarded": False,
                    "is_pinned": False,
                    "is_deleted": False
                }
            }
        )

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