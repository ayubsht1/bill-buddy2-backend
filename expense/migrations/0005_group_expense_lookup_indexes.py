from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expense', '0004_personalexpense_transaction_type_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='expense',
            index=models.Index(fields=['group', 'date'], name='expense_group_date_idx'),
        ),
        migrations.AddIndex(
            model_name='expenseshare',
            index=models.Index(fields=['expense', 'user'], name='share_exp_user_idx'),
        ),
        migrations.AddIndex(
            model_name='expenseshare',
            index=models.Index(fields=['user', 'expense'], name='share_user_exp_idx'),
        ),
    ]
