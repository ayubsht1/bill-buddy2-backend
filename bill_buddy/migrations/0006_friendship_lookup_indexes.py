from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bill_buddy', '0005_friendship'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='friendship',
            index=models.Index(fields=['to_user', 'status'], name='friendship_to_status_idx'),
        ),
        migrations.AddIndex(
            model_name='friendship',
            index=models.Index(fields=['from_user', 'status'], name='friendship_from_status_idx'),
        ),
    ]
