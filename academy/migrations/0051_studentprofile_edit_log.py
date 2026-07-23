from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0050_reservation_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="edit_log",
            field=models.TextField(blank=True, default=""),
        ),
    ]
