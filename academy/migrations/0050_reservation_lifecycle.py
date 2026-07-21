from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0049_lead_enroll_edit"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselreservation",
            name="channel",
            field=models.CharField(blank=True, default="VISIT", max_length=16),
        ),
        migrations.AddField(
            model_name="counselreservation",
            name="cancel_reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="counselreservation",
            name="completed_log",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="academy.CounselingLog"),
        ),
    ]
