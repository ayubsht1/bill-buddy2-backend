from django.db import models
from django.conf import settings
from groups.models import Group


class Expense(models.Model):
    class SplitType(models.TextChoices):
        EQUAL = 'EQUAL', 'Equal'
        EXACT = 'EXACT', 'Exact'
        PERCENT = 'PERCENT', 'Percent'

    group = models.ForeignKey('groups.Group', on_delete=models.CASCADE, related_name='expenses')
    description = models.CharField(max_length=255)
    amount = models.DecimalField(decimal_places=2, max_digits=10)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='expenses_paid')
    date = models.DateField(auto_now_add=True)
    
    # 🚀 Add these historical configuration fields
    split_type = models.CharField(max_length=10, choices=SplitType.choices, default=SplitType.EQUAL)
    split_data = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.description} - ${self.amount}"
    
class ExpenseShare(models.Model):
    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name="shares")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shares")
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.user.email} owes {self.amount} for {self.expense.description}"

# class ExpenseComment(models.Model):
#     expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name="comments")
#     user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
#     content = models.TextField()
#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"{self.user.email} on {self.expense.description}"
