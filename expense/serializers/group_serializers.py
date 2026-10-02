from rest_framework import serializers
from ..models import Expense, ExpenseShare

class ExpenseShareSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id')
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = ExpenseShare
        fields = ['user_id', 'email', 'amount']

class ExpenseCreateSerializer(serializers.ModelSerializer):
    split_data = serializers.ListField(
        child=serializers.JSONField(),
        write_only=True, 
        required=False
    )
    split_type = serializers.ChoiceField(
        choices=['EQUAL', 'EXACT', 'PERCENT'], 
        write_only=True, 
        default='EQUAL'
    )
    shares = ExpenseShareSerializer(many=True, read_only=True)

    class Meta:
        model = Expense
        fields = ['id', 'group', 'description', 'amount', 'paid_by', 'date', 'split_type', 'split_data', 'shares']
        read_only_fields = ['paid_by', 'group']

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Expense amount must be greater than zero.")
        return value

    def validate(self, attrs):
        split_type = attrs.get('split_type', getattr(self.instance, 'split_type', 'EQUAL'))
        split_data = attrs.get('split_data', getattr(self.instance, 'split_data', []))
        if split_type != 'EQUAL' and not split_data:
            raise serializers.ValidationError({
                'split_data': "Custom splits require participant data."
            })
        if not isinstance(split_data, list):
            raise serializers.ValidationError({'split_data': "Split data must be a list."})
        return attrs