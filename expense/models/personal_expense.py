from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class PersonalExpense(models.Model):
    # 🔄 New field options to handle cash flow direction
    TRANSACTION_TYPES = [
        ('EXPENSE', 'Expense'),
        ('INCOME', 'Income'),
    ]

    CATEGORY_CHOICES = [
        # Expense Categories
        ('FOOD', 'Food & Dining'),
        ('SHOPPING', 'Shopping'),
        ('UTILITIES', 'Bills & Utilities'),
        ('TRANSPORT', 'Transportation'),
        ('ENTERTAINMENT', 'Entertainment'),
        ('GROCERIES', 'Groceries'),
        
        # 💵 Income Categories
        ('SALARY', 'Salary / Paycheck'),
        ('FREELANCE', 'Freelance / Side Hustle'),
        ('INVESTMENT', 'Investments / Interest'),
        ('GIFT', 'Gifts / Allowances'),
        
        ('OTHER', 'Other'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="personal_expenses")
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, default='EXPENSE') # Added this!
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='OTHER')
    date = models.DateField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.transaction_type} | {self.description} (${self.amount})"