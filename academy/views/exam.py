"""자격증 · 대회 관리 API.

지금까지는 시험 정보가 학생마다 수업일지에 흩어져 있어, "누가 아직 접수 안 했지?"를
알려면 학생을 하나씩 열어봐야 했다. 참가를 한 줄씩 모아 보는 목록이 이 기능의 핵심이다.
"""
import json as _json
from datetime import timedelta, datetime

from django.db.models import Q
from django.utils.timezone import now

import os

from utils.api import APIView
from account.decorators import admin_role_required
from account.models import User

from ..models import (ExamCatalog, ExamSession, ExamEntry, ExamTeam, ExamKind, EntryMode,
                      EXAM_KIND_CHOICES, ENTRY_MODE_CHOICES, StudentProfile, EnrollmentStatus,
                      StudentCredential, ExamStage, ExamContact, ExamContactKind,
                      EXAM_STAGE_CHOICES, EXAM_CONTACT_CHOICES)
from ..services import viewable_branch_ids, can_manage_branch, can_view_branch
from .admin import DIRECTOR_UP_ROLES
from ..models import ExamChange, ExamChangeKind, EXAM_CHANGE_CHOICES, SavedSearch
from ..models import TuitionRate, TuitionRateChange, DiscountItem, Branch, AcademyRole
from ..services_brand import (KINDS as BRAND_KINDS, MAX_UPLOAD_BYTES as BRAND_MAX_BYTES,
                              ALLOWED_EXT as BRAND_EXT, brand_all, save_brand, delete_brand)

KIND_LABEL = dict(EXAM_KIND_CHOICES)
MODE_LABEL = dict(ENTRY_MODE_CHOICES)
STAGE_LABEL = dict(EXAM_STAGE_CHOICES)
CONTACT_LABEL = dict(EXAM_CONTACT_CHOICES)

# 특별시험은 우리가 날짜를 정하고 시험 이틀 전까지 접수한다
CERT_APPLY_LEAD_DAYS = 2
# 대회 일정 확인 알림을 며칠 전부터 띄울지(작년 접수 시작일 기준)
CONTEST_CHECK_LEAD_DAYS = 14


def kst_today():
    return (now() + timedelta(hours=9)).date()


def parse_date(v, default=None):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def load_list(s):
    """[{"name","fee"}] 목록으로 읽는다. 예전에 저장된 문자열 배열도 그대로 받아준다."""
    try:
        v = _json.loads(s) if s else []
    except (ValueError, TypeError):
        return []
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, dict):
            nm = str(x.get("name") or "").strip()
            if nm:
                out.append({"name": nm, "fee": x.get("fee")})
        elif str(x).strip():
            out.append({"name": str(x).strip(), "fee": None})
    return out


def parse_items(raw):
    """화면에서 온 '1급:40000, 2급' 같은 목록을 이름·응시료로 나눈다."""
    out = []
    for x in (raw or []):
        t = str(x).strip()
        if not t:
            continue
        name, fee = t, None
        if ":" in t:
            name, _, f = t.partition(":")
            name = name.strip()
            f = "".join(ch for ch in f if ch.isdigit())
            fee = int(f) if f else None
        if name:
            out.append({"name": name, "fee": fee})
    return out


def item_text(items, base_fee=None):
    """화면 입력칸에 되돌려 줄 문자열. 응시료가 다른 것만 붙인다."""
    parts = []
    for it in items:
        f = it.get("fee")
        parts.append("%s:%d" % (it["name"], f) if f else it["name"])
    return ", ".join(parts)


def kst_dt(dt):
    """저장된 UTC 를 화면용 KST 문자열로. 화면에 적는 시각은 늘 +9h."""
    return str(dt + timedelta(hours=9))[:16] if dt else ""


def name_of(u):
    if not u:
        return ""
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username


def catalog_row(c):
    return {"id": c.id, "kind": c.kind, "kind_label": KIND_LABEL.get(c.kind, c.kind),
            "name": c.name, "organizer": c.organizer, "homepage": c.homepage,
            "entry_mode": c.entry_mode, "entry_mode_label": MODE_LABEL.get(c.entry_mode, c.entry_mode),
            "apply_url": c.apply_url, "notice_url": c.notice_url,
            "fee": c.fee,
            "levels": load_list(c.levels), "tracks": load_list(c.tracks),
            "rounds": load_list(c.rounds), "venues": load_list(c.venues),
            "levels_text": item_text(load_list(c.levels)), "tracks_text": item_text(load_list(c.tracks)),
            "rounds_text": item_text(load_list(c.rounds)),
            "venues_text": item_text(load_list(c.venues)),
            "annual": c.annual, "check_from": c.check_from, "note": c.note,
            "branch_id": c.branch_id, "branch": (c.branch.name if c.branch_id else "전 지점"),
            "is_active": c.is_active, "order": c.order}


