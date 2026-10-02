from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settlement', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='settlement',
            index=models.Index(fields=['group', 'paid_by'], name='settle_group_payer_idx'),
        ),
        migrations.AddIndex(
            model_name='settlement',
            index=models.Index(fields=['group', 'date'], name='settle_group_date_idx'),
        ),
    ]
