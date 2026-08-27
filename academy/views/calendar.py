"""일정 — 달력은 하나이고 범위와 종류로 켜고 끈다(docs/83).

절반은 이미 데이터가 있다. 공휴일·자격증/대회·상담 예약·보강은 제 자리에 있으므로
여기서는 **끌어와 보여 주기만** 한다. 두 곳에 적으면 반드시 어긋난다.
"""
from datetime import datetime, timedelta, date as date_cls, timezone as _tz

_utc = _tz.utc

from django.utils.timezone import now

from utils.api import APIView
from account.decorators import admin_role_required

from ..models import (CalendarEvent, Holiday, HOLIDAY_KIND_CHOICES, OptionItem,
                      AcademyProfile, Branch, ExamEntry, ExamStage,
                      CounselReservation, LessonOccurrence)
from ..services import viewable_branch_ids, can_manage_branch
from .exam import menu_denied

_HOLIDAY_LABEL = dict(HOLIDAY_KIND_CHOICES)
_WD = ["월", "화", "수", "목", "금", "토", "일"]

# 끌어오는 것들. 직접 넣는 일정과 섞이지 않게 앞에 붙임표를 둔다.
PULLED = [
    ("holiday", "휴무·공휴일", "#64748b"),
    ("exam", "자격증·대회", "#d97706"),
    ("counsel", "상담 예약", "#7c3aed"),
    ("makeup", "보강", "#2563eb"),
]


def _name_of(u):
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username if u else ""


