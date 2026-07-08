from rest_framework import serializers
from .models import Expense, ExpenseShare

class ExpenseShareSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id')
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = ExpenseShare
        fields = ['user_id', 'email', 'amount']

class ExpenseCreateSerializer(serializers.ModelSerializer):
    split_data = serializers.JSONField(write_only=True, required=False)
    split_type = serializers.ChoiceField(choices=['EQUAL', 'EXACT', 'PERCENT'], write_only=True, default='EQUAL')
    shares = ExpenseShareSerializer(many=True, read_only=True)

    class Meta:
        model = Expense
        fields = ['id', 'group', 'description', 'amount', 'paid_by', 'date', 'split_type', 'split_data', 'shares']
        read_only_fields = ['paid_by', 'group']