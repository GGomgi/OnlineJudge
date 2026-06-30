from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0039_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonoccurrence",
            name="time_change_reason",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
