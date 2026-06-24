from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0030_option_color_attendance_note"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LessonOccurrence",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("start_time", models.TimeField()),
                ("duration_minutes", models.PositiveSmallIntegerField(default=60)),
                ("program", models.CharField(blank=True, default="", max_length=32)),
                ("subject", models.CharField(blank=True, default="", max_length=64)),
                ("status", models.CharField(default="SCHEDULED", max_length=16)),
                ("is_makeup", models.BooleanField(default=False)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="academy.Branch")),
                ("instructor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("makeup_for", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="makeups", to="academy.LessonOccurrence")),
                ("source_timetable", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="occurrences", to="academy.StudentTimetable")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lesson_occurrences", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_lesson_occurrence", "ordering": ["date", "start_time"]},
        ),
        migrations.AlterUniqueTogether(
            name="lessonoccurrence",
            unique_together={("source_timetable", "date")},
        ),
    ]
