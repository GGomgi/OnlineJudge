from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0048_student_guardian_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="enroll_edit_log",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="lead",
            name="enroll_edited",
            field=models.BooleanField(default=False),
        ),
    ]
