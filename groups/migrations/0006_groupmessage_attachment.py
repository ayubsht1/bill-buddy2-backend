import groups.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0005_groupevent_groupeventbudgetitem_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='groupmessage',
            name='attachment',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=groups.models.group_message_attachment_path,
            ),
        ),
        migrations.AddField(
            model_name='groupmessage',
            name='attachment_name',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
