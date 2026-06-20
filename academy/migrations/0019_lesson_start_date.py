from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0018_timetable_change"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="lesson_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studenttimetable",
            name="active_from",
            field=models.DateField(blank=True, null=True),
        ),
    ]
