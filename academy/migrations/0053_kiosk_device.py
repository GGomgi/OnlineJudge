import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academy", "0052_branch_kiosk_token"),
    ]

    operations = [
        migrations.CreateModel(
            name="KioskDevice",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("device_id", models.CharField(max_length=64)),
                ("label", models.CharField(blank=True, default="", max_length=64)),
                ("user_agent", models.CharField(blank=True, default="", max_length=255)),
                ("status", models.CharField(default="PENDING", max_length=16)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="kiosk_devices", to="academy.branch")),
            ],
            options={
                "db_table": "academy_kiosk_device",
                "ordering": ["-requested_at"],
                "unique_together": {("branch", "device_id")},
            },
        ),
    ]
