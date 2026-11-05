from rest_framework import serializers
from ..models import PersonalExpense

class PersonalExpenseSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source='public_id', read_only=True)

    class Meta:
        model = PersonalExpense
        fields = ['id', 'transaction_type', 'description', 'amount', 'category', 'payment_method', 'date', 'created_at']
        read_only_fields = ['id', 'created_at']

    def to_internal_value(self, data):
        if isinstance(data, dict) and isinstance(data.get('transaction_type'), str):
            data = data.copy()
            data['transaction_type'] = data['transaction_type'].upper()
        return super().to_internal_value(data)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate(self, attrs):
        income_categories = {'SALARY', 'FREELANCE', 'INVESTMENT', 'GIFT'}
        expense_categories = {'FOOD', 'SHOPPING', 'UTILITIES', 'TRANSPORT', 'ENTERTAINMENT', 'GROCERIES'}

        t_type = attrs.get('transaction_type', getattr(self.instance, 'transaction_type', None))
        category = attrs.get('category', getattr(self.instance, 'category', None))

        if t_type == 'INCOME' and category in expense_categories:
            raise serializers.ValidationError({"category": "Selected category is not valid for Income."})
        if t_type == 'EXPENSE' and category in income_categories:
            raise serializers.ValidationError({"category": "Selected category is not valid for Expense."})
            
        return attrs