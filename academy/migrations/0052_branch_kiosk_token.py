from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0051_studentprofile_edit_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="branch",
            name="kiosk_token",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
