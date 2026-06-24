from django.db import migrations

CATEGORY = "attendance_note"
# (value, label, color, order) — 출결 비고 표시 태그
DESIRED = [
    ("LATE_EXPECTED", "지각예정", "#f59e0b", 1),
    ("ABSENT", "결석", "#ef4444", 2),
    ("EARLY_LEAVE_EXPECTED", "조퇴예정", "#f97316", 3),
    ("TIME_CHANGE", "시간변동", "#3b82f6", 4),
    ("ETC", "기타", "#64748b", 5),
]


def apply(apps, schema_editor):
    OptionItem = apps.get_model("academy", "OptionItem")
    keep = set()
    for value, label, color, order in DESIRED:
        keep.add(value)
        OptionItem.objects.update_or_create(
            category=CATEGORY, value=value,
            defaults={"label": label, "color": color, "order": order, "is_active": True})
    # 원하는 집합 외의 기존 비고 태그는 숨김(이력 보존 위해 삭제하지 않음)
    OptionItem.objects.filter(category=CATEGORY).exclude(value__in=keep).update(is_active=False)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0032_occurrence_no_makeup"),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]
