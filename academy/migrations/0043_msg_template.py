from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0042_lead_enroll_link"),
    ]

    operations = [
        migrations.CreateModel(
            name="MsgTemplateGroup",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_hidden", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "academy_msg_template_group", "ordering": ["order", "id"]},
        ),
        migrations.CreateModel(
            name="MsgTemplate",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=120)),
                ("body", models.TextField(blank=True, default="")),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_hidden", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("group", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="templates", to="academy.MsgTemplateGroup")),
            ],
            options={"db_table": "academy_msg_template", "ordering": ["order", "id"]},
        ),
    ]
