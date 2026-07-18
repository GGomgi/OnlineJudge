from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0046_fixed_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="msgtemplate",
            name="edit_log",
            field=models.TextField(blank=True, default=""),
        ),
    ]
