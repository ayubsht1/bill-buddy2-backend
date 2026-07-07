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
    # Many-to-many relationship tracking everyone inside the group
    members = models.ManyToManyField(User, related_name="joined_groups")
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
    

# Add this to groups/models.py

class GroupMessage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="chat_messages")
    # If user is NULL, it's a "SYSTEM" automated update message
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_messages")
    message = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        sender_name = self.sender.username if self.sender else "SYSTEM"
        return f"[{self.group.name}] {sender_name}: {self.message[:30]}"
    

# class Membership(models.Model):
#     user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
#     group = models.ForeignKey(Group, on_delete=models.CASCADE)
#     role = models.CharField(max_length=20, choices=[('admin', 'Admin'), ('member', 'Member')])
#     joined_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         unique_together = ('user', 'group')
