import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expense', '0005_group_expense_lookup_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='personalexpense',
            name='date',
            field=models.DateField(default=django.utils.timezone.now),
        ),
    ]
