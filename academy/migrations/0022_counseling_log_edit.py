from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0021_counsel_softdelete_prefs"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CounselingLogEdit",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("old_summary", models.TextField(blank=True, default="")),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("log", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="edits", to="academy.counselinglog")),
            ],
            options={"db_table": "academy_counseling_log_edit", "ordering": ["-create_time"]},
        ),
    ]
