from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expense', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='personalexpense',
            name='payment_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('CASH', 'Cash'),
                    ('CARD', 'Card'),
                    ('BANK_TRANSFER', 'Bank transfer'),
                    ('MOBILE_WALLET', 'Mobile wallet'),
                    ('OTHER', 'Other'),
                ],
                default='',
                max_length=20,
            ),
        ),
    ]
