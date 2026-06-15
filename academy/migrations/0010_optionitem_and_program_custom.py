from django.db import migrations, models


SEED = {
    "program": [
        ("LANG", "프로그래밍언어", False),
        ("WEB", "웹", False),
        ("PROJECT", "프로젝트", False),
        ("COMPETITION", "대회", False),
        ("CUSTOM", "개인맞춤", True),
    ],
    "program_language": [
        ("Python", "Python", False),
        ("C", "C", False),
        ("C++", "C++", False),
        ("Java", "Java", False),
        ("C#", "C#", False),
    ],
    "school_type": [
        ("ELEMENTARY", "초등", False),
        ("MIDDLE", "중등", False),
        ("HIGH", "고등", False),
        ("UNIVERSITY", "대학생", False),
        ("ETC", "기타", False),
    ],
    "counseling_purpose": [
        ("SELF_DEV", "자기개발(흥미 및 취미)", False),
        ("ADMISSION", "입시", False),
        ("COMPETITION", "대회", False),
        ("CAREER", "진로선택(개발자)", False),
        ("ETC", "직접 입력", True),
    ],
}


def seed_options(apps, schema_editor):
    OptionItem = apps.get_model("academy", "OptionItem")
    for category, items in SEED.items():
        for order, (value, label, allow_custom) in enumerate(items):
            OptionItem.objects.get_or_create(
                category=category, value=value,
                defaults={"label": label, "order": order, "allow_custom": allow_custom},
            )


def unseed_options(apps, schema_editor):
    OptionItem = apps.get_model("academy", "OptionItem")
    OptionItem.objects.filter(category__in=list(SEED.keys())).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0009_studentprofile_program_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="program_custom",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.CreateModel(
            name="OptionItem",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(max_length=32)),
                ("value", models.CharField(max_length=32)),
                ("label", models.CharField(max_length=64)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("allow_custom", models.BooleanField(default=False)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "academy_option_item",
                "ordering": ["category", "order", "id"],
                "unique_together": {("category", "value")},
            },
        ),
        migrations.RunPython(seed_options, unseed_options),
    ]
