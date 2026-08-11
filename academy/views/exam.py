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
    try:
        v = _json.loads(s) if s else []
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


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
            "fee": c.fee, "levels": load_list(c.levels), "tracks": load_list(c.tracks),
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
        c.entry_mode = d.get("entry_mode") or EntryMode.INDIVIDUAL
        try:
            c.fee = int(d.get("fee")) if str(d.get("fee") or "").strip() else None
        except (TypeError, ValueError):
            c.fee = None
        c.levels = _json.dumps([x.strip() for x in (d.get("levels") or []) if str(x).strip()],
                               ensure_ascii=False)
        c.tracks = _json.dumps([x.strip() for x in (d.get("tracks") or []) if str(x).strip()],
                               ensure_ascii=False)
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
