from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0047_msgtemplate_edit_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="parent_relation",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="notify_optin",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="guardian2_phone",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="guardian2_relation",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
