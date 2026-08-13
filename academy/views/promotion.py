"""학년 올리기.

연 1~2회 하는 일이다. 봄(3월)에 대부분 올리고, 국제학교는 가을(9월)에 올린다.
같은 해에 두 번 하는 것이 정상일 수 있어(봄에 못 한 학생을 늦게) 막지 않는다.
대신 이미 올린 학생을 알려주고 사람이 골라서 진행한다.

학교가 바뀌는 두 지점(초6→중1, 중3→고1)은 학교 이름을 모르는 채로 올리게 된다.
이름은 비워 두고 '정보 미완료' 로 남겨, 배정 결과가 나오는 대로 채우게 한다.
"""
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from utils.api import APIView
from account.decorators import admin_role_required
from account.models import User

from ..models import (AcademyProfile, AcademyRole, StudentProfile, EnrollmentStatus,
                      PromotionBatch, PromotionItem, Branch)
from ..services import viewable_branch_ids, editable_branch_ids, can_manage_branch

# 학교급 순서 — 여기에 학년을 더해 하나의 숫자로 만들면 비교가 쉽다
LEVELS = ["ELEMENTARY", "MIDDLE", "HIGH", "UNIVERSITY"]
LEVEL_LABEL = {"ELEMENTARY": "초등학교", "MIDDLE": "중학교", "HIGH": "고등학교",
               "UNIVERSITY": "대학교"}
LEVEL_MAX = {"ELEMENTARY": 6, "MIDDLE": 3, "HIGH": 3, "UNIVERSITY": 4}
LEVEL_BASE = {"ELEMENTARY": 0, "MIDDLE": 6, "HIGH": 9, "UNIVERSITY": 12}


def kst_today():
    return (now() + __import__("datetime").timedelta(hours=9)).date()


def school_year(d=None):
    """학년도. 3월에 새 학년이 시작하므로 1~2월은 지난 학년도로 친다."""
    d = d or kst_today()
    return d.year if d.month >= 3 else d.year - 1


def as_num(level, grade):
    if level not in LEVEL_BASE or not grade:
        return None
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return None
    return LEVEL_BASE[level] + g


def peer_num(birth_date, d=None):
    """생년으로 본 또래. 빠른 생일 제도는 2009년 입학생부터 없어져 태어난 해만 본다."""
    if not birth_date:
        return None
    n = school_year(d) - birth_date.year - 6
    return n if n >= 1 else None


