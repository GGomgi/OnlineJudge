"""학원 관리(휴무일·근무 기준)와 직원 근태(출퇴근·연차) API.

admin.py 가 이미 5천 줄이 넘어서 새 기능은 이 파일에 둔다. 시각은 전부 KST(UTC+9)
기준으로 계산하고 저장은 UTC 그대로 둔다(now() 는 UTC).
"""
from datetime import timedelta, datetime, date as date_cls, time as time_cls

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from utils.api import APIView
from account.decorators import admin_role_required
from account.models import User

from ..models import (AcademyProfile, AcademyRole, Branch, Holiday, HolidayOptOut, HolidayKind,
                      StaffWorkPlan, WorkType,
                      HOLIDAY_KIND_CHOICES, WorkSchedule, StaffAttendance,
                      StaffAttendanceChange, StaffLeave, LeaveKind, LEAVE_KIND_CHOICES,
                      LessonOccurrence, OccurrenceStatus, STAFF_ROLES)
from ..services import viewable_branch_ids, editable_branch_ids, can_manage_branch, can_view_branch

_WD = ["월", "화", "수", "목", "금", "토", "일"]

HOLIDAY_KIND_LABEL = dict(HOLIDAY_KIND_CHOICES)
LEAVE_KIND_LABEL = dict(LEAVE_KIND_CHOICES)

# 지각 유예(분). 학생 출결과 같은 기준을 쓴다.
LATE_GRACE_MIN = 5
# 출퇴근을 잘못 찍었을 때 스스로 취소할 수 있는 시간(분)
UNDO_WINDOW_MIN = 5


def kst_now():
    return now() + timedelta(hours=9)


def kst_today():
    return kst_now().date()


def kst_hm(dt):
    """UTC 로 저장된 시각을 KST 'HH:MM' 로."""
    if not dt:
        return ""
    return str(dt + timedelta(hours=9))[11:16]


def kst_dt(dt):
    if not dt:
        return ""
    return str(dt + timedelta(hours=9))[:16]


def hm(v):
    """'HH:MM' 을 time 으로. 못 읽으면 None."""
    t = (v or "").strip()
    m = __import__("re").match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return time_cls(h, mi)


def kst_to_utc(d, hm):
    """KST 날짜+'HH:MM' 을 UTC datetime 으로."""
    h, m = [int(x) for x in hm.split(":")]
    return datetime.combine(d, time_cls(h, m)) - timedelta(hours=9)


def parse_date(v, default=None):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def name_of(u):
    if not u:
        return ""
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username


def role_of(user):
    p = getattr(user, "academy_profile", None)
    return p.role if p else ""


DIRECTOR_UP = {AcademyRole.HQ_ADMIN, AcademyRole.REGIONAL_MANAGER, AcademyRole.BRANCH_MANAGER}
# 근태를 관리(타인 조회·승인·설정)할 수 있는 역할. 부원장은 운영 권한만 있어 제외.
HR_MANAGE_ROLES = DIRECTOR_UP | {AcademyRole.HR_ADMIN}


def is_director_up(user):
    return role_of(user) in DIRECTOR_UP or user.is_super_admin()


def can_manage_hr(user):
    return role_of(user) in HR_MANAGE_ROLES or user.is_super_admin()


def my_branch_id(user):
    p = getattr(user, "academy_profile", None)
    return p.branch_id if p else None


# ─────────────────────────── 학원 휴무일 ───────────────────────────

def holidays_on(d, branch_id):
    """그 날짜에 해당 지점이 쉬는지. 전지점 휴무(branch=None)도 포함.

    전지점 휴무라도 그 지점이 '사용 안 함'으로 빼 두었으면 쉬지 않는다."""
    qs = Holiday.objects.filter(date=d, is_deleted=False).filter(
        Q(branch_id=None) | Q(branch_id=branch_id))
    if branch_id:
        qs = qs.exclude(opt_outs__branch_id=branch_id)
    return qs


def apply_holiday(h):
    """휴무일 등록 시 그날 수업을 '휴무'로 바꾼다.
    이미 등원 기록이 있거나 결석·임시휴원으로 정리된 건은 건드리지 않는다
    (그날 실제로 수업을 했거나 이미 다른 사유로 정리된 것이므로)."""
    qs = LessonOccurrence.objects.filter(date=h.date, status=OccurrenceStatus.SCHEDULED)
    if h.branch_id:
        qs = qs.filter(branch_id=h.branch_id)
    else:
        off = list(h.opt_outs.values_list("branch_id", flat=True))
        if off:
            qs = qs.exclude(branch_id__in=off)
    from ..models import DailyAttendance
    attended = set(DailyAttendance.objects.filter(date=h.date, check_in_at__isnull=False)
                   .values_list("student_id", flat=True))
    ids = [o.id for o in qs if o.student_id not in attended]
    if ids:
        LessonOccurrence.objects.filter(id__in=ids).update(status=OccurrenceStatus.HOLIDAY)
    return len(ids)


def revert_holiday(h):
    """휴무일을 지우면 '휴무'로 바꿔둔 수업을 예정으로 되돌린다.
    같은 날 다른 휴무일이 아직 남아 있으면 그대로 둔다."""
    others = holidays_on(h.date, h.branch_id).exclude(id=h.id)
    if h.branch_id is None:
        # 전지점 휴무를 지우는 경우: 지점별 휴무가 남아 있으면 그 지점은 유지
        keep_branches = set(others.exclude(branch_id=None).values_list("branch_id", flat=True))
        if others.filter(branch_id=None).exists():
            return 0
    else:
        if others.exists():
            return 0
        keep_branches = set()
    qs = LessonOccurrence.objects.filter(date=h.date, status=OccurrenceStatus.HOLIDAY)
    if h.branch_id:
        qs = qs.filter(branch_id=h.branch_id)
    elif keep_branches:
        qs = qs.exclude(branch_id__in=keep_branches)
    return qs.update(status=OccurrenceStatus.SCHEDULED)


