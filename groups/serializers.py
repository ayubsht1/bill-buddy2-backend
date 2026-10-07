from decimal import Decimal

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction
from .models import Group, GroupMessage, GroupMembership, GroupEvent, GroupEventBudgetItem

User = get_user_model()

class GroupMemberSerializer(serializers.ModelSerializer):
    """Provides minimal, clean user detail fields for group listings."""
    role = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'profile_picture', 'role']
    
    def get_role(self, obj):
        request = self.context.get('request')
        group = self.context.get('group')
        if group and request:
            return next(
                (
                    membership.role
                    for membership in group.memberships.all()
                    if membership.user_id == obj.id
                ),
                None
            )
        return None


class GroupMembershipSerializer(serializers.ModelSerializer):
    user = GroupMemberSerializer(read_only=True)
    
    class Meta:
        model = GroupMembership
        fields = ['id', 'user', 'role', 'joined_at']
        read_only_fields = ['id', 'joined_at']


class GroupSerializer(serializers.ModelSerializer):
    creator = GroupMemberSerializer(read_only=True)
    members = serializers.SerializerMethodField()
    memberships = GroupMembershipSerializer(many=True, read_only=True)
    join_code = serializers.CharField(read_only=True)
    user_role = serializers.SerializerMethodField()
    
    class Meta:
        model = Group
        fields = ['id', 'name', 'description', 'creator', 'members', 'memberships', 'join_code', 'created_at', 'user_role']
    
    def get_members(self, obj):
        context = {**self.context, 'group': obj}
        return GroupMembershipSerializer(
            obj.memberships.all(),
            many=True,
            context=context
        ).data
    
    def get_user_role(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return next(
                (
                    membership.role
                    for membership in obj.memberships.all()
                    if membership.user_id == request.user.id
                ),
                None
            )
        return None


class GroupMessageSerializer(serializers.ModelSerializer):
    sender_username = serializers.CharField(source='sender.username', read_only=True)
    is_system = serializers.SerializerMethodField()
    reply_to_id = serializers.IntegerField(source='reply_to.id', read_only=True)
    reply_to_text = serializers.SerializerMethodField()

    class Meta:
        model = GroupMessage
        fields = [
            'id', 'sender_username', 'message', 'timestamp',
            'is_system', 'reply_to_id', 'reply_to_text',
            'is_forwarded', 'is_pinned', 'is_deleted'
        ]

    def get_is_system(self, obj):
        return obj.sender is None

    def get_reply_to_text(self, obj):
        """Returns snippet of original text if this message is a reply."""
        if obj.reply_to:
            if obj.reply_to.is_deleted:
                return "Original message was deleted."
            return obj.reply_to.message[:50]
        return None

    def to_representation(self, instance):
        """Mask out the original raw text data payload if it was deleted."""
        ret = super().to_representation(instance)
        if instance.is_deleted:
            ret['message'] = "This message was deleted."
        return ret


class GroupEventBudgetItemSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )

    class Meta:
        model = GroupEventBudgetItem
        fields = ['id', 'description', 'amount']
        read_only_fields = ['id']


class GroupEventSerializer(serializers.ModelSerializer):
    budget_items = GroupEventBudgetItemSerializer(many=True, required=False)
    created_by = serializers.CharField(
        source='created_by.username',
        read_only=True,
        allow_null=True,
    )
    planned_budget = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = GroupEvent
        fields = [
            'id',
            'title',
            'description',
            'location',
            'starts_at',
            'ends_at',
            'created_by',
            'budget_items',
            'planned_budget',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'planned_budget', 'created_at', 'updated_at']

    def validate(self, attrs):
        starts_at = attrs.get(
            'starts_at',
            self.instance.starts_at if self.instance else None,
        )
        ends_at = attrs.get(
            'ends_at',
            self.instance.ends_at if self.instance else None,
        )
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError(
                {'ends_at': 'The event end must be after its start.'}
            )
        return attrs

    def create(self, validated_data):
        budget_items = validated_data.pop('budget_items', [])
        with transaction.atomic():
            event = GroupEvent.objects.create(**validated_data)
            GroupEventBudgetItem.objects.bulk_create([
                GroupEventBudgetItem(event=event, **item)
                for item in budget_items
            ])
        return event

    def update(self, instance, validated_data):
        budget_items = validated_data.pop('budget_items', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if budget_items is not None:
                instance.budget_items.all().delete()
                GroupEventBudgetItem.objects.bulk_create([
                    GroupEventBudgetItem(event=instance, **item)
                    for item in budget_items
                ])
        return instance