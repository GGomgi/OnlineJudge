from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0029_daily_attendance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="optionitem",
            name="color",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="dailyattendance",
            name="note_tag",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.CreateModel(
            name="AttendanceChange",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("detail", models.CharField(blank=True, default="", max_length=255)),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("attendance", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="changes", to="academy.DailyAttendance")),
            ],
            options={"db_table": "academy_attendance_change", "ordering": ["-create_time"]},
        ),
    ]
