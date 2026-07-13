from rest_framework import serializers
from ..models import PersonalExpense

class PersonalExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonalExpense
        # Included 'transaction_type' in the exposed fields array
        fields = ['id', 'transaction_type', 'description', 'amount', 'category', 'date', 'created_at']
        read_only_fields = ['id', 'date', 'created_at']