from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0041_lesson_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="enroll_token",
            field=models.CharField(blank=True, db_index=True, default="", max_length=48),
        ),
        migrations.AddField(
            model_name="lead",
            name="enroll_token_expires",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lead",
            name="enroll_status",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="lead",
            name="enroll_data",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="lead",
            name="enroll_submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
