from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0057_studentprofile_legacy_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonoccurrence",
            name="no_makeup_kind",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
