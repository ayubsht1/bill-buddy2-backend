from django.conf import settings
import secrets
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

# Create your models here.

class Group(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    # The creator of the group
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_groups")
    # Many-to-many relationship tracking everyone inside the group (through GroupMembership)
    members = models.ManyToManyField(User, related_name="joined_groups", through='GroupMembership')
    # Alphanumeric code used by friends to join the group
    join_code = models.CharField(max_length=10, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Automatically generate a unique, clean uppercase join code if it doesn't exist
        if not self.join_code:
            while True:
                code = f"BB-{secrets.token_hex(3).upper()}" # e.g., BB-A4F39E
                if not Group.objects.filter(join_code=code).exists():
                    self.join_code = code
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
    
    def get_user_role(self, user):
        """Get the role of a user in this group"""
        try:
            membership = GroupMembership.objects.get(group=self, user=user)
            return membership.role
        except GroupMembership.DoesNotExist:
            return None
    
    def is_member(self, user):
        """Check if user is a member of this group"""
        return self.members.filter(id=user.id).exists()
    
    def is_owner(self, user):
        """Check if user is the owner of this group"""
        return self.creator == user
    
    def is_admin(self, user):
        """Check if user is an admin or owner of this group"""
        role = self.get_user_role(user)
        return role in ['owner', 'admin'] or self.creator == user


class GroupMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        ADMIN = 'admin', 'Admin'
        MEMBER = 'member', 'Member'
    
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_memberships')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('group', 'user')
    
    def __str__(self):
        return f"{self.user.username} - {self.group.name} ({self.role})"

# Add this to groups/models.py

class GroupMessage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="chat_messages")
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_messages")
    message = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    # --- 🚀 NEW CHAT FEATURE FIELDS ---
    # Reply: Self-referencing link to the original target message
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name="replies")
    
    # Forward & Pin flags
    is_forwarded = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    
    # Soft Delete: Keeps database intact but hides the text string copy from users
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        if self.is_deleted:
            return f"[{self.group.name}] Message deleted"
        sender_name = self.sender.username if self.sender else "SYSTEM"
        return f"[{self.group.name}] {sender_name}: {self.message[:30]}"
    
