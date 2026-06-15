from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_staff_no(apps, schema_editor):
    """기존 직원(교직원 역할) 계정에 사번을 부여한다. 전지점 역할은 00 prefix."""
    AcademyProfile = apps.get_model("academy", "AcademyProfile")
    STAFF = {"HQ_ADMIN", "HR_ADMIN", "BRANCH_MANAGER", "INSTRUCTOR", "TA", "EXTERNAL_INSTRUCTOR_ADMIN"}
    ALL_BRANCH = {"HQ_ADMIN", "HR_ADMIN"}

    def prefix_for(profile):
        if profile.role in ALL_BRANCH or not profile.branch_id:
            return "00"
        code = profile.branch.code if profile.branch else ""
        digits = "".join(ch for ch in code if ch.isdigit())
        n = int(digits) if digits else 0
        return "%02d" % (n % 100)

    seq = {}
    # 기존 사번 최대 일련 수집
    for p in AcademyProfile.objects.exclude(staff_no=""):
        pre, tail = p.staff_no[:2], p.staff_no[2:]
        if tail.isdigit():
            seq[pre] = max(seq.get(pre, 0), int(tail))

    for p in AcademyProfile.objects.filter(staff_no="").select_related("branch"):
        if p.role not in STAFF:
            continue
        pre = prefix_for(p)
        seq[pre] = seq.get(pre, 0) + 1
        p.staff_no = "%s%03d" % (pre, seq[pre])
        p.save(update_fields=["staff_no"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0010_optionitem_and_program_custom"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="academyprofile",
            name="staff_no",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.CreateModel(
            name="StudentTimetable",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("class_type", models.CharField(default="PRIVATE", max_length=16)),
                ("weekday", models.PositiveSmallIntegerField()),
                ("start_time", models.TimeField()),
                ("duration_minutes", models.PositiveSmallIntegerField(default=60)),
                ("subject", models.CharField(blank=True, default="", max_length=64)),
                ("room", models.CharField(blank=True, default="", max_length=64)),
                ("status", models.CharField(default="ACTIVE", max_length=16)),
                ("create_time", models.DateTimeField(auto_now_add=True)),
                ("update_time", models.DateTimeField(auto_now=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="student_timetables", to="academy.branch")),
                ("instructor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="instructing_timetables", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="timetables", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "academy_student_timetable",
                "ordering": ["weekday", "start_time"],
            },
        ),
        migrations.RunPython(backfill_staff_no, noop),
    ]
