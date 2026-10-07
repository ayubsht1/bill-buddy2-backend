from django.conf import settings
import secrets
from django.db import models
from django.contrib.auth import get_user_model
from django.db.models import F, Q
from django.core.validators import MinValueValidator
from pathlib import Path
import uuid

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


def group_message_attachment_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"group_messages/{instance.group_id}/{uuid.uuid4().hex}{extension}"


# Add this to groups/models.py

class GroupMessage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="chat_messages")
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_messages")
    message = models.TextField()
    attachment = models.FileField(upload_to=group_message_attachment_path, blank=True, null=True)
    attachment_name = models.CharField(max_length=255, blank=True)
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


class GroupEvent(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='events')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_group_events',
    )
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['starts_at', 'id']
        indexes = [
            models.Index(fields=['group', 'starts_at'], name='event_group_start_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F('starts_at')),
                name='event_end_after_start',
            ),
        ]

    @property
    def planned_budget(self):
        return sum(
            (item.amount for item in self.budget_items.all()),
            start=0,
        )

    def __str__(self):
        return f"{self.title} - {self.group.name}"


class GroupEventBudgetItem(models.Model):
    event = models.ForeignKey(
        GroupEvent,
        on_delete=models.CASCADE,
        related_name='budget_items',
    )
    description = models.CharField(max_length=120)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.description} - {self.amount}"
    
