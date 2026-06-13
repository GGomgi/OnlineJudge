from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academy", "0005_classsession_attendance"),
    ]

    operations = [
        migrations.CreateModel(
            name="Lead",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("parent_name", models.CharField(max_length=64)),
                ("parent_phone", models.CharField(max_length=32)),
                ("student_name", models.CharField(max_length=64)),
                ("school_type", models.CharField(blank=True, default="", max_length=16)),
                ("school_name", models.CharField(blank=True, default="", max_length=64)),
                ("grade", models.CharField(blank=True, default="", max_length=16)),
                ("interest", models.TextField(blank=True, default="")),
                ("contact_preference", models.CharField(blank=True, default="PHONE_OK", max_length=24)),
                ("status", models.CharField(default="NEW", max_length=16)),
                ("close_reason", models.CharField(blank=True, default="", max_length=255)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="leads", to="academy.branch")),
                ("converted_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="converted_from_lead", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_lead", "ordering": ["-create_time"]},
        ),
        migrations.CreateModel(
            name="CounselingLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(blank=True, default="VISIT", max_length=16)),
                ("summary", models.TextField()),
                ("next_contact_at", models.DateField(blank=True, null=True)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="counseling_logs", to=settings.AUTH_USER_MODEL)),
                ("lead", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="logs", to="academy.lead")),
            ],
            options={"db_table": "academy_counseling_log", "ordering": ["-create_time"]},
        ),
        migrations.CreateModel(
            name="StudentProfile",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("birth_date", models.DateField(blank=True, null=True)),
                ("address", models.CharField(blank=True, default="", max_length=255)),
                ("student_phone", models.CharField(blank=True, default="", max_length=32)),
                ("parent_name", models.CharField(blank=True, default="", max_length=64)),
                ("parent_phone", models.CharField(blank=True, default="", max_length=32)),
                ("school_type", models.CharField(blank=True, default="", max_length=16)),
                ("school_name", models.CharField(blank=True, default="", max_length=64)),
                ("grade", models.CharField(blank=True, default="", max_length=16)),
                ("enrollment_date", models.DateField(blank=True, null=True)),
                ("enrollment_status", models.CharField(default="ENROLLED", max_length=16)),
                ("memo", models.TextField(blank=True, default="")),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="student_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_student_profile"},
        ),
    ]
