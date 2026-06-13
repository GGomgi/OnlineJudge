from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academy", "0002_seed_branches_and_root"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupRequest",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_role", models.CharField(default="STUDENT", max_length=32)),
                ("applicant_name", models.CharField(max_length=64)),
                ("contact", models.CharField(blank=True, default="", max_length=32)),
                ("memo", models.TextField(blank=True, default="")),
                ("status", models.CharField(default="PENDING", max_length=16)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("reject_reason", models.TextField(blank=True, default="")),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("requested_branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="signup_requests", to="academy.branch")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_signups", to=settings.AUTH_USER_MODEL)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="signup_request", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_signup_request", "ordering": ["-create_time"]},
        ),
    ]
