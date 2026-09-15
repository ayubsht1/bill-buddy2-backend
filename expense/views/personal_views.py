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
        """Fetch transaction logs with optional URL query parameters for filtering."""
        queryset = PersonalExpense.objects.filter(user=request.user)

        # Optional Query Parameter Filters
        transaction_type = request.query_params.get('type')  # ?type=INCOME or ?type=EXPENSE
        category = request.query_params.get('category')      # ?category=FOOD
        
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type.upper())
        if category:
            queryset = queryset.filter(category=category.upper())

        serializer = PersonalExpenseSerializer(queryset, many=True)
        return custom_response(
            success=True,
            message="Personal records fetched successfully.",
            data=serializer.data
        )

    def post(self, request):
        """Log a new individual transaction."""
        serializer = PersonalExpenseSerializer(data=request.data)
        if not serializer.is_valid():
            return custom_response(
                success=False, 
                message="Validation failed.", 
                errors=serializer.errors, 
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        # Saving directly uses the existing validated serializer instance
        serializer.save(user=request.user)
        return custom_response(
            success=True,
            message="Personal record saved successfully.",
            data=serializer.data,
            status_code=status.HTTP_201_CREATED
        )


class PersonalExpenseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        """Helper method to retrieve record owned by current user."""
        return get_object_or_404(PersonalExpense, id=pk, user=user)

    def get(self, request, pk):
        """Fetch details of a single transaction."""
        expense = self.get_object(pk, request.user)
        serializer = PersonalExpenseSerializer(expense)
        return custom_response(
            success=True,
            message="Record details fetched successfully.",
            data=serializer.data
        )

    def patch(self, request, pk):
        """Update an existing transaction (partial updates allowed)."""
        expense = self.get_object(pk, request.user)
        serializer = PersonalExpenseSerializer(expense, data=request.data, partial=True)
        if not serializer.is_valid():
            return custom_response(
                success=False,
                message="Validation failed.",
                errors=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        serializer.save()
        return custom_response(
            success=True,
            message="Record updated successfully.",
            data=serializer.data
        )

    def delete(self, request, pk):
        """Safely delete a personal transaction entry."""
        expense = self.get_object(pk, request.user)
        expense.delete()
        return custom_response(
            success=True, 
            message="Record completely dropped."
        )