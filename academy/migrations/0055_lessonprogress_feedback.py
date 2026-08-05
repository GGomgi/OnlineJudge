from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0054_studenttimetable_active_until"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonprogress",
            name="feedback",
            field=models.TextField(blank=True, default=""),
        ),
    ]
