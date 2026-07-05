from django.db import models
from groups.models import Group
from django.conf import settings

# Create your models here.
class Settlement(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="settlements")
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="settlements_made"
    )
    paid_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="settlements_received"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.paid_by.email} paid {self.paid_to.email} {self.amount}"


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.message
