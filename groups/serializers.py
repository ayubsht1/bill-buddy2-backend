from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Group, GroupMessage

User = get_user_model()

class GroupMemberSerializer(serializers.ModelSerializer):
    """Provides minimal, clean user detail fields for group listings."""
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class GroupSerializer(serializers.ModelSerializer):
    creator = GroupMemberSerializer(read_only=True)
    members = GroupMemberSerializer(many=True, read_only=True)
    join_code = serializers.CharField(read_only=True)

    class Meta:
        model = Group
        fields = ['id', 'name', 'description', 'creator', 'members', 'join_code', 'created_at']


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