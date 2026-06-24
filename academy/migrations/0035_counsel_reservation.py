from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0034_lead_status_simplify"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CounselReservation",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scheduled_at", models.DateTimeField()),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("status", models.CharField(default="ACTIVE", max_length=16)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("lead", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reservations", to="academy.Lead")),
            ],
            options={"db_table": "academy_counsel_reservation", "ordering": ["scheduled_at"]},
        ),
    ]