def next_step(level, grade):
    """다음 학년. 학교가 바뀌면 (새 학교급, 1학년, 학교이름 비움)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return None
    if level not in LEVELS:
        return None
    if g < LEVEL_MAX[level]:
        return {"level": level, "grade": g + 1, "school_changes": False}
    i = LEVELS.index(level)
    if i + 1 >= len(LEVELS):
        return None                     # 대학 4학년 — 여기서 끝
    return {"level": LEVELS[i + 1], "grade": 1, "school_changes": True}


def name_of(u):
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username if u else ""


class PromotionPreviewAPI(APIView):
    """진급 미리보기. 누가 어떻게 바뀌는지 보여 주고, 학생마다 고를 수 있게 한다."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        bid = request.GET.get("branch_id") or None
        qs = AcademyProfile.objects.filter(role=AcademyRole.STUDENT, is_deleted=False) \
                                   .select_related("user", "user__userprofile", "branch")
        if view is not None:
            qs = qs.filter(branch_id__in=view)
        if bid:
            qs = qs.filter(branch_id=int(bid))
        sy = school_year()
        # 이 학년도에 이미 올린 학생 — 막지 않고 알려만 준다
        done = {}
        for it in PromotionItem.objects.filter(
                batch__school_year=sy, batch__undone_at__isnull=True).select_related("batch"):
            done.setdefault(it.student_id, []).append(it.batch)

        groups, seniors = {}, []
        for p in qs:
            sp = getattr(p.user, "student_profile", None)
            if not sp or sp.enrollment_status == EnrollmentStatus.WITHDRAWN:
                continue
            lv, gr = sp.school_type, sp.grade
            cur = as_num(lv, gr)
            row = {
                "student_id": p.user_id, "name": name_of(p.user),
                "branch": (p.branch.name if p.branch_id else ""),
                "birth_date": str(sp.birth_date) if sp.birth_date else "",
                "school_type": lv, "school_type_label": LEVEL_LABEL.get(lv, lv),
                "school_name": sp.school_name, "grade": gr,
            }
            # 또래와 견주기 — 틀렸다는 뜻이 아니라 확인하라는 뜻
            pn = peer_num(sp.birth_date)
            if pn and cur:
                d = cur - pn
                row["peer"] = "같음" if d == 0 else ("%d년 빠름" % d if d > 0 else "%d년 늦음" % -d)
                row["peer_off"] = (d != 0)
                row["already"] = (d > 0)      # 또래보다 앞서면 이미 올린 것일 수 있다
            else:
                row["peer"] = ""
                row["peer_off"] = False
                row["already"] = False
            b = done.get(p.user_id)
            if b:
                row["done_note"] = "%s에 이미 진급함" % str(b[0].create_time + __import__("datetime").timedelta(hours=9))[:10]
            else:
                row["done_note"] = ""

            # 고3은 진급이 아니라 진로를 정해야 한다
            if lv == "HIGH" and str(gr) == "3":
                seniors.append(row)
                continue
            nx = next_step(lv, gr)
            if not nx:
                continue
            row["to_level"] = nx["level"]
            row["to_level_label"] = LEVEL_LABEL.get(nx["level"], nx["level"])
            row["to_grade"] = nx["grade"]
            row["school_changes"] = nx["school_changes"]
            key = "%s%s>%s%s" % (lv, gr, nx["level"], nx["grade"])
            g = groups.setdefault(key, {
                "key": key,
                "from_label": "%s %s학년" % (LEVEL_LABEL.get(lv, lv), gr),
                "to_label": "%s %s학년" % (LEVEL_LABEL.get(nx["level"], nx["level"]), nx["grade"]),
                "school_changes": nx["school_changes"], "rows": []})
            g["rows"].append(row)

        order = {lv: i for i, lv in enumerate(LEVELS)}
        out = sorted(groups.values(),
                     key=lambda g: (order.get(g["rows"][0]["school_type"], 9),
                                    int(g["rows"][0]["grade"] or 0)))
        for g in out:
            g["rows"].sort(key=lambda r: r["name"])
        seniors.sort(key=lambda r: r["name"])
        return self.success({
            "school_year": sy, "groups": out, "seniors": seniors,
            "count": sum(len(g["rows"]) for g in out),
        })