class ExamCatalogAdminAPI(APIView):
    """자격증·대회 '종류' 관리. 한 번 등록해 두고 계속 쓴다."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        qs = ExamCatalog.objects.select_related("branch")
        if request.GET.get("all") != "1":
            qs = qs.filter(is_active=True)
        if view is not None:
            qs = qs.filter(Q(branch_id=None) | Q(branch_id__in=view))
        return self.success({
            "rows": [catalog_row(c) for c in qs],
            "kinds": [{"value": k, "label": v} for k, v in EXAM_KIND_CHOICES],
            "modes": [{"value": k, "label": v} for k, v in ENTRY_MODE_CHOICES],
        })

    @admin_role_required
    def post(self, request):
        d = request.data
        cid = d.get("id")
        c = ExamCatalog.objects.filter(id=cid).first() if cid else None
        if cid and not c:
            return self.error("종류가 없습니다.")
        name = (d.get("name") or "").strip()
        if not name:
            return self.error("이름을 입력하세요.")
        bid = d.get("branch_id") or None
        if bid and not can_manage_branch(request.user, int(bid)):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        if not c:
            c = ExamCatalog(created_by=request.user)
        c.kind = d.get("kind") or ExamKind.CERT
        c.name = name
        c.organizer = (d.get("organizer") or "").strip()
        c.homepage = (d.get("homepage") or "").strip()
        c.apply_url = (d.get("apply_url") or "").strip()
        c.notice_url = (d.get("notice_url") or "").strip()
        c.entry_mode = d.get("entry_mode") or EntryMode.INDIVIDUAL
        try:
            c.fee = int(d.get("fee")) if str(d.get("fee") or "").strip() else None
        except (TypeError, ValueError):
            c.fee = None
        c.levels = _json.dumps(parse_items(d.get("levels")), ensure_ascii=False)
        c.tracks = _json.dumps(parse_items(d.get("tracks")), ensure_ascii=False)
        c.rounds = _json.dumps(parse_items(d.get("rounds")), ensure_ascii=False)
        c.venues = _json.dumps(parse_items(d.get("venues")), ensure_ascii=False)
        c.annual = bool(d.get("annual"))
        c.check_from = (d.get("check_from") or "").strip()[:8]
        c.note = (d.get("note") or "").strip()[:255]
        c.branch_id = int(bid) if bid else None
        if "is_active" in d:
            c.is_active = bool(d.get("is_active"))
        try:
            c.order = int(d.get("order") or 0)
        except (TypeError, ValueError):
            c.order = 0
        c.save()
        return self.success(catalog_row(c))

    @admin_role_required
    def delete(self, request):
        """실제로 지우지 않고 숨긴다(이미 쌓인 참가 기록을 살려두기 위함)."""
        # 만들고 고치는 건 시험을 굴리는 선생님이 하지만, 치우는 건 원장 이상만 한다.
        # 남이 쓰던 종류를 치우면 그 회차·참가 기록이 한꺼번에 안 보이게 된다.
        prof = getattr(request.user, "academy_profile", None)
        role = prof.role if prof else ""
        if not (role in DIRECTOR_UP_ROLES or request.user.is_super_admin()):
            return self.error("원장 이상만 치울 수 있습니다.")
        c = ExamCatalog.objects.filter(id=request.GET.get("id")).first()
        if not c:
            return self.error("종류가 없습니다.")
        if c.branch_id and not can_manage_branch(request.user, c.branch_id):
            return self.error("권한이 없습니다.")
        c.is_active = False
        c.save(update_fields=["is_active"])
        return self.success("ok")


# ─────────────────────── 회차 ───────────────────────

def limit_entries_to_my_students(qs, user):
    """참가는 학생 기준으로 거른다.

    시험·대회 일정은 전 지점이 함께 쓴다(같은 날 같은 시험을 본다). 그래서 회차에는
    지점을 두지 않는데, 그대로 두면 다른 지점 학생까지 보인다. 붙어 있는 학생이
    우리 지점 학생인지로 걸러야 맞다."""
    view = viewable_branch_ids(user)
    if view is None:
        return qs
    return qs.filter(student__academy_profile__branch_id__in=view)


def log_exam_change(sn, kind, actor, detail="", entries=None):
    """회차 등록·수정·삭제를 남긴다. 회차가 지워져도 읽히게 이름을 글로 함께 적는다."""
    names = [name_of(e.student) for e in (entries or [])]
    ExamChange.objects.create(
        session=sn, kind=kind, exam_kind=sn.kind, label=session_label(sn)[:160],
        detail=detail[:2000], entry_count=len(names),
        students=", ".join(names)[:2000], branch_id=sn.branch_id, actor=actor)


def _hhmm(v):
    """시각을 HH:MM 로 맞춘다. 못 읽으면 빈 값(미정)으로 둔다."""
    t = (v or "").strip().replace("：", ":")
    if not t:
        return ""
    if ":" not in t and t.isdigit():          # 1430 처럼 적은 경우
        t = t.rjust(4, "0")
        t = t[:2] + ":" + t[2:]
    try:
        h, m = t.split(":")[:2]
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return ""
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return ""
    return "%02d:%02d" % (h, m)


def session_label(sn):
    """목록에 쓸 이름. 자격증 특별시험은 날짜가 곧 이름이라 제목이 없어도 읽히게 만든다."""
    if sn.title:
        return sn.title
    if sn.catalog_id and sn.catalog:
        return sn.catalog.name
    return "특별시험"


def session_row(sn, counts=None):
    c = counts or {}
    d = sn.exam_date
    today = kst_today()
    return {
        "id": sn.id, "kind": sn.kind, "kind_label": KIND_LABEL.get(sn.kind, sn.kind),
        "catalog_id": sn.catalog_id, "catalog": (sn.catalog.name if sn.catalog_id else ""),
        "title": sn.title, "label": session_label(sn),
        "exam_date": str(d) if d else "", "exam_time": sn.exam_time, "confirmed": sn.confirmed,
        "submit_from": str(sn.submit_from) if sn.submit_from else "",
        # 온라인이면 '대회일' 이 아니라 '제출 마감' 이다. 화면이 이름표를 바꿔 쓴다.
        "online": ("온라인" in (sn.place or "")),
        "apply_from": str(sn.apply_from) if sn.apply_from else "",
        "apply_until": str(sn.apply_until) if sn.apply_until else "",
        "result_date": str(sn.result_date) if sn.result_date else "",
        # 안 적었으면 시험일 당일에 나오는 것으로 본다(자체 시험은 끝나면 바로 결과가 나온다)
        "result_date_eff": str(sn.result_date or d) if (sn.result_date or d) else "",
        "result_same_day": bool(sn.exam_date and (sn.result_date or sn.exam_date) == sn.exam_date),
        "entry_mode": sn.entry_mode or (sn.catalog.entry_mode if sn.catalog_id else ""),
        "level": sn.level, "track": sn.track, "round": sn.round,
        "place": sn.place, "fee": sn.fee, "note": sn.note,
        "branch_id": sn.branch_id, "branch": (sn.branch.name if sn.branch_id else ""),
        "d_exam": (d - today).days if d else None,
        "d_apply": ((sn.apply_until - today).days if sn.apply_until else None),
        "total": c.get("total", 0), "applied": c.get("applied", 0),
        "pending": c.get("total", 0) - c.get("applied", 0),
    }


class ExamSessionAdminAPI(APIView):
    """회차(시험일·대회) 등록·수정·삭제."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        qs = ExamSession.objects.select_related("catalog", "branch").filter(is_deleted=False)
        if view is not None:
            qs = qs.filter(Q(branch_id=None) | Q(branch_id__in=view))
        if request.GET.get("kind"):
            qs = qs.filter(kind=request.GET["kind"])
        if request.GET.get("upcoming") == "1":
            qs = qs.filter(Q(exam_date__gte=kst_today()) | Q(exam_date__isnull=True))
        counts = {}
        eq = limit_entries_to_my_students(
            ExamEntry.objects.filter(session__in=qs, is_deleted=False), request.user)
        for e in eq.values("session_id", "applied"):
            c = counts.setdefault(e["session_id"], {"total": 0, "applied": 0})
            c["total"] += 1
            if e["applied"]:
                c["applied"] += 1
        rows = [session_row(sn, counts.get(sn.id)) for sn in qs]
        rows.sort(key=lambda r: (r["exam_date"] or "9999-99-99"))
        return self.success(rows)

    @admin_role_required
    def post(self, request):
        d = request.data
        sid = d.get("id")
        sn = ExamSession.objects.filter(id=sid, is_deleted=False).first() if sid else None
        if sid and not sn:
            return self.error("회차가 없습니다.")
        kind = d.get("kind") or ExamKind.CERT
        exam_date = parse_date(d.get("exam_date"))
        cid = d.get("catalog_id") or None
        if not cid:
            return self.error("어떤 자격증인지 고르세요." if kind == ExamKind.CERT
                              else "어떤 대회인지 고르세요.")
        if not sn:
            sn = ExamSession(created_by=request.user)
            # 자격증은 학생마다 보는 날이 달라 날짜별로 회차를 만들면 같은 자격증이 여러 개로
            # 갈라진다. 종류마다 하나만 두고 날짜는 학생 쪽에 둔다.
            if kind == ExamKind.CERT:
                dup = ExamSession.objects.filter(kind=ExamKind.CERT, catalog_id=int(cid),
                                                 is_deleted=False).first()
                if dup:
                    sn = dup
        sn.kind = kind
        sn.catalog_id = int(cid)
        sn.title = (d.get("title") or "").strip()[:128]
        sn.exam_date = exam_date
        sn.apply_from = parse_date(d.get("apply_from"))
        au = parse_date(d.get("apply_until"))
        if au is None and kind == ExamKind.CERT and exam_date:
            # 특별시험은 시험 이틀 전까지 접수 — 비워 두면 자동으로 채운다
            au = exam_date - timedelta(days=CERT_APPLY_LEAD_DAYS)
        sn.apply_until = au
        # 발표일을 비워 두면 시험일 당일로 본다. 다만 저장까지 해 버리면 '미정'으로 둔 것이
        # 정해진 것처럼 보이므로, 채우지 않고 보여줄 때만 시험일을 쓴다.
        sn.result_date = parse_date(d.get("result_date"))
        sn.exam_time = _hhmm(d.get("exam_time"))
        sn.submit_from = parse_date(d.get("submit_from"))
        sn.entry_mode = (d.get("entry_mode") or "").strip()
        sn.level = (d.get("level") or "").strip()[:64]
        sn.track = (d.get("track") or "").strip()[:64]
        sn.round = (d.get("round") or "").strip()[:64]
        sn.place = (d.get("place") or "").strip()[:128]
        try:
            sn.fee = int(d.get("fee")) if str(d.get("fee") or "").strip() else None
        except (TypeError, ValueError):
            sn.fee = None
        sn.note = (d.get("note") or "").strip()[:255]
        # 날짜가 서로 따로 논다. 주최측이 대회일만 내고 접수·발표는 나중에 내는 일이 잦아
        # '미정'을 날짜마다 따로 둔다. 비어 있으면 그 날짜가 미정이라는 뜻.
        # 회차 전체의 확정 여부는 대회일이 잡혔는지로 본다.
        sn.confirmed = bool(sn.exam_date) if kind == ExamKind.CONTEST else True
        bid = d.get("branch_id") or None
        if bid and not can_manage_branch(request.user, int(bid)):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        sn.branch_id = int(bid) if bid else None
        is_new = not sn.id
        sn.save()
        bits = []
        if sn.exam_date: bits.append("시험일 " + str(sn.exam_date))
        if sn.exam_time: bits.append(sn.exam_time)
        if sn.apply_until: bits.append("접수 마감 " + str(sn.apply_until))
        log_exam_change(sn, ExamChangeKind.CREATE if is_new else ExamChangeKind.UPDATE,
                        request.user, " · ".join(bits))
        return self.success(session_row(sn))

    @admin_role_required
    def delete(self, request):
        sn = ExamSession.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not sn:
            return self.error("회차가 없습니다.")
        if sn.branch_id and not can_manage_branch(request.user, sn.branch_id):
            return self.error("권한이 없습니다.")
        # 함께 내려가는 학생을 이력에 남긴다(지운 뒤에는 누가 붙어 있었는지 알 수 없다)
        kids = list(ExamEntry.objects.filter(session=sn, is_deleted=False)
                    .select_related("student", "student__userprofile"))
        log_exam_change(sn, ExamChangeKind.DELETE, request.user,
                        ("시험일 " + str(sn.exam_date)) if sn.exam_date else "", kids)
        sn.is_deleted = True
        sn.save(update_fields=["is_deleted"])
        ExamEntry.objects.filter(session=sn, is_deleted=False).update(
            is_deleted=True, deleted_at=now(), deleted_by=request.user, deleted_reason="SESSION")
        return self.success("ok")


