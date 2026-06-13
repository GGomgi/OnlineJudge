from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academy", "0003_signuprequest"),
    ]

    operations = [
        migrations.CreateModel(
            name="CourseClass",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=128)),
                ("track", models.CharField(blank=True, default="", max_length=16)),
                ("level", models.CharField(blank=True, default="", max_length=8)),
                ("is_active", models.BooleanField(default=True)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="classes", to="academy.branch")),
                ("instructor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="teaching_classes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_class", "ordering": ["branch_id", "name"]},
        ),
        migrations.CreateModel(
            name="ClassEnrollment",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("joined_at", models.DateTimeField(auto_now_add=True)),
                ("course_class", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enrollments", to="academy.courseclass")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="class_enrollments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_class_enrollment", "unique_together": {("course_class", "student")}},
        ),
        migrations.CreateModel(
            name="TimetableSlot",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day_of_week", models.PositiveSmallIntegerField()),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("room", models.CharField(blank=True, default="", max_length=64)),
                ("course_class", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="timetable_slots", to="academy.courseclass")),
            ],
            options={"db_table": "academy_timetable_slot", "ordering": ["day_of_week", "start_time"]},
        ),
    ]
