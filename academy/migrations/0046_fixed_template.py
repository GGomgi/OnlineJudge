from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academy", "0045_lead_edit_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="FixedTemplate",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=32)),
                ("body", models.TextField(blank=True, default="")),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fixed_templates", to="academy.Branch")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "academy_fixed_template",
                "unique_together": {("branch", "key")},
            },
        ),
    ]
