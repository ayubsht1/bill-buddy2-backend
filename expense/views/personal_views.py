from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404

from bill_buddy.response import custom_response
from ..models import PersonalExpense
from ..serializers import PersonalExpenseSerializer

class PersonalExpenseListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Fetch all individual transaction logs (Incomes & Expenses) for the user."""
        expenses = PersonalExpense.objects.filter(user=request.user)
        serializer = PersonalExpenseSerializer(expenses, many=True)
        return custom_response(
            success=True,
            message="Personal records fetched successfully.",
            data=serializer.data
        )

    def post(self, request):
        """Log a brand new individual transaction (Income or Expense)."""
        serializer = PersonalExpenseSerializer(data=request.data)
        if not serializer.is_valid():
            return custom_response(
                success=False, 
                message="Validation failed.", 
                errors=serializer.errors, 
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        personal_expense = serializer.save(user=request.user)
        return custom_response(
            success=True,
            message="Personal record saved successfully.",
            data=PersonalExpenseSerializer(personal_expense).data,
            status_code=status.HTTP_201_CREATED
        )

class PersonalExpenseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        """Safely delete a personal transaction entry."""
        expense = get_object_or_404(PersonalExpense, id=pk, user=request.user)
        expense.delete()
        return custom_response(
            success=True, 
            message="Record completely dropped."
        )