def _month_range(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    first = date_cls(y, m, 1)
    last = date_cls(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1) - timedelta(days=1)
    return first, last


def _ev(src, key, date, title, kind, kind_label, color, time="", detail="",
        end="", ref=None, editable=False):
    return {"src": src, "key": key, "date": str(date), "wd": _WD[date.weekday()],
            "title": title, "kind": kind, "kind_label": kind_label, "color": color,
            "time": time, "detail": detail, "end": str(end) if end else "",
            "ref": ref, "editable": editable}


class CalendarAPI(APIView):
    """한 달치 일정. 직접 넣은 것 + 끌어온 것을 함께 돌려준다."""

    @admin_role_required
    def get(self, request):
        _d = menu_denied(request.user, "calendar")
        if _d:
            return self.error(_d)
        me = request.user
        ym = (request.GET.get("ym") or str((now() + timedelta(hours=9)).date())[:7]).strip()
        try:
            first, last = _month_range(ym)
        except (ValueError, IndexError):
            return self.error("달이 올바르지 않습니다(2026-09).")
        view = viewable_branch_ids(me)
        prof = AcademyProfile.objects.filter(user=me, is_deleted=False).first()
        my_branch = prof.branch_id if prof else None

        rows = []
        # ── 직접 넣은 일정 ──
        eq = CalendarEvent.objects.filter(is_deleted=False, start_date__lte=last) \
                                  .select_related("branch", "created_by")
        eq = eq.filter(**{}) if True else eq
        for e in eq:
            end = e.end_date or e.start_date
            if end < first:
                continue
            if e.scope == "PRIVATE" and e.created_by_id != me.id:
                continue
            if e.scope == "BRANCH":
                if view is not None and e.branch_id not in (view or []):
                    continue
            rows.append(_ev(
                "event", "e%d" % e.id, e.start_date, e.title,
                e.kind, "", "", time=e.start_time,
                detail=(e.branch.name if e.branch_id else ""), end=(e.end_date or ""),
                ref=e.id, editable=self._can_edit(me, e)))
            rows[-1]["scope"] = e.scope
            rows[-1]["note"] = e.note
            rows[-1]["end_time"] = e.end_time
            rows[-1]["by"] = _name_of(e.created_by) if e.created_by_id else ""

        # ── 끌어오는 것들 ──
        hq = Holiday.objects.filter(is_deleted=False, date__gte=first, date__lte=last) \
                            .select_related("branch")
        for h in hq:
            if h.branch_id and view is not None and h.branch_id not in view:
                continue
            rows.append(_ev("holiday", "h%d" % h.id, h.date, h.name, h.kind,
                            _HOLIDAY_LABEL.get(h.kind, h.kind), "#64748b",
                            detail=(h.branch.name if h.branch_id else "전 지점")))

        for e in ExamEntry.objects.filter(is_deleted=False).exclude(stage=ExamStage.JOIN_NO) \
                                  .select_related("student", "student__userprofile", "session",
                                                  "session__catalog", "catalog",
                                                  "student__academy_profile"):
            d = e.exam_date or (e.session.exam_date if e.session_id else None)
            if not d or d < first or d > last:
                continue
            p2 = getattr(e.student, "academy_profile", None)
            if view is not None and (p2.branch_id if p2 else None) not in view:
                continue
            cat = e.catalog or (e.session.catalog if e.session_id else None)
            title = (cat.name if cat else "") or (e.session.title if e.session_id else "") or "시험"
            tm = e.exam_time or (e.session.exam_time if e.session_id else "")
            rows.append(_ev("exam", "x%d" % e.id, d, title, "exam", "자격증·대회",
                            "#d97706", time=tm, detail=_name_of(e.student), ref=e.id))

        # 저장은 UTC 이고 보는 것은 KST 다. 하루 넉넉히 잡아 받고 KST 날짜로 거른다.
        for r in CounselReservation.objects.filter(
                status="ACTIVE",
                scheduled_at__gte=datetime.combine(first - timedelta(days=1), datetime.min.time(),
                                                   tzinfo=_utc),
                scheduled_at__lt=datetime.combine(last + timedelta(days=2), datetime.min.time(),
                                                  tzinfo=_utc)) \
                .select_related("lead", "lead__branch"):
            if view is not None and r.lead.branch_id not in view:
                continue
            at = r.scheduled_at + timedelta(hours=9)
            if at.date() < first or at.date() > last:
                continue
            rows.append(_ev("counsel", "c%d" % r.id, at.date(), r.lead.student_name or "상담",
                            "counsel", "상담 예약", "#7c3aed",
                            time=str(at.time())[:5], detail=(r.lead.branch.name if r.lead.branch_id else "")))

        for o in LessonOccurrence.objects.filter(is_makeup=True, date__gte=first, date__lte=last) \
                                         .select_related("student", "student__userprofile",
                                                         "student__academy_profile"):
            p2 = getattr(o.student, "academy_profile", None)
            if view is not None and (p2.branch_id if p2 else None) not in view:
                continue
            rows.append(_ev("makeup", "m%d" % o.id, o.date, _name_of(o.student), "makeup",
                            "보강", "#2563eb", time=str(o.start_time)[:5],
                            detail=(o.subject or "")))

        rows.sort(key=lambda x: (x["date"], x["time"] or "99:99", x["title"]))
        kinds = [{"value": o.value, "label": o.label, "color": (o.color or "#0f766e")}
                 for o in OptionItem.objects.filter(category="calendar_kind", is_active=True)
                                            .order_by("order", "id")]
        kmap = {k["value"]: k for k in kinds}
        for r in rows:
            if r["src"] == "event":
                k = kmap.get(r["kind"])
                r["kind_label"] = k["label"] if k else (r["kind"] or "일정")
                r["color"] = k["color"] if k else "#0f766e"
        return self.success({
            "ym": ym, "from": str(first), "to": str(last), "rows": rows,
            "kinds": kinds, "pulled": [{"value": k, "label": l, "color": c} for k, l, c in PULLED],
            "branches": [{"id": b.id, "name": b.name} for b in Branch.objects.filter(
                is_active=True, **({"id__in": view} if view is not None else {}))],
            "my_branch": my_branch,
            "can_all": (view is None),
        })

    def _can_edit(self, me, e):
        if e.scope == "PRIVATE":
            return e.created_by_id == me.id
        if e.scope == "ALL":
            return viewable_branch_ids(me) is None      # 전 지점 일정은 본부만
        return can_manage_branch(me, e.branch_id)

    @admin_role_required
    def post(self, request):
        _d = menu_denied(request.user, "calendar")
        if _d:
            return self.error(_d)
        d = request.data
        me = request.user
        title = (d.get("title") or "").strip()
        if not title:
            return self.error("제목을 적어 주세요.")
        try:
            sd = datetime.strptime(d.get("start_date"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return self.error("시작일을 정해 주세요.")
        ed = None
        if d.get("end_date"):
            try:
                ed = datetime.strptime(d["end_date"], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                ed = None
        if ed and ed < sd:
            return self.error("끝나는 날이 시작일보다 앞설 수 없습니다.")
        scope = d.get("scope") or "BRANCH"
        if scope == "ALL" and viewable_branch_ids(me) is not None:
            return self.error("전 지점 일정은 본부만 넣을 수 있습니다.")
        e = CalendarEvent.objects.filter(id=d.get("id"), is_deleted=False).first() if d.get("id") else None
        if e:
            if not self._can_edit(me, e):
                return self.error("이 일정을 고칠 권한이 없습니다.")
        else:
            e = CalendarEvent(created_by=me)
        e.scope = scope
        if scope == "BRANCH":
            bid = d.get("branch_id")
            if not bid:
                prof = AcademyProfile.objects.filter(user=me, is_deleted=False).first()
                bid = prof.branch_id if prof else None
            if not bid:
                return self.error("지점을 고르세요.")
            if not can_manage_branch(me, int(bid)):
                return self.error("이 지점에 넣을 권한이 없습니다.")
            e.branch_id = bid
        else:
            e.branch = None
        e.kind = d.get("kind") or ""
        e.title, e.start_date, e.end_date = title, sd, ed
        e.start_time = (d.get("start_time") or "")[:5]
        e.end_time = (d.get("end_time") or "")[:5]
        e.note = d.get("note") or ""
        e.save()
        return self.success({"id": e.id})

    @admin_role_required
    def delete(self, request):
        _d = menu_denied(request.user, "calendar")
        if _d:
            return self.error(_d)
        e = CalendarEvent.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not e:
            return self.error("일정이 없습니다.")
        if not self._can_edit(request.user, e):
            return self.error("이 일정을 지울 권한이 없습니다.")
        e.is_deleted = True
        e.save(update_fields=["is_deleted"])
        return self.success({"deleted": True})
