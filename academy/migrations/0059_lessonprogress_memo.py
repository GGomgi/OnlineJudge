from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0058_occurrence_no_makeup_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonprogress",
            name="memo",
            field=models.TextField(blank=True, default=""),
        ),
    ]
