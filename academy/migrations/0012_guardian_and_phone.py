from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0011_staff_no_and_student_timetable"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="academyprofile",
            name="phone",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.CreateModel(
            name="GuardianStudent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("relation", models.CharField(blank=True, default="학부모", max_length=16)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("parent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="children_links", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="guardian_links", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "academy_guardian_student",
                "unique_together": {("parent", "student")},
            },
        ),
    ]
