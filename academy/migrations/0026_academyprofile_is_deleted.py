from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0025_roles_managed_branches"),
    ]

    operations = [
        migrations.AddField(
            model_name="academyprofile",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
    ]
