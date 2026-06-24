from django.db import migrations


def apply(apps, schema_editor):
    # 상태 단순화: '상담중(COUNSELING)'을 '상담(NEW)'으로 통합.
    # 이후 상태는 상담(NEW) / 등록완료(CONVERTED) / 종결(CLOSED)만 사용,
    # '상담예약중'은 미래 예약 유무로 화면에서 자동 표시(저장 상태 아님).
    Lead = apps.get_model("academy", "Lead")
    Lead.objects.filter(status="COUNSELING").update(status="NEW")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0033_attendance_note_options"),
    ]

    operations = [
        migrations.RunPython(apply, noop),
    ]
