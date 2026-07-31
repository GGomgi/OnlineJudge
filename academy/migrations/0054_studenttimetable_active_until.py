from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0053_kiosk_device"),
    ]

    operations = [
        migrations.AddField(
            model_name="studenttimetable",
            name="active_until",
            field=models.DateField(blank=True, null=True),
        ),
    ]
