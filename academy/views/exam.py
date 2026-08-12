"""자격증 · 대회 관리 API.

지금까지는 시험 정보가 학생마다 수업일지에 흩어져 있어, "누가 아직 접수 안 했지?"를
알려면 학생을 하나씩 열어봐야 했다. 참가를 한 줄씩 모아 보는 목록이 이 기능의 핵심이다.
"""
import json as _json
from datetime import timedelta, datetime

from django.db.models import Q
from django.utils.timezone import now

from utils.api import APIView
from account.decorators import admin_role_required
from account.models import User

from ..models import (ExamCatalog, ExamSession, ExamEntry, ExamTeam, ExamKind, EntryMode,
                      EXAM_KIND_CHOICES, ENTRY_MODE_CHOICES, StudentProfile, EnrollmentStatus,
                      StudentCredential)
from ..services import viewable_branch_ids, can_manage_branch, can_view_branch

KIND_LABEL = dict(EXAM_KIND_CHOICES)
MODE_LABEL = dict(ENTRY_MODE_CHOICES)

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
            "levels_text": item_text(load_list(c.levels)), "tracks_text": item_text(load_list(c.tracks)),
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
        c = ExamCatalog.objects.filter(id=request.GET.get("id")).first()
        if not c:
            return self.error("종류가 없습니다.")
        if c.branch_id and not can_manage_branch(request.user, c.branch_id):
            return self.error("권한이 없습니다.")
        c.is_active = False
        c.save(update_fields=["is_active"])
        return self.success("ok")


# ─────────────────────── 회차 ───────────────────────

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
        "exam_date": str(d) if d else "", "confirmed": sn.confirmed,
        "apply_from": str(sn.apply_from) if sn.apply_from else "",
        "apply_until": str(sn.apply_until) if sn.apply_until else "",
        "result_date": str(sn.result_date) if sn.result_date else "",
        "result_same_day": bool(sn.result_date and sn.exam_date and sn.result_date == sn.exam_date),
        "entry_mode": sn.entry_mode or (sn.catalog.entry_mode if sn.catalog_id else ""),
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
        for e in ExamEntry.objects.filter(session__in=qs, is_deleted=False).values("session_id", "applied"):
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
        if kind == ExamKind.CERT and not exam_date:
            return self.error("시험일을 정하세요.")
        if not sn:
            sn = ExamSession(created_by=request.user)
        sn.kind = kind
        cid = d.get("catalog_id") or None
        sn.catalog_id = int(cid) if cid else None
        if kind == ExamKind.CONTEST and not sn.catalog_id:
            return self.error("어떤 대회인지 고르세요.")
        sn.title = (d.get("title") or "").strip()[:128]
        sn.exam_date = exam_date
        sn.apply_from = parse_date(d.get("apply_from"))
        au = parse_date(d.get("apply_until"))
        if au is None and kind == ExamKind.CERT and exam_date:
            # 특별시험은 시험 이틀 전까지 접수 — 비워 두면 자동으로 채운다
            au = exam_date - timedelta(days=CERT_APPLY_LEAD_DAYS)
        sn.apply_until = au
        rd = parse_date(d.get("result_date"))
        if rd is None and exam_date:
            # 자체 시험은 그날 시험이 끝나면 바로 결과가 나온다 — 비워 두면 시험일로
            rd = exam_date
        sn.result_date = rd
        sn.entry_mode = (d.get("entry_mode") or "").strip()
        sn.place = (d.get("place") or "").strip()[:128]
        try:
            sn.fee = int(d.get("fee")) if str(d.get("fee") or "").strip() else None
        except (TypeError, ValueError):
            sn.fee = None
        sn.note = (d.get("note") or "").strip()[:255]
        sn.confirmed = bool(d.get("confirmed", True))
        bid = d.get("branch_id") or None
        if bid and not can_manage_branch(request.user, int(bid)):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        sn.branch_id = int(bid) if bid else None
        sn.save()
        return self.success(session_row(sn))

    @admin_role_required
    def delete(self, request):
        sn = ExamSession.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not sn:
            return self.error("회차가 없습니다.")
        if sn.branch_id and not can_manage_branch(request.user, sn.branch_id):
            return self.error("권한이 없습니다.")
        sn.is_deleted = True
        sn.save(update_fields=["is_deleted"])
        ExamEntry.objects.filter(session=sn).update(is_deleted=True)
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
    return {
        "id": e.id, "session_id": sn.id, "kind": sn.kind,
        "kind_label": KIND_LABEL.get(sn.kind, sn.kind),
        "student_id": e.student_id, "student": name_of(e.student),
        "catalog_id": (cat.id if cat else None), "catalog": (cat.name if cat else ""),
        "level": e.level, "track": e.track,
        "team_id": e.team_id, "team": (e.team.name if e.team_id else ""),
        "what": " ".join(what) or session_label(sn),
        "session_label": session_label(sn),
        "exam_date": str(sn.exam_date) if sn.exam_date else "",
        "confirmed": sn.confirmed,
        "apply_until": str(sn.apply_until) if sn.apply_until else "",
        "d_exam": ((sn.exam_date - today).days if sn.exam_date else None),
        "d_apply": ((sn.apply_until - today).days if sn.apply_until else None),
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
            "credential", "instructor").filter(is_deleted=False, session__is_deleted=False)
        if view is not None:
            qs = qs.filter(Q(session__branch_id=None) | Q(session__branch_id__in=view))
        if request.GET.get("session_id"):
            qs = qs.filter(session_id=request.GET["session_id"])
        if request.GET.get("student_id"):
            qs = qs.filter(student_id=request.GET["student_id"])
        rows = [entry_row(e) for e in qs[:1000]]
        # 시험일이 가까운 순. 날짜 미정(대회 미확정)은 뒤로.
        rows.sort(key=lambda r: (r["exam_date"] or "9999-99-99", r["student"]))
        return self.success(rows)

    @admin_role_required
    def post(self, request):
        """참가 추가·수정. student_ids 로 여러 명을 한 번에 붙일 수 있다."""
        d = request.data
        eid = d.get("id")
        if eid:
            e = ExamEntry.objects.filter(id=eid, is_deleted=False).first()
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
        ids = d.get("student_ids") or ([d.get("student_id")] if d.get("student_id") else [])
        if not ids:
            return self.error("학생을 고르세요.")
        made, skipped = 0, []
        for sid in ids:
            u = User.objects.filter(id=sid).first()
            if not u:
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
        if "team_id" in d:
            e.team_id = int(d["team_id"]) if d.get("team_id") else None
        if "applied" in d:
            e.applied = bool(d.get("applied"))
            e.applied_at = kst_today() if e.applied else None
            if d.get("applied_at"):
                e.applied_at = parse_date(d["applied_at"], e.applied_at)
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
        e = ExamEntry.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not e:
            return self.error("참가 기록이 없습니다.")
        e.is_deleted = True
        e.save(update_fields=["is_deleted"])
        return self.success("ok")
