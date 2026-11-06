from django.utils import timezone
from rest_framework import serializers

from groups.models import Group, GroupEvent
from expense.models import Expense
from .models import CATEGORY_CHOICES, Notification, NotificationPreference, Reminder


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ['id', 'category', 'title', 'message', 'url', 'created_at', 'read_at', 'is_read']


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['categories', 'updated_at']
        read_only_fields = ['updated_at']

    def validate_categories(self, categories):
        valid_categories = {key for key, _ in CATEGORY_CHOICES}
        if not isinstance(categories, dict) or set(categories) - valid_categories:
            raise serializers.ValidationError('Provide only supported notification categories.')
        merged_categories = self.instance.categories.copy() if self.instance else {}
        for category, channels in categories.items():
            merged_categories[category] = {
                **merged_categories.get(category, {}),
                **channels,
            }
        categories = merged_categories
        for category, channels in categories.items():
            if not isinstance(channels, dict) or set(channels) - {'in_app', 'email'}:
                raise serializers.ValidationError(f'Invalid preferences for {category}.')
            if any(not isinstance(value, bool) for value in channels.values()):
                raise serializers.ValidationError('Preference values must be boolean.')
        categories['security'] = {'in_app': True, 'email': True}
        return categories


class ReminderSerializer(serializers.ModelSerializer):
    group_id = serializers.UUIDField(write_only=True)
    expense_id = serializers.UUIDField(write_only=True, required=False)
    event_id = serializers.UUIDField(write_only=True, required=False)
    target_user_id = serializers.UUIDField(write_only=True, required=False)
    group = serializers.UUIDField(source='group.public_id', read_only=True)
    expense = serializers.UUIDField(source='expense.public_id', read_only=True, allow_null=True)
    event = serializers.UUIDField(source='event.public_id', read_only=True, allow_null=True)
    target_user = serializers.UUIDField(source='target_user.public_id', read_only=True, allow_null=True)

    class Meta:
        model = Reminder
        fields = [
            'id', 'group', 'group_id', 'target_type', 'expense', 'expense_id',
            'event', 'event_id', 'target_user', 'target_user_id',
            'scheduled_at', 'status', 'created_at', 'sent_at',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'sent_at']

    def validate(self, attrs):
        request = self.context['request']
        group_id = attrs.get('group_id')
        group = Group.objects.filter(public_id=group_id).first() if group_id else (
            self.instance.group if self.instance else None
        )
        if group is None or not group.members.filter(pk=request.user.pk).exists():
            raise serializers.ValidationError({'group_id': 'Choose a group you belong to.'})
        target_type = attrs.get('target_type', self.instance.target_type if self.instance else None)
        if target_type == Reminder.TargetType.EXPENSE:
            expense_id = attrs.get('expense_id')
            expense = Expense.objects.filter(
                public_id=expense_id,
                group=group,
            ).first() if expense_id else (self.instance.expense if self.instance else None)
            if expense is None:
                raise serializers.ValidationError({'expense_id': 'Choose an expense in this group.'})
            attrs['expense'] = expense
            attrs['event'] = None
            attrs['target_user'] = None
        elif target_type == Reminder.TargetType.EVENT:
            event_id = attrs.get('event_id')
            event = GroupEvent.objects.filter(
                public_id=event_id,
                group=group,
            ).first() if event_id else (self.instance.event if self.instance else None)
            if event is None:
                raise serializers.ValidationError({'event_id': 'Choose an event in this group.'})
            attrs['event'] = event
            attrs['expense'] = None
            attrs['target_user'] = None
        elif target_type == Reminder.TargetType.SETTLEMENT:
            target_user_id = attrs.get('target_user_id')
            target_user = group.members.filter(
                public_id=target_user_id,
            ).first() if target_user_id else (self.instance.target_user if self.instance else None)
            if target_user is None:
                raise serializers.ValidationError({'target_user_id': 'Choose a member of this group.'})
            from expense.utils import group_net_balances

            if group_net_balances(group).get(target_user.id, 0) >= 0:
                raise serializers.ValidationError({'target_user_id': 'The selected member has no pending amount to settle.'})
            attrs['target_user'] = target_user
            attrs['expense'] = None
            attrs['event'] = None
        else:
            raise serializers.ValidationError({'target_type': 'Choose expense, event, or settlement.'})
        scheduled_at = attrs.get('scheduled_at', self.instance.scheduled_at if self.instance else None)
        if scheduled_at and scheduled_at <= timezone.now():
            raise serializers.ValidationError({'scheduled_at': 'Choose a future date and time.'})
        attrs['group'] = group
        attrs['recipient'] = request.user
        return attrs

    def create(self, validated_data):
        for key in ('group_id', 'expense_id', 'event_id', 'target_user_id'):
            validated_data.pop(key, None)
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        for key in ('group_id', 'expense_id', 'event_id', 'target_user_id'):
            validated_data.pop(key, None)
        if instance.status != Reminder.Status.PENDING:
            raise serializers.ValidationError('Only pending reminders can be edited.')
        return super().update(instance, validated_data)
