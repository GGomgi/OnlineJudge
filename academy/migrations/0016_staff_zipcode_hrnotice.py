from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0015_staff_profile"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="zipcode",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.CreateModel(
            name="HRNotice",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(default="DEPENDENTS", max_length=32)),
                ("message", models.CharField(blank=True, default="", max_length=255)),
                ("is_read", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hr_notices", to="academy.branch")),
                ("staff", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hr_notices", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_hr_notice", "ordering": ["-create_time"]},
        ),
    ]