# ─────────────────────── 참가(목록의 한 줄) ───────────────────────

def entry_row(e):
    sn = e.session
    cat = e.catalog or sn.catalog
    today = kst_today()
    what = []
    if cat:
        what.append(cat.name)
    if e.level:
        what.append(e.level)
    if e.track:
        what.append(e.track)
    if e.team_id:
        what.append("팀 " + e.team.name)
    ed = e.exam_date or sn.exam_date
    au = e.apply_until or sn.apply_until
    rd = e.result_date or sn.result_date or ed
    return {
        "id": e.id, "session_id": sn.id, "kind": sn.kind,
        "kind_label": KIND_LABEL.get(sn.kind, sn.kind),
        "student_id": e.student_id, "student": name_of(e.student),
        "catalog_id": (cat.id if cat else None), "catalog": (cat.name if cat else ""),
        "level": e.level, "track": e.track,
        "team_id": e.team_id, "team": (e.team.name if e.team_id else ""),
        "what": " ".join(what) or session_label(sn),
        "session_label": session_label(sn),
        # 자격증은 학생 쪽 날짜가 먼저다. 없으면 회차 것을 쓴다(대회는 늘 회차 것).
        "exam_date": str(ed) if ed else "",
        "confirmed": sn.confirmed,
        "apply_until": str(au) if au else "",
        "result_date": str(rd) if rd else "",
        "place": e.place or sn.place,
        "exam_time": e.exam_time or sn.exam_time,
        "round": e.round or sn.round,
        "d_exam": ((ed - today).days if ed else None),
        "d_apply": ((au - today).days if au else None),
        "stage": e.stage, "stage_label": stage_text(e),
        "cancelled": bool(e.is_deleted),
        "cancelled_at": (str(e.deleted_at + timedelta(hours=9))[:16] if e.deleted_at else ""),
        "cancelled_by": (name_of(e.deleted_by) if e.deleted_by_id else ""),
        "cancelled_reason": e.deleted_reason,
        "contacts": [{"kind": c.kind, "kind_label": CONTACT_LABEL.get(c.kind, c.kind),
                      "note": c.note, "actor": name_of(c.actor),
                      "time": str(c.create_time + timedelta(hours=9))[:16]}
                     for c in e.contacts.all()[:20]],
        "last_contact": _last_contact(e),
        "applied": e.applied, "applied_at": str(e.applied_at) if e.applied_at else "",
        "entry_mode": sn.entry_mode or (sn.catalog.entry_mode if sn.catalog_id else
                                        (cat.entry_mode if cat else "")),
        "fee_paid": e.fee_paid,
        "credential_id": e.credential_id,
        "credential": ((e.credential.site + " / " + e.credential.login_id) if e.credential_id else ""),
        "fee": entry_fee(e, cat, sn),
        "result": e.result, "score": e.score, "note": e.note,
        "instructor": name_of(e.instructor) if e.instructor_id else "",
        "phone": _entry_phone(e),
    }


