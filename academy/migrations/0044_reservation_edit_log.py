from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0043_msg_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="counselreservation",
            name="edit_log",
            field=models.TextField(blank=True, default=""),
        ),
    ]
