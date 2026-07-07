from rest_framework import serializers
from .models import Settlement

class SettlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Settlement
        fields = ['id', 'group', 'paid_by', 'paid_to', 'amount', 'date']
        read_only_fields = ['paid_by']

    def validate(self, data):
        # Prevent settling with yourself
        if self.context['request'].user == data['paid_to']:
            raise serializers.ValidationError("You cannot record a settlement to yourself.")
        
        # Ensure the amount is positive
        if data['amount'] <= 0:
            raise serializers.ValidationError("Settlement amount must be greater than zero.")
            
        return data