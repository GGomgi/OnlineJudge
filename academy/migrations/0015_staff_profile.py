from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0014_timetable_program_frequency"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffProfile",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("address", models.CharField(blank=True, default="", max_length=255)),
                ("address_detail", models.CharField(blank=True, default="", max_length=255)),
                ("phone", models.CharField(blank=True, default="", max_length=32)),
                ("resident_copy", models.CharField(blank=True, default="", max_length=255)),
                ("bankbook_copy", models.CharField(blank=True, default="", max_length=255)),
                ("graduation_cert", models.CharField(blank=True, default="", max_length=255)),
                ("transcript", models.CharField(blank=True, default="", max_length=255)),
                ("dependents_decided", models.BooleanField(default=False)),
                ("dependents", models.TextField(blank=True, default="")),
                ("emergency_contacts", models.TextField(blank=True, default="")),
                ("sex_offense_consent", models.BooleanField(default=False)),
                ("sex_offense_signature", models.TextField(blank=True, default="")),
                ("sex_offense_date", models.DateField(blank=True, null=True)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="staff_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academy_staff_profile"},
        ),
    ]
