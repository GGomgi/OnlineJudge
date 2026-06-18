from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0016_staff_zipcode_hrnotice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="file_uploaded_at",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="StaffDocument",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("group", models.CharField(blank=True, default="", max_length=64)),
                ("title", models.CharField(blank=True, default="", max_length=128)),
                ("url", models.CharField(max_length=255)),
                ("doc_date", models.DateField(blank=True, null=True)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("visible_to_staff", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploaded_staff_documents", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staff_documents", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_staff_document", "ordering": ["group", "order", "id"]},
        ),
        migrations.CreateModel(
            name="StaffProfileHistory",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("field", models.CharField(max_length=64)),
                ("old_value", models.TextField(blank=True, default="")),
                ("new_value", models.TextField(blank=True, default="")),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staff_history", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_staff_history", "ordering": ["-create_time"]},
        ),
    ]