def entry_fee(e, cat, sn):
    """이 참가의 응시료. 부문 → 급수 → 종류 기본값 → 회차 순으로 정한다.
    급수마다, 대회는 부문마다 응시료가 다른 경우가 있어 좁은 쪽을 먼저 본다."""
    if cat:
        for items, want in ((load_list(cat.tracks), e.track), (load_list(cat.levels), e.level)):
            if not want:
                continue
            for it in items:
                if it["name"] == want and it.get("fee"):
                    return it["fee"]
        if cat.fee:
            return cat.fee
    return sn.fee


def stage_text(e):
    """상태 표시. 같은 안내를 여러 번 보냈으면 '접수 안내(3차)'처럼 차수를 붙인다."""
    base = STAGE_LABEL.get(e.stage, e.stage)
    if e.applied:
        return STAGE_LABEL[ExamStage.APPLIED]
    kind = {ExamStage.NOTIFIED: ExamContactKind.NOTIFY,
            ExamStage.APPLY_GUIDE: ExamContactKind.APPLY_GUIDE}.get(e.stage)
    if kind:
        n = sum(1 for c in e.contacts.all() if c.kind == kind)
        if n > 1:
            return "%s(%d차)" % (base, n)
    return base


def _last_contact(e):
    c = next(iter(e.contacts.all()), None)
    if not c:
        return ""
    return "%s %s" % (str(c.create_time + timedelta(hours=9))[5:10], CONTACT_LABEL.get(c.kind, c.kind))


def _entry_phone(e):
    sp = getattr(e.student, "student_profile", None)
    return (sp.parent_phone if sp else "") or ""


