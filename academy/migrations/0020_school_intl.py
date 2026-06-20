from django.db import migrations


def add_intl(apps, schema_editor):
    OptionItem = apps.get_model("academy", "OptionItem")
    if not OptionItem.objects.filter(category="school_type", value="INTL").exists():
        order = OptionItem.objects.filter(category="school_type").count()
        OptionItem.objects.create(category="school_type", value="INTL",
                                  label="국제학교", order=order)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0019_lesson_start_date"),
    ]

    operations = [
        migrations.RunPython(add_intl, noop),
    ]
