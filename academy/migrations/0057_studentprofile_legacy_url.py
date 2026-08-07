from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0056_branch_kiosk_pin"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="legacy_url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
