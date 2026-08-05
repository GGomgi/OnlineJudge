from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0055_lessonprogress_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="branch",
            name="kiosk_pin",
            field=models.CharField(blank=True, default="", max_length=6),
        ),
    ]