class ExamEntryAdminAPI(APIView):
    """참가 목록 — 이 기능의 메인 화면. '누구 / 무엇 / 언제 / 접수했나'를 한 줄씩."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        qs = ExamEntry.objects.select_related(
            "session", "session__catalog", "session__branch", "catalog", "team",
            "student", "student__userprofile", "student__student_profile",
            "credential", "instructor", "deleted_by", "deleted_by__userprofile").prefetch_related(
            "contacts", "contacts__actor")
        if request.GET.get("include_deleted") == "1":
            # 도전하려다 그만둔 것도 이력이라 학생 화면에서는 함께 본다.
            # 이 기능을 만들기 전에 지운 것은 지운 때가 없지만 그래도 보여야 한다.
            pass
        else:
            qs = qs.filter(is_deleted=False, session__is_deleted=False)
        qs = limit_entries_to_my_students(qs, request.user)
        if request.GET.get("session_id"):
            qs = qs.filter(session_id=request.GET["session_id"])
        if request.GET.get("student_id"):
            qs = qs.filter(student_id=request.GET["student_id"])
        rows = [entry_row(e) for e in qs[:1000]]
        # 시험일이 가까운 순. 날짜 미정(대회 미확정)은 뒤로.
        # 시험일이 먼저, 같은 날이면 명칭(무엇을 보는지), 그 다음 학생 이름
        rows.sort(key=lambda r: (r["exam_date"] or "9999-99-99", r.get("what") or "", r["student"]))
        return self.success(rows)

    @admin_role_required
    def post(self, request):
        """참가 추가·수정. student_ids 로 여러 명을 한 번에 붙일 수 있다."""
        d = request.data
        eid = d.get("id")
        if eid:
            e = limit_entries_to_my_students(
                ExamEntry.objects.filter(id=eid, is_deleted=False), request.user).first()
            if not e:
                return self.error("참가 기록이 없습니다.")
            self._apply(e, d, request.user)
            e.save()
            return self.success(entry_row(ExamEntry.objects.select_related(
                "session", "session__catalog", "catalog", "team", "student",
                "student__userprofile", "student__student_profile", "credential",
                "instructor").get(id=e.id)))

        sn = ExamSession.objects.filter(id=d.get("session_id"), is_deleted=False).first()
        if not sn:
            return self.error("회차가 없습니다.")
        view = viewable_branch_ids(request.user)
        ids = d.get("student_ids") or ([d.get("student_id")] if d.get("student_id") else [])
        if not ids:
            return self.error("학생을 고르세요.")
        made, skipped = 0, []
        for sid in ids:
            u = User.objects.filter(id=sid).first()
            if not u:
                continue
            # 남의 지점 학생을 붙이지 못하게 막는다(목록에서는 어차피 안 보이지만 요청은 올 수 있다)
            if view is not None:
                sp = getattr(u, "academy_profile", None)
                if not sp or sp.branch_id not in view:
                    skipped.append(name_of(u))
                    continue
            if ExamEntry.objects.filter(session=sn, student=u, is_deleted=False).exists():
                skipped.append(name_of(u))
                continue
            e = ExamEntry(session=sn, student=u, created_by=request.user)
            self._apply(e, d, request.user)
            e.save()
            made += 1
        return self.success({"made": made, "skipped": skipped})

    @staticmethod
    def _apply(e, d, actor):
        if "catalog_id" in d:
            e.catalog_id = int(d["catalog_id"]) if d.get("catalog_id") else None
        if "level" in d:
            e.level = (d.get("level") or "").strip()[:16]
        if "track" in d:
            e.track = (d.get("track") or "").strip()[:32]
        if "round" in d:
            e.round = (d.get("round") or "").strip()[:64]
        if "place" in d:
            e.place = (d.get("place") or "").strip()[:128]
        if "exam_time" in d:
            e.exam_time = _hhmm(d.get("exam_time"))
        # 자격증은 학생마다 보는 날이 다르다. 비워 두면 회차 날짜를 그대로 쓴다.
        if "exam_date" in d:
            e.exam_date = parse_date(d.get("exam_date"))
        if "apply_until" in d:
            e.apply_until = parse_date(d.get("apply_until"))
        if "result_date" in d:
            e.result_date = parse_date(d.get("result_date"))
        # 접수 마감을 안 적으면 시험 이틀 전으로 잡는다(회차에서 하던 것과 같은 규칙)
        if e.exam_date and not e.apply_until:
            e.apply_until = e.exam_date - timedelta(days=2)
        if "team_id" in d:
            e.team_id = int(d["team_id"]) if d.get("team_id") else None
        if "stage" in d and d.get("stage"):
            e.stage = d["stage"]
        if "applied" in d:
            e.applied = bool(d.get("applied"))
            e.applied_at = kst_today() if e.applied else None
            if d.get("applied_at"):
                e.applied_at = parse_date(d["applied_at"], e.applied_at)
            # 접수하면 단계도 함께 올린다. 되돌리면 접수 안내 상태로 돌아간다.
            e.stage = ExamStage.APPLIED if e.applied else (
                e.stage if e.stage not in (ExamStage.APPLIED,) else ExamStage.APPLY_GUIDE)
        if "credential_id" in d:
            e.credential_id = int(d["credential_id"]) if d.get("credential_id") else None
        if "fee_paid" in d:
            e.fee_paid = bool(d.get("fee_paid"))
        for f, mx in (("result", 32), ("score", 32), ("note", 255)):
            if f in d:
                setattr(e, f, (d.get(f) or "").strip()[:mx])
        if "instructor_id" in d:
            e.instructor_id = int(d["instructor_id"]) if d.get("instructor_id") else None

    @admin_role_required
    def delete(self, request):
        e = limit_entries_to_my_students(
            ExamEntry.objects.filter(id=request.GET.get("id"), is_deleted=False), request.user).first()
        if not e:
            return self.error("참가 기록이 없습니다.")
        e.is_deleted = True
        e.deleted_at = now()
        e.deleted_by = request.user
        e.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])
        return self.success("ok")


class ExamChangeAdminAPI(APIView):
    """회차 등록·수정·삭제 이력. 지웠다 다시 만드는 일이 잦아 나중에 되짚을 일이 생긴다.

    누가 무엇을 지웠는지는 운영 책임에 걸리는 내용이라 원장 이상만 본다."""

    @admin_role_required
    def get(self, request):
        prof = getattr(request.user, "academy_profile", None)
        role = prof.role if prof else ""
        if not (role in DIRECTOR_UP_ROLES or request.user.is_super_admin()):
            return self.error("원장 이상만 볼 수 있습니다.")
        view = viewable_branch_ids(request.user)
        qs = ExamChange.objects.select_related("actor", "actor__userprofile", "branch")
        if view is not None:
            qs = qs.filter(Q(branch_id=None) | Q(branch_id__in=view))
        rows = [{
            "id": c.id, "kind": c.kind, "kind_label": dict(EXAM_CHANGE_CHOICES).get(c.kind, c.kind),
            "exam_kind": c.exam_kind,
            "exam_kind_label": KIND_LABEL.get(c.exam_kind, c.exam_kind),
            "label": c.label, "detail": c.detail,
            "entry_count": c.entry_count, "students": c.students,
            "branch": (c.branch.name if c.branch_id else ""),
            "actor": name_of(c.actor) if c.actor_id else "",
            "time": str(c.create_time + timedelta(hours=9))[:16],
        } for c in qs[:300]]
        return self.success(rows)


class ExamContactAdminAPI(APIView):
    """연락 기록. 여러 명에게 같은 안내를 한 번에 남긴다(문자를 복사해 보낸 뒤 기록).

    기록을 남기면 단계도 함께 올라간다 — 안내를 보냈는데 상태가 그대로면
    '보냈는지 안 보냈는지'를 또 헷갈리게 되기 때문."""

    @admin_role_required
    def post(self, request):
        d = request.data
        ids = d.get("entry_ids") or ([d.get("entry_id")] if d.get("entry_id") else [])
        if not ids:
            return self.error("대상을 고르세요.")
        kind = d.get("kind") or ExamContactKind.APPLY_GUIDE
        note = (d.get("note") or "").strip()[:255]
        stage = {ExamContactKind.NOTIFY: ExamStage.NOTIFIED,
                 ExamContactKind.APPLY_GUIDE: ExamStage.APPLY_GUIDE}.get(kind)
        made = 0
        for e in ExamEntry.objects.filter(id__in=ids, is_deleted=False):
            ExamContact.objects.create(entry=e, kind=kind, note=note, actor=request.user)
            # 이미 접수한 사람은 단계를 되돌리지 않는다
            if stage and not e.applied:
                e.stage = stage
                e.save(update_fields=["stage"])
            made += 1
        return self.success({"made": made})

    @admin_role_required
    def delete(self, request):
        c = ExamContact.objects.filter(id=request.GET.get("id")).first()
        if not c:
            return self.error("기록이 없습니다.")
        c.delete()
        return self.success("ok")


class ExamStageAdminAPI(APIView):
    """참여 의사 등 단계만 바꾼다(대회의 참여/불참 표시)."""

    @admin_role_required
    def post(self, request):
        d = request.data
        ids = d.get("entry_ids") or ([d.get("entry_id")] if d.get("entry_id") else [])
        stage = d.get("stage")
        if not ids or stage not in dict(EXAM_STAGE_CHOICES):
            return self.error("값이 올바르지 않습니다.")
        n = ExamEntry.objects.filter(id__in=ids, is_deleted=False).update(stage=stage)
        return self.success({"changed": n})


# ─────────────────────── 포털 메뉴 켜고 끄기 ───────────────────────

from ..models import (MenuSetting, MenuOverride, AcademyProfile, ACADEMY_ROLE_CHOICES,
                      STAFF_ROLES, AcademyRole)
from ..services import editable_branch_ids

# 화면의 메뉴 순서와 같게 둔다. always=True 는 끌 수 없다 —
# 학원 관리를 꺼버리면 되돌릴 방법이 없어지기 때문.
# floor: 코드에서 이미 막고 있는 최소 권한. 메뉴 설정으로 더 열 수는 없고 더 좁힐 수만 있다
# (직원 관리에는 인사 정보가 들어 있어 화면 설정만으로 열려서는 안 된다).
MENU_DEFS = [
    {"key": "dashboard", "label": "오늘 운영", "always": True},
    {"key": "leadmgr", "label": "신규 상담·등록"},
    {"key": "students", "label": "학생 관리"},
    {"key": "indtt", "label": "시간표"},
    {"key": "makeup", "label": "보강 관리"},
    {"key": "classes", "label": "그룹·특강"},
    {"key": "exam", "label": "자격증·대회"},
    {"key": "staff", "label": "직원 관리", "floor": "원장 이상",
     "floor_roles": ["HQ_ADMIN", "HR_ADMIN", "BRANCH_MANAGER"]},
    {"key": "hr", "label": "내 정보", "always": True},
    {"key": "options", "label": "학원 관리", "always": True, "floor": "원장 이상",
     "floor_roles": ["HQ_ADMIN", "HR_ADMIN", "REGIONAL_MANAGER", "BRANCH_MANAGER"]},
    {"key": "msgtpl", "label": "문자 템플릿"},
    {"key": "devboard", "label": "개발 요청"},
    {"key": "devlog", "label": "개발일지", "floor": "본부 관리자", "floor_roles": []},
]
MENU_ALWAYS = {m["key"] for m in MENU_DEFS if m.get("always")}
ROLE_LABEL = dict(ACADEMY_ROLE_CHOICES)

# 원장보다 위(본부관리자·인사관리자·지부장)는 언제나 전체 메뉴를 본다.
# 위에 있는 사람이 아래 설정 때문에 화면을 못 보는 상황은 없어야 하므로 제한 대상에서 뺀다.
MENU_SUPER_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN, AcademyRole.REGIONAL_MANAGER}
# 직급 제한을 걸 수 있는 대상(원장 이하)
MENU_LIMITABLE_ROLES = [r for r in STAFF_ROLES if r not in MENU_SUPER_ROLES]


def _floor_ok(d, role, is_super):
    """코드에서 정해 둔 최소 권한을 만족하는가. floor_roles 가 없으면 제한 없음."""
    if "floor_roles" not in d:
        return True
    if is_super:
        return True
    return role in set(d["floor_roles"])


def menu_setting_for(key_map, key, branch_id):
    """지점 설정이 있으면 그것, 없으면 전 지점 기본값."""
    return key_map.get((key, branch_id)) or key_map.get((key, None))


def menu_allowed_keys(user):
    """이 사람이 볼 수 있는 메뉴 키. 개인 예외 > 직급 제한 > 지점 설정 > 전 지점 기본값."""
    prof = getattr(user, "academy_profile", None)
    role = prof.role if prof else ""
    is_super = user.is_super_admin()
    if role in MENU_SUPER_ROLES or is_super:
        # 상위 직급도 코드로 막아 둔 곳(개발일지)은 그대로 따른다
        return [d["key"] for d in MENU_DEFS
                if _floor_ok(d, role, is_super)]
    branch_id = prof.branch_id if prof else None
    key_map = {(m.key, m.branch_id): m for m in MenuSetting.objects.all()}
    over = {o.key: o.allow for o in MenuOverride.objects.filter(staff=user)}
    out = []
    for d in MENU_DEFS:
        k = d["key"]
        # 코드에서 막아 둔 것은 설정으로도 열 수 없다(직원 관리엔 인사 정보가 들어 있음)
        if not _floor_ok(d, role, is_super):
            continue
        if k in MENU_ALWAYS:
            out.append(k)
            continue
        if k in over:
            if over[k]:
                out.append(k)
            continue
        st = menu_setting_for(key_map, k, branch_id)
        if st and not st.enabled:
            continue
        # 저장된 설정이 없으면 기본 제한을 쓴다(직원 관리는 원장 이상만 보는 게 기본)
        roles = load_list_plain(st.roles) if st else list(d.get("default_roles") or [])
        if roles and role not in roles:
            continue
        out.append(k)
    return out


def menu_scope_branch(user):
    """설정을 어느 지점에 저장·조회할지. 전지점 역할은 전 지점 기본값(None)을 다룬다."""
    if editable_branch_ids(user) is None:
        return None
    prof = getattr(user, "academy_profile", None)
    return prof.branch_id if prof else None


def load_list_plain(s):
    try:
        v = _json.loads(s) if s else []
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


class MyMenuAPI(APIView):
    """로그인한 사람이 볼 수 있는 메뉴 목록."""

    @admin_role_required
    def get(self, request):
        return self.success({"keys": menu_allowed_keys(request.user)})


# 보통 이 학년이 이 시간을 한다는 안내일 뿐, 강제하지 않는다.
# 초등학생에게 중학생 시간·금액을 적용하는 경우가 실제로 있다.
TUITION_TIERS = [
    ("초등 1·2학년", [(1, 50), (2, 50)]),
    ("초등 3~6학년", [(1, 90), (2, 60)]),
    ("중학생 이상", [(1, 120), (2, 90)]),
]
TUITION_STD = [c for _lb, cs in TUITION_TIERS for c in cs]
TUITION_WEEKS = [1, 2]


def _tuition_tier(wk, du):
    for lb, cs in TUITION_TIERS:
        if (wk, du) in cs:
            return lb
    return ""


class TuitionRateAdminAPI(APIView):
    """원비 기준표 — 지점 × 주횟수 × 회당 시간 → 한 달 금액."""

    @admin_role_required
    def get(self, request):
        me = request.user
        view = viewable_branch_ids(me)
        bq = Branch.objects.filter(is_active=True)
        if view is not None:
            bq = bq.filter(id__in=view)
        branches = list(bq.order_by("id"))
        have = {(r.branch_id, r.sessions_per_week, r.duration_minutes): r
                for r in TuitionRate.objects.filter(branch_id__in=[b.id for b in branches])}
        # 시간표에 실제로 있는 조합(표에 없더라도 알려 줘야 한다)
        used = _tuition_used_combos([b.id for b in branches])
        counts = _tuition_people_counts([b.id for b in branches])
        rows = []
        for b in branches:
            # 기본 구간을 사람이 보는 순서대로 먼저, 그 밖의 시간을 뒤에.
            extra = sorted(c for c in used.get(b.id, []) if c not in TUITION_STD and c[0] <= 2)
            # 주3회 이상은 회당 단가로 계산하므로 표에 금액 칸이 필요 없다(아래 목록으로 따로 본다)
            for group, combos in (("std", TUITION_STD), ("extra", extra)):
                for wk, du in combos:
                    r = have.get((b.id, wk, du))
                    rows.append({
                        "branch_id": b.id, "branch": b.name, "group": group,
                        "tier": _tuition_tier(wk, du),
                        "sessions_per_week": wk, "duration_minutes": du,
                        "amount": (r.amount if r else 0),
                        "note": (r.note if r else ""),
                        "people": counts.get((b.id, wk, du), 0),
                    })
        # 회당 단가로 계산되는 사람(주3회 이상·회당 시간 섞임)은 표의 칸에 담기지 않는다
        by_unit = _tuition_by_unit([b.id for b in branches])
        hist = [{"branch": (c.branch.name if c.branch_id else "전 지점"), "detail": c.detail,
                 "reason": c.reason, "actor": name_of(c.actor) if c.actor_id else "",
                 "time": kst_dt(c.create_time)}
                for c in TuitionRateChange.objects.select_related("branch", "actor")[:100]]
        return self.success({"rows": rows,
                             "branches": [{"id": b.id, "name": b.name} for b in branches],
                             "by_unit": by_unit,
                             "tiers": [lb for lb, _ in TUITION_TIERS],
                             "history": hist})

    @admin_role_required
    def post(self, request):
        """{branch_id, sessions_per_week, duration_minutes, amount, note?, reason?}"""
        d = request.data
        b = Branch.objects.filter(id=d.get("branch_id")).first()
        if not b:
            return self.error("지점이 없습니다.")
        if not can_manage_branch(request.user, b.id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        try:
            wk = int(d.get("sessions_per_week"))
            du = int(d.get("duration_minutes"))
            amount = int(str(d.get("amount") or 0).replace(",", "") or 0)
        except (TypeError, ValueError):
            return self.error("값이 올바르지 않습니다.")
        if wk < 1 or du < 1 or amount < 0:
            return self.error("값이 올바르지 않습니다.")
        r = TuitionRate.objects.filter(branch=b, sessions_per_week=wk, duration_minutes=du).first()
        old_amt = r.amount if r else 0
        if r:
            r.amount = amount
            r.note = (d.get("note") or "").strip()
            r.updated_by = request.user
            r.save(update_fields=["amount", "note", "updated_by", "update_time"])
        else:
            r = TuitionRate.objects.create(branch=b, sessions_per_week=wk, duration_minutes=du,
                                           amount=amount, note=(d.get("note") or "").strip(),
                                           updated_by=request.user)
        if old_amt != amount:
            TuitionRateChange.objects.create(
                branch=b, actor=request.user, reason=(d.get("reason") or "").strip(),
                detail="주%d회 %d분: %s → %s원" % (wk, du, format(old_amt, ","), format(amount, ",")))
        return self.success({"ok": True})


class DiscountItemAdminAPI(APIView):
    """할인 항목 — 여러 개를 겹쳐 붙일 수 있다. 정액 먼저 빼고 비율을 적용한다."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        qs = DiscountItem.objects.select_related("branch")
        if view is not None:
            qs = qs.filter(Q(branch__isnull=True) | Q(branch_id__in=view))
        rows = [{"id": x.id, "name": x.name, "kind": x.kind, "value": x.value,
                 "recurring": x.recurring, "branch_id": x.branch_id,
                 "branch": (x.branch.name if x.branch_id else ""), "is_active": x.is_active,
                 "note": x.note} for x in qs]
        return self.success({"rows": rows})

    @admin_role_required
    def post(self, request):
        d = request.data
        if not _is_director_up_exam(request.user):
            return self.error("원장 이상만 고칠 수 있습니다.")
        x = DiscountItem.objects.filter(id=d.get("id")).first() if d.get("id") else None
        name = (d.get("name") or "").strip()
        if not x and not name:
            return self.error("할인 이름을 적어 주세요.")
        try:
            value = int(str(d.get("value") or 0).replace(",", "") or 0)
        except (TypeError, ValueError):
            return self.error("값이 올바르지 않습니다.")
        kind = d.get("kind") if d.get("kind") in ("AMOUNT", "PERCENT") else "AMOUNT"
        if kind == "PERCENT" and not (0 <= value <= 100):
            return self.error("비율은 0~100 사이여야 합니다.")
        bid = d.get("branch_id") or None
        if x:
            if name:
                x.name = name
            x.kind = kind
            x.value = value
            x.recurring = bool(d.get("recurring", x.recurring))
            x.branch_id = bid
            x.note = (d.get("note") or "").strip()
            if "is_active" in d:
                x.is_active = bool(d.get("is_active"))
            x.save()
        else:
            x = DiscountItem.objects.create(
                name=name, kind=kind, value=value, branch_id=bid,
                recurring=bool(d.get("recurring", True)), note=(d.get("note") or "").strip())
        return self.success({"id": x.id})

    @admin_role_required
    def delete(self, request):
        if not _is_director_up_exam(request.user):
            return self.error("원장 이상만 지울 수 있습니다.")
        x = DiscountItem.objects.filter(id=request.GET.get("id")).first()
        if not x:
            return self.error("항목이 없습니다.")
        x.is_active = False          # 지우지 않고 끈다 — 이미 붙은 것은 그대로 둔다
        x.save(update_fields=["is_active"])
        return self.success({"ok": True})