class PromotionAPI(APIView):
    """진급 실행·이력·되돌리기."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        qs = PromotionBatch.objects.select_related("actor", "actor__userprofile", "branch") \
                                   .prefetch_related("items", "items__student",
                                                     "items__student__userprofile")
        if view is not None:
            qs = qs.filter(Q(branch_id=None) | Q(branch_id__in=view))
        rows = []
        for b in qs[:50]:
            rows.append({
                "id": b.id, "season": b.season, "school_year": b.school_year,
                "branch": (b.branch.name if b.branch_id else ""),
                "actor": name_of(b.actor) if b.actor_id else "",
                "time": str(b.create_time + __import__("datetime").timedelta(hours=9))[:16],
                "count": b.items.count(),
                "undone": bool(b.undone_at),
                "undone_at": (str(b.undone_at + __import__("datetime").timedelta(hours=9))[:16]
                              if b.undone_at else ""),
                "items": [{
                    "name": name_of(i.student),
                    "old": "%s %s학년%s" % (LEVEL_LABEL.get(i.old_school_type, i.old_school_type),
                                          i.old_grade, (" · " + i.old_school_name) if i.old_school_name else ""),
                    "new": "%s %s학년%s" % (LEVEL_LABEL.get(i.new_school_type, i.new_school_type),
                                          i.new_grade, (" · " + i.new_school_name) if i.new_school_name else ""),
                    "action": i.action,
                } for i in b.items.all()[:400]],
            })
        return self.success(rows)

    @admin_role_required
    def post(self, request):
        """{season, items:[{student_id, action, school_name?}]} — 고른 학생만 처리한다."""
        d = request.data
        items = d.get("items") or []
        if not items:
            return self.error("올릴 학생을 고르세요.")
        season = d.get("season") or PromotionBatch.SPRING
        edit = editable_branch_ids(request.user)
        prof = getattr(request.user, "academy_profile", None)
        made = 0
        with transaction.atomic():
            batch = PromotionBatch.objects.create(
                season=season, school_year=school_year(),
                branch_id=(prof.branch_id if prof and prof.branch_id else None),
                actor=request.user, note=(d.get("note") or "")[:500])
            for it in items:
                u = User.objects.filter(id=it.get("student_id")).first()
                if not u:
                    continue
                sp = getattr(u, "student_profile", None)
                ap = getattr(u, "academy_profile", None)
                if not sp or not ap:
                    continue
                if edit is not None and ap.branch_id not in edit:
                    continue
                action = it.get("action") or "GRADE"
                old = (sp.school_type, sp.school_name, str(sp.grade or ""))
                if action == "DONE":
                    sp.enrollment_status = EnrollmentStatus.WITHDRAWN
                    sp.save(update_fields=["enrollment_status"])
                    PromotionItem.objects.create(
                        batch=batch, student=u, action="DONE",
                        old_school_type=old[0], old_school_name=old[1], old_grade=old[2],
                        new_school_type=old[0], new_school_name=old[1], new_grade=old[2])
                    made += 1
                    continue
                if action == "UNIV":
                    nx = {"level": "UNIVERSITY", "grade": 1, "school_changes": True}
                else:
                    nx = next_step(sp.school_type, sp.grade)
                if not nx:
                    continue
                # 학교가 바뀌면 이름은 비운다. 아는 이름을 적어 줬으면 그대로 채운다.
                new_name = (it.get("school_name") or "").strip()[:64]
                if nx["school_changes"] and not new_name:
                    new_name = ""
                elif not nx["school_changes"]:
                    new_name = new_name or sp.school_name
                sp.school_type = nx["level"]
                sp.grade = str(nx["grade"])
                sp.school_name = new_name
                sp.save(update_fields=["school_type", "grade", "school_name"])
                PromotionItem.objects.create(
                    batch=batch, student=u, action=("UNIV" if action == "UNIV" else "GRADE"),
                    old_school_type=old[0], old_school_name=old[1], old_grade=old[2],
                    new_school_type=nx["level"], new_school_name=new_name, new_grade=str(nx["grade"]))
                made += 1
        return self.success({"batch_id": batch.id, "made": made})

    @admin_role_required
    def delete(self, request):
        """되돌리기. 그 묶음으로 바꾼 학생만 이전 값으로 돌린다."""
        b = PromotionBatch.objects.filter(id=request.GET.get("id"), undone_at__isnull=True).first()
        if not b:
            return self.error("되돌릴 진급 기록이 없습니다.")
        if b.branch_id and not can_manage_branch(request.user, b.branch_id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        n = 0
        with transaction.atomic():
            for it in b.items.select_related("student"):
                sp = getattr(it.student, "student_profile", None)
                if not sp:
                    continue
                if it.action == "DONE":
                    sp.enrollment_status = EnrollmentStatus.ENROLLED
                    sp.save(update_fields=["enrollment_status"])
                    n += 1
                    continue
                # 그 뒤에 사람이 또 고쳤으면 건드리지 않는다(덮어쓰면 더 큰 사고)
                if (sp.school_type, str(sp.grade or "")) != (it.new_school_type, it.new_grade):
                    continue
                sp.school_type = it.old_school_type
                sp.grade = it.old_grade
                sp.school_name = it.old_school_name
                sp.save(update_fields=["school_type", "grade", "school_name"])
                n += 1
            b.undone_at = now()
            b.undone_by = request.user
            b.save(update_fields=["undone_at", "undone_by"])
        return self.success({"reverted": n, "total": b.items.count()})