class HolidayAdminAPI(APIView):
    """학원 휴무일 목록·등록·삭제. 조회는 직원 전체, 등록·삭제는 원장 이상."""

    @admin_role_required
    def get(self, request):
        d0 = parse_date(request.GET.get("from"), kst_today().replace(month=1, day=1))
        d1 = parse_date(request.GET.get("to"), kst_today().replace(month=12, day=31))
        view = viewable_branch_ids(request.user)
        qs = Holiday.objects.filter(is_deleted=False, date__gte=d0, date__lte=d1) \
                            .select_related("branch", "created_by")
        if view is not None:
            qs = qs.filter(Q(branch_id=None) | Q(branch_id__in=view))
        qs = qs.prefetch_related("opt_outs", "opt_outs__branch")
        # 내가 관리하는 지점 기준으로 '우리 지점은 쉬지 않음' 을 함께 내려준다
        mine = editable_branch_ids(request.user)
        out = []
        for h in qs:
            offs = list(h.opt_outs.all())
            off_ids = {o.branch_id for o in offs}
            my_off = None
            if mine is not None:
                my_off = bool(off_ids & set(mine))
            out.append({"id": h.id, "date": str(h.date), "wd": _WD[h.date.weekday()],
                        "name": h.name, "kind": h.kind, "kind_label": HOLIDAY_KIND_LABEL.get(h.kind, h.kind),
                        "branch_id": h.branch_id, "branch": (h.branch.name if h.branch_id else "전 지점"),
                        "note": h.note, "created_by": name_of(h.created_by),
                        "off_branches": [o.branch.name for o in offs],
                        "my_off": my_off,
                        "time": kst_dt(h.create_time)})
        return self.success({"rows": out, "kinds": [{"value": k, "label": v} for k, v in HOLIDAY_KIND_CHOICES]})

    @admin_role_required
    def post(self, request):
        if not is_director_up(request.user):
            return self.error("휴무일 등록은 원장 이상만 가능합니다.")
        data = request.data
        # 여러 날짜를 한 번에 등록(연초에 공휴일을 몰아 넣는 경우)
        items = data.get("items")
        if not isinstance(items, list):
            items = [data]
        bid = data.get("branch_id") or None
        if bid and not can_manage_branch(request.user, int(bid)):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        if not bid:
            # 전 지점 휴무일은 전지점 역할(본부·인사)만. 원장은 자기 지점으로 한정한다
            # (지점 하나를 맡은 사람이 회사 전체를 쉬게 할 수 있으면 안 됨).
            if editable_branch_ids(request.user) is not None:
                bid = my_branch_id(request.user)
                if not bid:
                    return self.error("지점을 선택하세요.")
        made, skipped, applied = [], [], 0
        with transaction.atomic():
            for it in items:
                d = parse_date(it.get("date"))
                nm = (it.get("name") or "").strip()
                if not d or not nm:
                    continue
                if Holiday.objects.filter(date=d, branch_id=bid, is_deleted=False).exists():
                    skipped.append(str(d))
                    continue
                h = Holiday.objects.create(
                    date=d, name=nm, kind=(it.get("kind") or HolidayKind.PUBLIC),
                    branch_id=bid, note=(it.get("note") or "").strip(),
                    created_by=request.user)
                applied += apply_holiday(h)
                made.append(str(d))
        return self.success({"created": made, "skipped": skipped, "lessons": applied})

    @admin_role_required
    def put(self, request):
        """전 지점 휴무일을 우리 지점만 쉬지 않기(끄기)·다시 쉬기(켜기).

        공휴일이라도 지점 사정에 따라 수업을 하는 날이 있다. 전 지점 휴무를 지워
        버리면 다른 지점까지 영향을 받으므로 지우는 대신 지점별로 뺀다."""
        if not is_director_up(request.user):
            return self.error("원장 이상만 바꿀 수 있습니다.")
        d = request.data
        h = Holiday.objects.filter(id=d.get("id"), is_deleted=False).first()
        if not h:
            return self.error("휴무일이 없습니다.")
        if h.branch_id is not None:
            return self.error("전 지점 휴무일에만 쓸 수 있습니다.")
        bid = d.get("branch_id") or None
        if not bid:
            mine = editable_branch_ids(request.user)
            bid = mine[0] if mine else None
        if not bid:
            return self.error("어느 지점인지 알 수 없습니다.")
        bid = int(bid)
        if not can_manage_branch(request.user, bid):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        if d.get("off"):
            HolidayOptOut.objects.get_or_create(
                holiday=h, branch_id=bid,
                defaults={"actor": request.user, "reason": (d.get("reason") or "").strip()})
            # 이미 휴무로 바뀌어 있던 그 지점 수업을 예정으로 되돌린다
            n = LessonOccurrence.objects.filter(
                date=h.date, branch_id=bid, status=OccurrenceStatus.HOLIDAY
            ).update(status=OccurrenceStatus.SCHEDULED)
            return self.success({"off": True, "changed": n})
        HolidayOptOut.objects.filter(holiday=h, branch_id=bid).delete()
        # 다시 쉬기 — 그날 그 지점 예정 수업을 휴무로
        from ..models import DailyAttendance
        attended = set(DailyAttendance.objects.filter(date=h.date, check_in_at__isnull=False)
                       .values_list("student_id", flat=True))
        ids = [o.id for o in LessonOccurrence.objects.filter(
            date=h.date, branch_id=bid, status=OccurrenceStatus.SCHEDULED)
            if o.student_id not in attended]
        if ids:
            LessonOccurrence.objects.filter(id__in=ids).update(status=OccurrenceStatus.HOLIDAY)
        return self.success({"off": False, "changed": len(ids)})

    @admin_role_required
    def delete(self, request):
        if not is_director_up(request.user):
            return self.error("휴무일 삭제는 원장 이상만 가능합니다.")
        h = Holiday.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not h:
            return self.error("휴무일이 없습니다.")
        if h.branch_id and not can_manage_branch(request.user, h.branch_id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        # 전 지점 휴무를 한 지점 원장이 지우면 다른 지점까지 영향을 받는다.
        # 지우는 건 전 지점을 볼 수 있는 사람만, 원장은 '우리 지점 사용 안 함'을 쓴다.
        if h.branch_id is None and editable_branch_ids(request.user) is not None:
            return self.error("전 지점 휴무일은 지울 수 없습니다. "
                              "우리 지점만 쉬지 않으려면 [우리 지점 사용 안 함]을 쓰세요.")
        reverted = revert_holiday(h)
        h.is_deleted = True
        h.deleted_by = request.user
        h.deleted_at = now()
        h.delete_reason = (request.GET.get("reason") or "").strip()[:255]
        h.save(update_fields=["is_deleted", "deleted_by", "deleted_at", "delete_reason"])
        return self.success({"reverted": reverted})


# ─────────────────────────── 근무 기준 ───────────────────────────

def is_irregular(staff_id):
    p = AcademyProfile.objects.filter(user_id=staff_id).only("work_type").first()
    return bool(p and p.work_type == WorkType.IRREGULAR)


def plan_for(staff_id, d):
    """불규칙 근무자의 그날 근무 예정. 급여는 이 시간으로 계산한다."""
    return StaffWorkPlan.objects.filter(staff_id=staff_id, date=d).first()


def schedule_for(staff_id, branch_id, d):
    """그 날짜에 적용되는 근무 기준. 직원 개별 지정이 있으면 그것이 우선.

    불규칙 근무자는 정해진 요일·시각이 없어 근무 기준을 쓰지 않는다(지각·결근을 따지면
    전부 거짓말이 된다). 그날 근무표가 있으면 그것을 기준처럼 쓴다."""
    if is_irregular(staff_id):
        pl = plan_for(staff_id, d)
        if not pl:
            return None
        return WorkSchedule(staff_id=staff_id, active_from=d,
                            start_time=pl.start_time, end_time=pl.end_time,
                            workdays="0123456", break_per_hours=0, break_minutes=0)
    base = WorkSchedule.objects.filter(active_from__lte=d).filter(
        Q(active_until=None) | Q(active_until__gte=d))
    s = base.filter(staff_id=staff_id).order_by("-active_from", "-id").first()
    if s:
        return s
    if branch_id:
        return base.filter(staff_id=None, branch_id=branch_id).order_by("-active_from", "-id").first()
    return None


def schedule_row(s):
    if not s:
        return None
    return {"id": s.id, "branch_id": s.branch_id, "staff_id": s.staff_id,
            "active_from": str(s.active_from), "active_until": (str(s.active_until) if s.active_until else ""),
            "start_time": str(s.start_time)[:5], "end_time": str(s.end_time)[:5],
            "workdays": s.workdays, "workdays_label": "".join(_WD[int(c)] for c in s.workdays if c.isdigit()),
            "break_per_hours": s.break_per_hours, "break_minutes": s.break_minutes,
            "reason": s.reason, "created_by": name_of(s.created_by), "time": kst_dt(s.create_time),
            "current": s.active_until is None or s.active_until >= kst_today()}


class WorkScheduleAdminAPI(APIView):
    """근무 기준(정규 근무시간·휴게). 학생 시간표와 같은 '적용 시작일' 방식이라
    바꾸면 이전 줄이 전날로 끝나고 과거 기록이 그대로 남는다."""

    @admin_role_required
    def get(self, request):
        bid = request.GET.get("branch_id") or None
        sid = request.GET.get("staff_id") or None
        qs = WorkSchedule.objects.select_related("created_by", "branch", "staff")
        if sid:
            qs = qs.filter(staff_id=sid)
        else:
            qs = qs.filter(staff_id=None)
            if bid:
                qs = qs.filter(branch_id=bid)
            else:
                view = viewable_branch_ids(request.user)
                if view is not None:
                    qs = qs.filter(branch_id__in=view)
        rows = [schedule_row(s) for s in qs.order_by("-active_from", "-id")[:200]]
        return self.success(rows)

    @admin_role_required
    def post(self, request):
        """근무 기준 변경. {branch_id | staff_ids[], active_from, start_time, end_time,
        workdays, break_per_hours, break_minutes, reason}"""
        if not can_manage_hr(request.user):
            return self.error("근무 기준 변경은 원장 이상만 가능합니다.")
        d = request.data
        af = parse_date(d.get("active_from"))
        if not af:
            return self.error("적용 시작일을 입력하세요.")
        st, et = (d.get("start_time") or "").strip(), (d.get("end_time") or "").strip()
        if not st or not et:
            return self.error("근무 시각을 입력하세요.")
        reason = (d.get("reason") or "").strip()
        if not reason:
            return self.error("변경 사유를 입력하세요.")
        wd = "".join(c for c in str(d.get("workdays") or "012345") if c.isdigit())
        if not wd:
            return self.error("근무 요일을 선택하세요.")
        common = dict(active_from=af, start_time=st, end_time=et, workdays=wd,
                      break_per_hours=int(d.get("break_per_hours") or 4),
                      break_minutes=int(d.get("break_minutes") or 30),
                      reason=reason, created_by=request.user)
        staff_ids = d.get("staff_ids") or []
        made = []
        with transaction.atomic():
            if staff_ids:
                for uid in staff_ids:
                    p = AcademyProfile.objects.filter(user_id=uid).first()
                    if p and not can_manage_branch(request.user, p.branch_id):
                        continue
                    self._close_previous(WorkSchedule.objects.filter(staff_id=uid), af)
                    made.append(WorkSchedule.objects.create(staff_id=uid, branch_id=(p.branch_id if p else None),
                                                            **common).id)
            else:
                bid = d.get("branch_id") or my_branch_id(request.user)
                if not bid:
                    return self.error("지점을 선택하세요.")
                if not can_manage_branch(request.user, int(bid)):
                    return self.error("이 지점을 관리할 권한이 없습니다.")
                self._close_previous(WorkSchedule.objects.filter(staff_id=None, branch_id=bid), af)
                made.append(WorkSchedule.objects.create(branch_id=bid, **common).id)
        return self.success({"created": made})

    @staticmethod
    def _close_previous(qs, af):
        """적용 시작일 이전의 열린 줄을 전날로 끊고, 같은 날 이후 줄은 지운다."""
        qs.filter(active_from__gte=af).delete()
        for s in qs.filter(Q(active_until=None) | Q(active_until__gte=af)):
            s.active_until = af - timedelta(days=1)
            s.save(update_fields=["active_until"])


# ─────────────────────────── 직원 근태 ───────────────────────────

def leave_map(staff_ids, d0, d1):
    out = {}
    for lv in StaffLeave.objects.filter(staff_id__in=staff_ids, date__gte=d0, date__lte=d1,
                                        is_deleted=False):
        out[(lv.staff_id, lv.date)] = lv
    return out


def att_row(a, sch, leave, today):
    """근태 한 줄. 지각은 정규 출근시각 + 유예(5분) 초과분으로 계산한다."""
    d = a.date if a else None
    in_hm = kst_hm(a.check_in_at) if a else ""
    out_hm = kst_hm(a.check_out_at) if a else ""
    late = 0
    # 불규칙 근무자는 '몇 시까지 와야 한다'가 없어 지각을 따지지 않는다.
    # 근무표가 있으면 그 시각을 쓰되, 급여는 근무표대로라 지각으로 깎지 않는다.
    if in_hm and sch and getattr(sch, "id", None) is not None:
        ref = int(str(sch.start_time)[:2]) * 60 + int(str(sch.start_time)[3:5])
        cur = int(in_hm[:2]) * 60 + int(in_hm[3:])
        if cur - ref > LATE_GRACE_MIN:
            late = cur - ref
    if leave:
        state = "LEAVE"
    elif in_hm and out_hm:
        state = "DONE"
    elif in_hm:
        state = "WORKING"
    elif d and d < today:
        state = "MISSING"
    else:
        state = "NONE"
    return {"id": (a.id if a else None), "date": str(d) if d else "",
            "wd": (_WD[d.weekday()] if d else ""),
            "in": in_hm, "out": out_hm, "late": late,
            "in_source": (a.in_source if a else ""), "out_source": (a.out_source if a else ""),
            "note": (a.note if a else ""), "state": state,
            "branch": (a.branch.name if (a and a.branch_id) else ""),
            "leave_kind": (leave.kind if leave else ""),
            "leave_label": (LEAVE_KIND_LABEL.get(leave.kind, leave.kind) if leave else ""),
            "leave_reason": (leave.reason if leave else "")}


def worked_minutes(a, sch):
    """근무시간(분) — 휴게시간을 차감한 값. 원장 이상 화면에서만 쓴다."""
    if not a or not a.check_in_at or not a.check_out_at:
        return 0
    total = int((a.check_out_at - a.check_in_at).total_seconds() // 60)
    if sch and sch.break_per_hours and sch.break_minutes:
        blocks = total // (sch.break_per_hours * 60)
        total -= blocks * sch.break_minutes
    return max(0, total)


class StaffAttendanceAPI(APIView):
    """근태 조회. 본인은 항상, 타인은 원장 이상만.
    GET ?staff_id=&from=&to=  /  ?branch_id= 로 지점 전체(월 그리드)"""

    @admin_role_required
    def get(self, request):
        me = request.user
        today = kst_today()
        d1 = parse_date(request.GET.get("to"), today)
        d0 = parse_date(request.GET.get("from"), d1.replace(day=1))
        mine_only = not can_manage_hr(me)

        if request.GET.get("branch_id") or request.GET.get("all"):
            if mine_only:
                return self.error("다른 직원의 근태는 원장 이상만 볼 수 있습니다.")
            bid = request.GET.get("branch_id") or my_branch_id(me)
            if bid and not can_view_branch(me, int(bid)):
                return self.error("이 지점을 볼 권한이 없습니다.")
            profs = AcademyProfile.objects.filter(role__in=STAFF_ROLES, is_deleted=False) \
                                          .select_related("user", "user__userprofile", "branch")
            if bid:
                profs = profs.filter(branch_id=bid)
            else:
                view = viewable_branch_ids(me)
                if view is not None:
                    profs = profs.filter(branch_id__in=view)
            staff = list(profs)
            ids = [p.user_id for p in staff]
        else:
            sid = request.GET.get("staff_id")
            if sid and str(sid) != str(me.id) and mine_only:
                return self.error("다른 직원의 근태는 원장 이상만 볼 수 있습니다.")
            uid = int(sid) if sid else me.id
            p = AcademyProfile.objects.filter(user_id=uid).select_related(
                "user", "user__userprofile", "branch").first()
            if not p:
                return self.error("직원 정보가 없습니다.")
            staff, ids = [p], [uid]

        atts = {}
        for a in StaffAttendance.objects.filter(staff_id__in=ids, date__gte=d0, date__lte=d1) \
                                        .select_related("branch"):
            atts[(a.staff_id, a.date)] = a
        leaves = leave_map(ids, d0, d1)

        show_total = can_manage_hr(me)
        people = []
        for p in staff:
            days, sch_cache = [], {}
            cur = d0
            total_min = late_days = work_days = leave_days = 0
            while cur <= d1:
                sch = sch_cache.get(cur)
                if sch is None:
                    sch = schedule_for(p.user_id, p.branch_id, cur)
                    sch_cache[cur] = sch
                a = atts.get((p.user_id, cur))
                lv = leaves.get((p.user_id, cur))
                r = att_row(a, sch, lv, today)
                r["date"] = str(cur)
                r["wd"] = _WD[cur.weekday()]
                r["scheduled"] = bool(sch and str(cur.weekday()) in sch.workdays)
                r["sch_start"] = (str(sch.start_time)[:5] if sch else "")
                r["sch_end"] = (str(sch.end_time)[:5] if sch else "")
                if show_total:
                    r["minutes"] = worked_minutes(a, sch)
                    total_min += r["minutes"]
                if r["state"] in ("DONE", "WORKING"):
                    work_days += 1
                if r["late"]:
                    late_days += 1
                if lv:
                    leave_days += 1
                days.append(r)
                cur += timedelta(days=1)
            person = {"staff_id": p.user_id, "name": name_of(p.user), "staff_no": p.staff_no,
                      "role": p.role, "branch": (p.branch.name if p.branch_id else ""),
                      "days": days, "work_days": work_days, "late_days": late_days,
                      "leave_days": leave_days}
            if show_total:
                person["total_minutes"] = total_min
            people.append(person)
        people.sort(key=lambda x: (x["branch"], x["staff_no"]))
        return self.success({"from": str(d0), "to": str(d1), "today": str(today),
                             "show_total": show_total, "can_manage": can_manage_hr(me),
                             "people": people})


class StaffAttendanceCheckAPI(APIView):
    """포털에서 직접 출근/퇴근을 찍는다(키오스크가 없는 자리 대비).
    {action: IN|OUT|UNDO}"""

    @admin_role_required
    def post(self, request):
        act = (request.data.get("action") or "").upper()
        uid = request.data.get("staff_id") or request.user.id
        if str(uid) != str(request.user.id) and not can_manage_hr(request.user):
            return self.error("다른 직원의 출퇴근은 원장 이상만 기록할 수 있습니다.")
        u = User.objects.filter(id=uid).first()
        if not u:
            return self.error("직원이 없습니다.")
        return _wrap(self, staff_check(u, act, "PORTAL", my_branch_id(u), request.user))


def staff_check(staff, action, source, branch_id, actor):
    """출근/퇴근/취소 공통 처리. 키오스크와 포털이 같은 규칙을 쓴다."""
    today = kst_today()
    tnow = now()
    a, _ = StaffAttendance.objects.get_or_create(
        staff=staff, date=today, defaults={"branch_id": branch_id})

    def log(field, old, new, extra=""):
        StaffAttendanceChange.objects.create(
            attendance=a, actor=actor, field=field, old_value=old or "", new_value=new or "",
            status=StaffAttendanceChange.DIRECT, source=source, reason=extra)

    if action == "UNDO":
        # 잘못 찍었을 때 5분 안에는 스스로 취소할 수 있다.
        last, field = None, ""
        if a.check_out_at:
            last, field = a.check_out_at, "OUT"
        elif a.check_in_at:
            last, field = a.check_in_at, "IN"
        if not last:
            return _err("취소할 기록이 없습니다.")
        if (tnow - last).total_seconds() > UNDO_WINDOW_MIN * 60:
            return _err("%d분이 지나 취소할 수 없습니다. 원장에게 수정을 요청하세요." % UNDO_WINDOW_MIN)
        old = kst_hm(last)
        if field == "OUT":
            a.check_out_at = None
            a.out_source = ""
        else:
            a.check_in_at = None
            a.in_source = ""
        a.save()
        log("CANCEL", old, "", "%s 취소" % ("퇴근" if field == "OUT" else "출근"))
        return _ok({"action": "UNDO", "field": field, "name": name_of(staff)})

    if action == "IN":
        if a.check_in_at:
            return _err("이미 %s 에 출근하셨습니다." % kst_hm(a.check_in_at))
        a.check_in_at = tnow
        a.in_source = source
        if branch_id and not a.branch_id:
            a.branch_id = branch_id
        a.save()
        log("IN", "", kst_hm(tnow))
        return _ok({"action": "IN", "time": kst_hm(tnow), "name": name_of(staff)})

    if action == "OUT":
        if not a.check_in_at:
            return _err("출근 기록이 없습니다. 먼저 출근을 찍어주세요.")
        if a.check_out_at:
            return _err("이미 %s 에 퇴근하셨습니다." % kst_hm(a.check_out_at))
        a.check_out_at = tnow
        a.out_source = source
        a.save()
        log("OUT", "", kst_hm(tnow))
        return _ok({"action": "OUT", "time": kst_hm(tnow), "name": name_of(staff)})

    return _err("알 수 없는 동작입니다.")


def _ok(data):
    return {"__ok": True, "data": data}


def _err(msg):
    return {"__ok": False, "msg": msg}


def _wrap(view, result):
    """staff_check 결과를 APIView 응답으로."""
    if isinstance(result, dict) and "__ok" in result:
        return view.success(result["data"]) if result["__ok"] else view.error(result["msg"])
    return result


class StaffAttendanceEditAPI(APIView):
    """시각 수정. 본인은 승인 요청으로 남고, 원장 이상은 바로 반영된다.
    {att_id | staff_id+date, field: IN|OUT|NOTE, value, reason}"""

    @admin_role_required
    def post(self, request):
        d = request.data
        field = (d.get("field") or "").upper()
        if field not in ("IN", "OUT", "NOTE"):
            return self.error("수정 항목이 올바르지 않습니다.")
        a = None
        if d.get("att_id"):
            a = StaffAttendance.objects.filter(id=d["att_id"]).select_related("staff").first()
        elif d.get("staff_id") and d.get("date"):
            dt = parse_date(d.get("date"))
            a, _ = StaffAttendance.objects.get_or_create(
                staff_id=d["staff_id"], date=dt,
                defaults={"branch_id": my_branch_id(request.user)})
        if not a:
            return self.error("근태 기록이 없습니다.")
        mine = a.staff_id == request.user.id
        if not mine and not can_manage_hr(request.user):
            return self.error("다른 직원의 근태는 원장 이상만 수정할 수 있습니다.")
        reason = (d.get("reason") or "").strip()
        value = (d.get("value") or "").strip()

        if field == "NOTE":
            # 비고는 본인이 자유롭게 쓴다(승인 불필요).
            old, a.note = a.note, value[:255]
            a.save(update_fields=["note"])
            StaffAttendanceChange.objects.create(
                attendance=a, actor=request.user, field="NOTE", old_value=old, new_value=a.note,
                status=StaffAttendanceChange.DIRECT, source="PORTAL")
            return self.success("ok")

        if value and not _valid_hm(value):
            return self.error("시각은 HH:MM 형식으로 입력하세요.")
        if not reason:
            return self.error("수정 사유를 입력하세요.")
        old = kst_hm(a.check_in_at if field == "IN" else a.check_out_at)

        if mine and not can_manage_hr(request.user):
            # 본인 수정은 바로 반영하지 않고 승인 요청으로 남긴다.
            StaffAttendanceChange.objects.create(
                attendance=a, actor=request.user, field=field, old_value=old, new_value=value,
                reason=reason, status=StaffAttendanceChange.REQUESTED, source="PORTAL")
            return self.success({"requested": True})

        _apply_time(a, field, value)
        StaffAttendanceChange.objects.create(
            attendance=a, actor=request.user, field=field, old_value=old, new_value=value,
            reason=reason, status=StaffAttendanceChange.DIRECT, source="PORTAL")
        return self.success({"requested": False})


def _valid_hm(v):
    try:
        h, m = v.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (ValueError, AttributeError):
        return False


def _apply_time(a, field, value):
    dt = kst_to_utc(a.date, value) if value else None
    if field == "IN":
        a.check_in_at = dt
    else:
        a.check_out_at = dt
    a.save()


class StaffAttendanceApproveAPI(APIView):
    """수정 요청 목록·승인·반려. 원장 이상."""

    @admin_role_required
    def get(self, request):
        if not can_manage_hr(request.user):
            return self.error("원장 이상만 볼 수 있습니다.")
        view = viewable_branch_ids(request.user)
        qs = StaffAttendanceChange.objects.filter(status=StaffAttendanceChange.REQUESTED) \
            .select_related("attendance", "attendance__staff", "actor")
        out = []
        for c in qs[:200]:
            p = AcademyProfile.objects.filter(user_id=c.attendance.staff_id).first()
            if view is not None and p and p.branch_id not in view:
                continue
            out.append({"id": c.id, "staff": name_of(c.attendance.staff),
                        "date": str(c.attendance.date), "field": c.field,
                        "field_label": {"IN": "출근", "OUT": "퇴근"}.get(c.field, c.field),
                        "old": c.old_value, "new": c.new_value, "reason": c.reason,
                        "time": kst_dt(c.create_time)})
        return self.success(out)

    @admin_role_required
    def post(self, request):
        if not can_manage_hr(request.user):
            return self.error("원장 이상만 처리할 수 있습니다.")
        c = StaffAttendanceChange.objects.filter(
            id=request.data.get("id"), status=StaffAttendanceChange.REQUESTED) \
            .select_related("attendance").first()
        if not c:
            return self.error("처리할 요청이 없습니다.")
        if (request.data.get("action") or "").upper() == "APPROVE":
            _apply_time(c.attendance, c.field, c.new_value)
            c.status = StaffAttendanceChange.APPROVED
        else:
            c.status = StaffAttendanceChange.REJECTED
            c.reject_reason = (request.data.get("reason") or "").strip()[:255]
        c.approver = request.user
        c.approved_at = now()
        c.save(update_fields=["status", "approver", "approved_at", "reject_reason"])
        return self.success("ok")


class StaffAttendanceHistoryAPI(APIView):
    """한 근태 기록의 변경 이력(최신이 위)."""

    @admin_role_required
    def get(self, request):
        a = StaffAttendance.objects.filter(id=request.GET.get("att_id")).first()
        if not a:
            return self.success([])
        if a.staff_id != request.user.id and not can_manage_hr(request.user):
            return self.error("권한이 없습니다.")
        FIELD = {"IN": "출근", "OUT": "퇴근", "NOTE": "비고", "CANCEL": "취소"}
        ST = {"REQUESTED": "승인 대기", "APPROVED": "승인됨", "REJECTED": "반려됨",
              "DIRECT": "", "CANCELLED": "취소됨"}
        out = []
        for c in a.changes.select_related("actor", "approver"):
            out.append({"id": c.id, "field": FIELD.get(c.field, c.field),
                        "old": c.old_value, "new": c.new_value, "reason": c.reason,
                        "status": ST.get(c.status, c.status), "actor": name_of(c.actor),
                        "approver": name_of(c.approver), "source": c.source,
                        "time": kst_dt(c.create_time)})
        return self.success(out)


class StaffLeaveAPI(APIView):
    """연차·휴가 기록. 등록·삭제는 원장 이상, 조회는 본인 것 또는 원장 이상."""

    @admin_role_required
    def get(self, request):
        d1 = parse_date(request.GET.get("to"), kst_today().replace(month=12, day=31))
        d0 = parse_date(request.GET.get("from"), kst_today().replace(month=1, day=1))
        qs = StaffLeave.objects.filter(is_deleted=False, date__gte=d0, date__lte=d1) \
                               .select_related("staff", "created_by")
        if not can_manage_hr(request.user):
            qs = qs.filter(staff_id=request.user.id)
        elif request.GET.get("staff_id"):
            qs = qs.filter(staff_id=request.GET["staff_id"])
        else:
            view = viewable_branch_ids(request.user)
            if view is not None:
                ids = AcademyProfile.objects.filter(branch_id__in=view).values_list("user_id", flat=True)
                qs = qs.filter(staff_id__in=ids)
        rows = [{"id": lv.id, "staff_id": lv.staff_id, "staff": name_of(lv.staff),
                 "date": str(lv.date), "wd": _WD[lv.date.weekday()], "kind": lv.kind,
                 "kind_label": LEAVE_KIND_LABEL.get(lv.kind, lv.kind), "reason": lv.reason,
                 "created_by": name_of(lv.created_by), "time": kst_dt(lv.create_time)}
                for lv in qs]
        return self.success({"rows": rows,
                             "kinds": [{"value": k, "label": v} for k, v in LEAVE_KIND_CHOICES]})

    @admin_role_required
    def post(self, request):
        if not can_manage_hr(request.user):
            return self.error("휴가 등록은 원장 이상만 가능합니다.")
        d = request.data
        uid = d.get("staff_id")
        d0 = parse_date(d.get("date"))
        d1 = parse_date(d.get("date_to"), d0)   # 연속 휴가는 기간으로 한 번에
        if not uid or not d0:
            return self.error("직원과 날짜를 선택하세요.")
        if d1 < d0:
            d0, d1 = d1, d0
        p = AcademyProfile.objects.filter(user_id=uid).first()
        if p and not can_manage_branch(request.user, p.branch_id):
            return self.error("이 지점 직원이 아닙니다.")
        kind = d.get("kind") or LeaveKind.ANNUAL
        reason = (d.get("reason") or "").strip()
        made, skipped = [], []
        cur = d0
        while cur <= d1:
            if StaffLeave.objects.filter(staff_id=uid, date=cur, is_deleted=False).exists():
                skipped.append(str(cur))
            else:
                StaffLeave.objects.create(staff_id=uid, date=cur, kind=kind, reason=reason,
                                          created_by=request.user)
                made.append(str(cur))
            cur += timedelta(days=1)
        return self.success({"created": made, "skipped": skipped})

    @admin_role_required
    def delete(self, request):
        if not can_manage_hr(request.user):
            return self.error("휴가 삭제는 원장 이상만 가능합니다.")
        lv = StaffLeave.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not lv:
            return self.error("휴가 기록이 없습니다.")
        lv.is_deleted = True
        lv.deleted_by = request.user
        lv.deleted_at = now()
        lv.delete_reason = (request.GET.get("reason") or "").strip()[:255]
        lv.save(update_fields=["is_deleted", "deleted_by", "deleted_at", "delete_reason"])
        return self.success("ok")


class HRTodayAPI(APIView):
    """오늘 출근 현황 요약(원장 이상) + 본인 오늘 기록. 로그인 첫 화면에서도 쓴다."""

    @admin_role_required
    def get(self, request):
        today = kst_today()
        me = request.user
        mine = StaffAttendance.objects.filter(staff=me, date=today).first()
        my_p = getattr(me, "academy_profile", None)
        my_sch = schedule_for(me.id, (my_p.branch_id if my_p else None), today)
        my_leave = StaffLeave.objects.filter(staff=me, date=today, is_deleted=False).first()
        out = {"today": str(today), "can_manage": can_manage_hr(me),
               "mine": att_row(mine, my_sch, my_leave, today),
               "undo_window": UNDO_WINDOW_MIN}
        out["mine"]["date"] = str(today)
        if can_manage_hr(me):
            view = viewable_branch_ids(me)
            profs = AcademyProfile.objects.filter(role__in=STAFF_ROLES, is_deleted=False) \
                                          .select_related("user", "user__userprofile")
            if view is not None:
                profs = profs.filter(branch_id__in=view)
            ids = [p.user_id for p in profs]
            atts = {a.staff_id: a for a in StaffAttendance.objects.filter(staff_id__in=ids, date=today)}
            lvs = {lv.staff_id: lv for lv in StaffLeave.objects.filter(
                staff_id__in=ids, date=today, is_deleted=False)}
            working = done = absent = on_leave = 0
            people = []
            for p in profs:
                a = atts.get(p.user_id)
                lv = lvs.get(p.user_id)
                sch = schedule_for(p.user_id, p.branch_id, today)
                scheduled = bool(sch and str(today.weekday()) in sch.workdays)
                r = att_row(a, sch, lv, today)
                r["name"] = name_of(p.user)
                r["scheduled"] = scheduled
                if lv:
                    on_leave += 1
                elif a and a.check_out_at:
                    done += 1
                elif a and a.check_in_at:
                    working += 1
                elif scheduled:
                    absent += 1
                people.append(r)
            out["summary"] = {"working": working, "done": done, "absent": absent, "leave": on_leave}
            out["people"] = sorted(people, key=lambda x: x["name"])
        return self.success(out)


# ─────────────────────── 불규칙 근무자 근무표 ───────────────────────

class WorkPlanAPI(APIView):
    """불규칙 근무자(아르바이트)의 날짜별 근무 예정.

    미리 정해 두되 갑자기 바뀌는 일이 잦아 그날만 고치고 사유를 남긴다.
    급여는 여기 적힌 시간으로 계산한다 — 몇 분 일찍·늦게 왔다고 깎거나 더하지 않는다.
    실제로 찍은 시각은 따로 보여 주기만 한다(궁금하니까).
    """

    @admin_role_required
    def get(self, request):
        """?staff_id=&month=YYYY-MM — 그 달의 근무표 + 실제 기록 + 합계."""
        sid = request.GET.get("staff_id")
        prof = AcademyProfile.objects.filter(user_id=sid, is_deleted=False) \
                                     .select_related("user", "user__userprofile", "branch").first()
        if not prof:
            return self.error("직원이 없습니다.")
        if not can_view_branch(request.user, prof.branch_id):
            return self.error("이 지점을 볼 권한이 없습니다.")
        ym = request.GET.get("month") or str(kst_today())[:7]
        try:
            y, m = int(ym[:4]), int(ym[5:7])
        except (ValueError, IndexError):
            return self.error("달이 올바르지 않습니다.")
        d0 = date_cls(y, m, 1)
        d1 = date_cls(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)

        plans = {p.date: p for p in StaffWorkPlan.objects.filter(
            staff_id=sid, date__gte=d0, date__lte=d1)}
        atts = {a.date: a for a in StaffAttendance.objects.filter(
            staff_id=sid, date__gte=d0, date__lte=d1)}

        rows, plan_min, real_min = [], 0, 0
        d = d0
        while d <= d1:
            p, a = plans.get(d), atts.get(d)
            pm = 0
            if p:
                pm = (p.end_time.hour * 60 + p.end_time.minute) - \
                     (p.start_time.hour * 60 + p.start_time.minute)
                if pm < 0:
                    pm += 24 * 60
                plan_min += pm
            rm = 0
            if a and a.check_in_at and a.check_out_at:
                rm = int((a.check_out_at - a.check_in_at).total_seconds() // 60)
                real_min += rm
            if p or a:
                rows.append({
                    "date": str(d), "wd": _WD[d.weekday()],
                    "start": (str(p.start_time)[:5] if p else ""),
                    "end": (str(p.end_time)[:5] if p else ""),
                    "note": (p.note if p else ""),
                    "plan_min": pm,
                    "in": kst_hm(a.check_in_at) if a else "",
                    "out": kst_hm(a.check_out_at) if a else "",
                    "real_min": rm,
                })
            d += timedelta(days=1)

        wage = prof.hourly_wage or 0
        return self.success({
            "staff_id": int(sid), "name": name_of(prof.user),
            "work_type": prof.work_type, "hourly_wage": wage,
            "month": ym, "rows": rows,
            "plan_min": plan_min, "real_min": real_min,
            # 급여는 정한 시간으로. 실 근무는 견주어 보라고 함께 준다.
            "pay": int(round(plan_min / 60.0 * wage)) if wage else 0,
        })

    @admin_role_required
    def post(self, request):
        """하루 저장. {staff_id, date, start, end, note} — start 가 비면 그날을 지운다."""
        d = request.data
        sid = d.get("staff_id")
        prof = AcademyProfile.objects.filter(user_id=sid, is_deleted=False).first()
        if not prof:
            return self.error("직원이 없습니다.")
        if not can_manage_branch(request.user, prof.branch_id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        day = parse_date(d.get("date"))
        if not day:
            return self.error("날짜를 정하세요.")
        st, en = hm(d.get("start")), hm(d.get("end"))
        if not st or not en:
            StaffWorkPlan.objects.filter(staff_id=sid, date=day).delete()
            return self.success({"deleted": True})
        StaffWorkPlan.objects.update_or_create(
            staff_id=sid, date=day,
            defaults={"start_time": st, "end_time": en,
                      "note": (d.get("note") or "").strip(), "created_by": request.user})
        return self.success({"ok": True})

    @admin_role_required
    def put(self, request):
        """지난달 그대로 가져오기 / 시급 저장."""
        d = request.data
        sid = d.get("staff_id")
        prof = AcademyProfile.objects.filter(user_id=sid, is_deleted=False).first()
        if not prof:
            return self.error("직원이 없습니다.")
        if not can_manage_branch(request.user, prof.branch_id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        if "hourly_wage" in d or "work_type" in d:
            if "hourly_wage" in d:
                try:
                    prof.hourly_wage = int(d.get("hourly_wage") or 0) or None
                except (TypeError, ValueError):
                    prof.hourly_wage = None
            if "work_type" in d:
                prof.work_type = (d.get("work_type") or WorkType.FIXED)
            prof.save(update_fields=["hourly_wage", "work_type"])
            return self.success({"ok": True})
        # 지난달 복사 — 요일이 같은 날에 같은 시각을 넣는다(날짜가 아니라 요일이 맞아야 한다)
        ym = d.get("month") or str(kst_today())[:7]
        y, m = int(ym[:4]), int(ym[5:7])
        pm_y, pm_m = (y - 1, 12) if m == 1 else (y, m - 1)
        src0 = date_cls(pm_y, pm_m, 1)
        src1 = date_cls(pm_y + (pm_m == 12), (pm_m % 12) + 1, 1) - timedelta(days=1)
        dst0 = date_cls(y, m, 1)
        dst1 = date_cls(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        by_wd = {}
        for p in StaffWorkPlan.objects.filter(staff_id=sid, date__gte=src0, date__lte=src1):
            by_wd.setdefault(p.date.weekday(), []).append(p)
        made = 0
        d0 = dst0
        while d0 <= dst1:
            src = by_wd.get(d0.weekday())
            if src and not StaffWorkPlan.objects.filter(staff_id=sid, date=d0).exists():
                p = src[0]
                StaffWorkPlan.objects.create(staff_id=sid, date=d0, start_time=p.start_time,
                                             end_time=p.end_time, created_by=request.user)
                made += 1
            d0 += timedelta(days=1)
        return self.success({"made": made})
