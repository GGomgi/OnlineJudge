from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0006_lead_counseling_studentprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="purpose",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
        migrations.AddField(
            model_name="lead",
            name="purpose_detail",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