def _tuition_used_combos(branch_ids):
    """지점마다 시간표에 실제로 있는 (주횟수, 회당시간) 조합."""
    out = {}
    for bid, combos in _tuition_student_combos(branch_ids).items():
        out[bid] = sorted({c for c, _n in combos.items()})
    return out


def _tuition_people_counts(branch_ids):
    cnt = {}
    for bid, combos in _tuition_student_combos(branch_ids).items():
        for c, n in combos.items():
            cnt[(bid, c[0], c[1])] = n
    return cnt


def _tuition_student_combos(branch_ids):
    """지점 → {(주횟수, 회당시간): 인원}. 회당 시간이 섞인 학생은 여기서 빠진다
    (그 학생은 회차별로 나눠 더하므로 표의 한 칸에 담기지 않는다).

    요일을 옮기면 옛 줄은 기간만 끊기고 status 는 ACTIVE 로 남는다. status 만 보면
    끝난 줄까지 세어 주1회가 주2회로 둔갑한다(고은결·심규민이 그랬다).
    반드시 '오늘 적용중인지'로 걸러야 한다."""
    from ..models import StudentTimetable, StudentProfile, AcademyProfile, EnrollmentStatus
    from .admin import _slot_active_on
    today = (now() + timedelta(hours=9)).date()
    enrolled = set(StudentProfile.objects.filter(
        enrollment_status=EnrollmentStatus.ENROLLED).values_list("user_id", flat=True))
    branch_of = {a.user_id: a.branch_id for a in AcademyProfile.objects.filter(
        is_deleted=False, branch_id__in=branch_ids, user_id__in=enrolled)}
    per = {}
    for t in StudentTimetable.objects.exclude(status="ENDED"):
        if t.student_id in branch_of and _slot_active_on(t, today):
            per.setdefault(t.student_id, []).append(t.duration_minutes)
    out = {}
    for sid, ds in per.items():
        if len(set(ds)) != 1:
            continue
        key = (len(ds), ds[0])
        out.setdefault(branch_of[sid], {})
        out[branch_of[sid]][key] = out[branch_of[sid]].get(key, 0) + 1
    return out


