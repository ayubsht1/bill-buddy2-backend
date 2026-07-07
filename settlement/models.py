from django.db import models
from groups.models import Group
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()

# Create your models here.
class Settlement(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="settlements")
    paid_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="settlements_paid")
    paid_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name="settlements_received")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.paid_by.username} paid ${self.amount} to {self.paid_to.username} in {self.group.name}"


# class Notification(models.Model):
#     user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
#     message = models.TextField()
#     is_read = models.BooleanField(default=False)
#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return self.message
