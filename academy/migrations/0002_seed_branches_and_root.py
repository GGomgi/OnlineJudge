from django.db import migrations

BRANCHES = [
    ("B001", "인천청라"),
    ("B002", "김포"),
    ("B003", "고양"),
    ("B004", "파주"),
]


def seed(apps, schema_editor):
    Branch = apps.get_model("academy", "Branch")
    for code, name in BRANCHES:
        Branch.objects.get_or_create(code=code, defaults={"name": name})

    User = apps.get_model("account", "User")
    AcademyProfile = apps.get_model("academy", "AcademyProfile")
    root = User.objects.filter(username="root").first()
    if root is not None:
        AcademyProfile.objects.get_or_create(user=root, defaults={"role": "HQ_ADMIN", "branch": None})


def unseed(apps, schema_editor):
    Branch = apps.get_model("academy", "Branch")
    Branch.objects.filter(code__in=[b[0] for b in BRANCHES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0001_initial"),
        ("account", "0012_userprofile_language"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
