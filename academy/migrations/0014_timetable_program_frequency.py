from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0013_studentprofile_schedule_pending"),
    ]

    operations = [
        migrations.AddField(
            model_name="studenttimetable",
            name="program",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="studenttimetable",
            name="frequency",
            field=models.CharField(default="WEEKLY", max_length=16),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="programs",
            field=models.TextField(blank=True, default=""),
        ),
    ]
