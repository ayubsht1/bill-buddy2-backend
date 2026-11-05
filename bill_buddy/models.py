import uuid

from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin, Permission
from django.conf import settings
from django.utils import timezone


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)

class CustomUser(AbstractBaseUser, PermissionsMixin):
    CURRENCY_CHOICES = [
        ('NPR', 'Nepalese Rupee'),
        ('USD', 'US Dollar'),
        ('EUR', 'Euro'),
        ('GBP', 'British Pound'),
        ('INR', 'Indian Rupee'),
        ('JPY', 'Japanese Yen'),
        ('AUD', 'Australian Dollar'),
        ('CAD', 'Canadian Dollar'),
        ('CHF', 'Swiss Franc'),
        ('CNY', 'Chinese Yuan'),
        ('SGD', 'Singapore Dollar'),
        ('AED', 'UAE Dirham'),
        ('BDT', 'Bangladeshi Taka'),
        ('PKR', 'Pakistani Rupee'),
    ]

    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, unique=True)
    first_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150, blank=True, null=True)
    profile_picture = models.CharField(max_length=500, blank=True, null=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='NPR')
    language = models.CharField(max_length=5, choices=[('en', 'English'), ('ne', 'नेपाली')], default='en')
    timezone = models.CharField(max_length=64, default='Asia/Kathmandu')
    groups = models.ManyToManyField(
        'groups.Group',
        related_name='customuser_set',
        blank=True,
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name='customuser_set',
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'  # Still login via email
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']

    class Meta:
        # 🌟 This forces Django to use "User" instead of "Custom user" everywhere
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.email


class PasswordResetToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='password_reset_tokens')
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 60 * 10

# models.py
class EmailVerificationToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_verification_tokens')
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 60 * 10
     
class Friendship(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        ACCEPTED = 'ACCEPTED', 'Accepted'

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='sent_friend_requests'
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='received_friend_requests'
    )
    status = models.CharField(
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('from_user', 'to_user')
        indexes = [
            models.Index(fields=['to_user', 'status'], name='friendship_to_status_idx'),
            models.Index(fields=['from_user', 'status'], name='friendship_from_status_idx'),
        ]

    def __str__(self):
        return f"{self.from_user.email} -> {self.to_user.email} ({self.status})"


class PersonalMessage(models.Model):
    """Personal messages between individual friends, not tied to groups or expenses."""
    
    class MessageType(models.TextChoices):
        TEXT = 'text', 'Text'
        EMOTICON = 'emoticon', 'Emoticon'
        ATTACHMENT = 'attachment', 'Attachment'
    
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_personal_messages"
    )
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_personal_messages"
    )
    message = models.TextField(
        help_text="The text content of the personal message (no expense features)"
    )
    attachment = models.FileField(
        upload_to="personal_messages/%Y/%m/%d/",
        blank=True,
        null=True
    )
    attachment_name = models.CharField(max_length=255, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT
    )
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['sender', 'receiver', 'is_read']),
            models.Index(fields=['receiver', 'is_read']),
        ]

    # Reply: Self-referencing link to the original target message
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies"
    )

    def __str__(self):
        return f"Personal: {self.sender.username} -> {self.receiver.username}: {self.message[:30]}"


class UserSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LOGGED_OUT = "logged_out", "Logged out"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_sessions",
    )
    refresh_jti = models.CharField(max_length=255, unique=True)
    device = models.CharField(max_length=100, blank=True)
    browser = models.CharField(max_length=100, blank=True)
    operating_system = models.CharField(max_length=100, blank=True)
    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    login_at = models.DateTimeField(default=timezone.now)
    last_activity = models.DateTimeField(default=timezone.now)
    logout_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    class Meta:
        ordering = ["-login_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="user_session_status_idx"),
            models.Index(fields=["user", "-login_at"], name="user_session_login_idx"),
        ]

    def __str__(self):
        return f"{self.user.email} ({self.device or 'Unknown device'})"