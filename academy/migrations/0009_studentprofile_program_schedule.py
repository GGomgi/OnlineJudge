from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0008_studentprofile_consent"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="program",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="program_language",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="weekly_sessions",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="class_schedule",
            field=models.TextField(blank=True, default=""),
        ),
    ]
