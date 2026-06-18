from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0017_staff_documents_history"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TimetableChange",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=16)),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("detail", models.CharField(blank=True, default="", max_length=255)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="timetable_changes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_timetable_change", "ordering": ["-create_time"]},
        ),
    ]