def _tuition_by_unit(branch_ids):
    """회당 단가로 계산되는 학생 — 주3회 이상이거나 회당 시간이 섞인 사람."""
    from ..models import StudentTimetable, StudentProfile, AcademyProfile, EnrollmentStatus
    from .admin import _slot_active_on
    today = (now() + timedelta(hours=9)).date()
    enrolled = set(StudentProfile.objects.filter(
        enrollment_status=EnrollmentStatus.ENROLLED).values_list("user_id", flat=True))
    prof = {a.user_id: a for a in AcademyProfile.objects.filter(
        is_deleted=False, branch_id__in=branch_ids, user_id__in=enrolled).select_related("branch")}
    per = {}
    for t in StudentTimetable.objects.exclude(status="ENDED").select_related("student__userprofile"):
        if t.student_id in prof and _slot_active_on(t, today):
            per.setdefault(t.student_id, []).append(t)
    out = []
    for sid, ts in per.items():
        ds = [x.duration_minutes for x in ts]
        if len(ds) <= 2 and len(set(ds)) == 1:
            continue
        out.append({"student_id": sid, "name": name_of(ts[0].student),
                    "branch_id": prof[sid].branch_id,
                    "branch": (prof[sid].branch.name if prof[sid].branch_id else ""),
                    "sessions": len(ds),
                    "durations": sorted(ds),
                    "why": ("주%d회" % len(ds)) if len(ds) > 2 else "회당 시간 섞임"})
    return sorted(out, key=lambda x: (x["branch"], -x["sessions"], x["name"]))


