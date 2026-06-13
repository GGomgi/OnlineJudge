from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academy", "0004_courseclass_enrollment_timetable"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassSession",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("start_time", models.TimeField(blank=True, null=True)),
                ("end_time", models.TimeField(blank=True, null=True)),
                ("status", models.CharField(default="SCHEDULED", max_length=16)),
                ("topic", models.CharField(blank=True, default="", max_length=255)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("course_class", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sessions", to="academy.courseclass")),
            ],
            options={"db_table": "academy_class_session", "ordering": ["-date", "start_time"],
                     "unique_together": {("course_class", "date", "start_time")}},
        ),
        migrations.CreateModel(
            name="AttendanceRecord",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(default="PRESENT", max_length=16)),
                ("memo", models.CharField(blank=True, default="", max_length=255)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attendances", to="academy.classsession")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attendance_records", to=settings.AUTH_USER_MODEL)),
                ("marked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marked_attendances", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_attendance_record",
                     "unique_together": {("session", "student")}},
        ),
    ]
