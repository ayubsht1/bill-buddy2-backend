from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date

from bill_buddy.response import custom_response
from ..models import PersonalExpense
from ..serializers import PersonalExpenseSerializer

class PersonalExpenseListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Fetch the current user's transactions with optional filters."""
        queryset = PersonalExpense.objects.filter(user=request.user)

        transaction_type = request.query_params.get('type') or request.query_params.get('transaction_type')
        if transaction_type:
            transaction_type = transaction_type.upper()
            if transaction_type not in dict(PersonalExpense.TRANSACTION_TYPES):
                return custom_response(
                    success=False,
                    message="Invalid transaction type.",
                    errors={"type": "Use INCOME or EXPENSE."},
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(transaction_type=transaction_type)

        category = request.query_params.get('category')
        if category:
            category = category.upper()
            if category not in dict(PersonalExpense.CATEGORY_CHOICES):
                return custom_response(
                    success=False,
                    message="Invalid category.",
                    errors={"category": "Choose a supported personal transaction category."},
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            queryset = queryset.filter(category=category)

        parsed_dates = {}
        for parameter, lookup in (('start_date', 'date__gte'), ('end_date', 'date__lte')):
            value = request.query_params.get(parameter)
            if value:
                try:
                    date_value = parse_date(value)
                except ValueError:
                    date_value = None
                if date_value is None:
                    return custom_response(
                        success=False,
                        message="Invalid date filter.",
                        errors={parameter: "Use YYYY-MM-DD format."},
                        status_code=status.HTTP_400_BAD_REQUEST
                    )
                parsed_dates[parameter] = date_value
                queryset = queryset.filter(**{lookup: date_value})

        if (
            'start_date' in parsed_dates
            and 'end_date' in parsed_dates
            and parsed_dates['start_date'] > parsed_dates['end_date']
        ):
            return custom_response(
                success=False,
                message="Invalid date range.",
                errors={"end_date": "End date must be on or after start date."},
                status_code=status.HTTP_400_BAD_REQUEST
            )

        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(description__icontains=search)

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