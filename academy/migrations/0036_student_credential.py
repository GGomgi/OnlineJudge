from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0035_counsel_reservation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StudentCredential",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("site", models.CharField(blank=True, default="", max_length=64)),
                ("login_id", models.CharField(blank=True, default="", max_length=128)),
                ("password", models.CharField(blank=True, default="", max_length=128)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="site_credentials", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_student_credential", "ordering": ["order", "id"]},
        ),
    ]
