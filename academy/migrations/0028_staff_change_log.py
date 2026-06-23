from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0027_studentprofile_enroll_no"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffChangeLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("change_type", models.CharField(max_length=16)),
                ("detail", models.CharField(blank=True, default="", max_length=255)),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staff_changes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_staff_change_log", "ordering": ["-create_time"]},
        ),
    ]
