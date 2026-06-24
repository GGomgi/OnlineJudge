from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0031_lesson_occurrence"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonoccurrence",
            name="no_makeup",
            field=models.BooleanField(default=False),
        ),
    ]
