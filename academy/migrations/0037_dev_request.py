from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0036_student_credential"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DevRequest",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("body", models.TextField(blank=True, default="")),
                ("status", models.CharField(default="NONE", max_length=16)),
                ("is_hidden", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="dev_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_dev_request", "ordering": ["-create_time"]},
        ),
        migrations.CreateModel(
            name="DevRequestComment",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField()),
                ("is_hidden", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="academy.DevRequest")),
            ],
            options={"db_table": "academy_dev_request_comment", "ordering": ["create_time"]},
        ),
    ]
