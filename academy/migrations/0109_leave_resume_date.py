from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("academy", "0108_auto_20260907_0100")]

    operations = [
        migrations.AddField(
            model_name="studentstatuschange",
            name="resume_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="pending_resume_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