def _is_director_up_exam(user):
    if user.is_super_admin():
        return True
    prof = getattr(user, "academy_profile", None)
    return bool(prof and prof.role in (AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN,
                                       AcademyRole.REGIONAL_MANAGER, AcademyRole.BRANCH_MANAGER))


class MenuSettingAdminAPI(APIView):
    """메뉴 관리 — 전체 on/off, 역할 제한, 직원 예외."""

    @admin_role_required
    def get(self, request):
        bid = menu_scope_branch(request.user)
        key_map = {(m.key, m.branch_id): m for m in MenuSetting.objects.all()}
        # 직원 예외는 볼 수 있는 지점 사람만
        view = viewable_branch_ids(request.user)
        sq = AcademyProfile.objects.filter(role__in=MENU_LIMITABLE_ROLES, is_deleted=False)\
                                   .select_related("user", "user__userprofile", "branch")
        if view is not None:
            sq = sq.filter(branch_id__in=view)
        staff_ids = {p.user_id for p in sq}
        over = {}
        for o in MenuOverride.objects.filter(staff_id__in=staff_ids)\
                                     .select_related("staff", "staff__userprofile"):
            over.setdefault(o.key, []).append(
                {"staff_id": o.staff_id, "name": name_of(o.staff), "allow": o.allow})
        me = getattr(request.user, "academy_profile", None)
        my_role = me.role if me else ""
        my_super = request.user.is_super_admin()
        rows = []
        for d in MENU_DEFS:
            # 자기가 못 보는 메뉴는 손댈 수도 없으니 목록에서 뺀다(원장에게 개발일지 등)
            if not _floor_ok(d, my_role, my_super):
                continue
            st = menu_setting_for(key_map, d["key"], bid)
            rows.append({"key": d["key"], "label": d["label"], "always": bool(d.get("always")),
                         "floor": d.get("floor", ""),
                         # 제한을 만족하는 직급이 하나도 없으면(개발일지) 사용·예외까지 손댈 게 없다
                         "floor_open": [r for r in MENU_LIMITABLE_ROLES
                                        if r in set(d.get("floor_roles") or [])],
                         "enabled": (st.enabled if st else True),
                         "roles": (load_list_plain(st.roles) if st else list(d.get("default_roles") or [])),
                         "overrides": over.get(d["key"], [])})
        staff = [{"user_id": p.user_id, "name": name_of(p.user), "role": p.role,
                  "role_label": ROLE_LABEL.get(p.role, p.role),
                  "branch": (p.branch.name if p.branch_id else "")} for p in sq]
        staff.sort(key=lambda x: x["name"])
        b = None
        if bid:
            _B = Branch
            b = _B.objects.filter(id=bid).first()
        return self.success({
            "rows": rows, "staff": staff,
            "scope": (b.name if b else "전 지점"),
            "roles": [{"value": k, "label": ROLE_LABEL.get(k, k)} for k in MENU_LIMITABLE_ROLES],
        })

    @admin_role_required
    def post(self, request):
        d = request.data
        key = d.get("key")
        if not key or key not in {m["key"] for m in MENU_DEFS}:
            return self.error("메뉴가 올바르지 않습니다.")
        if key in MENU_ALWAYS and "enabled" in d and not d.get("enabled"):
            return self.error("이 메뉴는 끌 수 없습니다(끄면 설정으로 돌아올 수 없습니다).")
        st, _ = MenuSetting.objects.get_or_create(key=key, branch_id=menu_scope_branch(request.user))
        if "enabled" in d:
            st.enabled = bool(d.get("enabled"))
        if "roles" in d:
            st.roles = _json.dumps([str(x) for x in (d.get("roles") or [])], ensure_ascii=False)
        st.save()
        # 직원 예외
        if "override" in d:
            o = d["override"] or {}
            sid, allow = o.get("staff_id"), o.get("allow")
            if sid:
                if allow is None:
                    MenuOverride.objects.filter(staff_id=sid, key=key).delete()
                else:
                    MenuOverride.objects.update_or_create(
                        staff_id=sid, key=key, defaults={"allow": bool(allow)})
        return self.success("ok")


class BrandAPI(APIView):
    """학원 로고·아이콘. 읽기는 누구나(로그인 화면에도 걸리므로), 올리는 건 본부만."""
    request_parsers = ()

    def get(self, request):
        return self.success(brand_all())

    def post(self, request):
        # 지점마다 다르면 학원이 여러 곳처럼 보인다. 전 지점 공통이라 본부에서만 올린다
        prof = getattr(request.user, "academy_profile", None)
        role = prof.role if prof else ""
        if not (request.user.is_authenticated
                and (role == AcademyRole.HQ_ADMIN or request.user.is_super_admin())):
            return self.error("본부 관리자만 올릴 수 있습니다.")
        kind = request.POST.get("kind", "")
        if kind not in BRAND_KINDS:
            return self.error("종류가 올바르지 않습니다.")
        if request.POST.get("clear") == "1":
            delete_brand(kind)
            return self.success(brand_all())
        f = request.FILES.get("file")
        if not f:
            return self.error("파일이 없습니다.")
        if f.size > BRAND_MAX_BYTES:
            return self.error("파일이 너무 큽니다(최대 8MB).")
        if os.path.splitext(f.name)[-1].lower() not in BRAND_EXT:
            return self.error("이미지 파일만 올릴 수 있습니다(png·jpg·gif·webp).")
        ok, msg = save_brand(kind, f)
        if not ok:
            return self.error(msg)
        return self.success(brand_all())


class SavedSearchAPI(APIView):
    """자주 쓰는 검색어. 사람에게 딸린 것이라 서버에 둔다 — 다른 컴퓨터에서도 그대로 나온다."""

    @admin_role_required
    def get(self, request):
        scope = request.GET.get("scope") or "phone"
        qs = SavedSearch.objects.filter(user=request.user, scope=scope)[:30]
        return self.success([{
            "id": r.id, "query": r.query, "favorite": r.is_favorite,
            "count": r.use_count,
            "last": str(r.last_used_at + timedelta(hours=9))[:16],
        } for r in qs])

    @admin_role_required
    def post(self, request):
        d = request.data
        scope = (d.get("scope") or "phone")[:32]
        q = (d.get("query") or "").strip()[:200]
        if not q:
            return self.error("검색어가 없습니다.")
        row, made = SavedSearch.objects.get_or_create(
            user=request.user, scope=scope, query=q)
        if not made:
            # 같은 검색어를 또 쓰면 새로 만들지 않고 쓴 횟수와 시각만 올린다
            row.use_count += 1
        if "favorite" in d:
            row.is_favorite = bool(d.get("favorite"))
        row.save()
        return self.success({"id": row.id, "favorite": row.is_favorite})

    @admin_role_required
    def delete(self, request):
        SavedSearch.objects.filter(id=request.GET.get("id"), user=request.user).delete()
        return self.success("ok")
