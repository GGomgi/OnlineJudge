from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0028_staff_change_log"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyAttendance",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("check_in_at", models.DateTimeField(blank=True, null=True)),
                ("check_out_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="academy.Branch")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="daily_attendances", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_daily_attendance"},
        ),
        migrations.AlterUniqueTogether(
            name="dailyattendance",
            unique_together={("student", "date")},
        ),
    ]
