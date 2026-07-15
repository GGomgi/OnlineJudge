from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0044_reservation_edit_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="edit_log",
            field=models.TextField(blank=True, default=""),
        ),
    ]
