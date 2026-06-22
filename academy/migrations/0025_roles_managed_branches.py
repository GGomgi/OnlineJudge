from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0024_student_status_change"),
    ]

    operations = [
        migrations.AddField(
            model_name="academyprofile",
            name="managed_branches",
            field=models.ManyToManyField(blank=True, related_name="regional_managers", to="academy.Branch"),
        ),
    ]
