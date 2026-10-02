from rest_framework import serializers
from .models import Settlement

class SettlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Settlement
        fields = ['id', 'group', 'paid_by', 'paid_to', 'amount', 'date']
        read_only_fields = ['group', 'paid_by']

    def validate(self, data):
        # 🚀 CHANGED: Grab payer from the request data, fallback to request user
        request_user = self.context['request'].user
        paid_by_user = data.get('paid_by', request_user)
        paid_to_user = data.get('paid_to')

        # Prevent settling with yourself
        if paid_by_user == paid_to_user:
            raise serializers.ValidationError("A user cannot record a settlement to themselves.")
        
        # Ensure the amount is positive
        if data['amount'] <= 0:
            raise serializers.ValidationError("Settlement amount must be greater than zero.")
            
        return data