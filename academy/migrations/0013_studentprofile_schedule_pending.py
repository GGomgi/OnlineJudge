from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0012_guardian_and_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="schedule_pending",
            field=models.BooleanField(default=False),
        ),
    ]
