from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0026_academyprofile_is_deleted"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="enroll_no",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
