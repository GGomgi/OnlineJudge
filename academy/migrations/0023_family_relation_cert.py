from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0022_counseling_log_edit"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="family_relation_cert",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
