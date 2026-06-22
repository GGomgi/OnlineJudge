from datetime import timedelta, datetime, date as date_cls

from django.utils.timezone import now
from django.db.models import Count


def _to_date(v):
    """validate_serializer 가 문자열로 넘기는 날짜를 date 로 변환."""
    if isinstance(v, date_cls):
        return v
    return datetime.strptime(v, "%Y-%m-%d").date()

from django.db import transaction

from utils.api import APIView, validate_serializer

from account.decorators import admin_role_required
from account.models import User, UserProfile
from ..models import (AcademyProfile, AcademyRole, ACADEMY_ROLE_CHOICES,
                      ALL_BRANCH_ROLES, STAFF_ROLES, Branch,
                      SignupRequest, SignupStatus, CourseClass, ClassEnrollment,
                      TimetableSlot, ClassSession, SessionStatus, AttendanceRecord,
                      Lead, LeadStatus, CounselingLog, CounselingLogEdit, StudentProfile, EnrollmentStatus,
                      OptionItem, StudentTimetable, LessonType, GuardianStudent,
                      StaffProfile, HRNotice, StaffDocument, StaffProfileHistory,
                      TimetableChange, StudentStatusChange)
_WD = ["월", "화", "수", "목", "금", "토", "일"]
import os as _os
from django.conf import settings as _settings
from utils.shortcuts import rand_str as _rand_str
from ..serializers import (SignupRequestSerializer, SignupApproveSerializer,
                           SignupRejectSerializer, AssignRoleSerializer,
                           CreateStaffSerializer, StaffStatusSerializer,
                           CourseClassSerializer, CreateClassSerializer,
                           EditClassSerializer, EnrollSerializer,
                           EnrollmentSerializer, SetTimetableSlotSerializer,
                           ClassSessionSerializer, CreateSessionSerializer,
                           GenerateSessionsSerializer, AttendanceRecordSerializer,
                           MarkAttendanceSerializer, _student_brief,
                           LeadSerializer, AddCounselingNoteSerializer,
                           ConvertLeadSerializer, CloseLeadSerializer,
                           OptionItemSerializer, CreateOptionSerializer,
                           UpdateOptionSerializer, ReorderOptionSerializer,
                           StudentTimetableSerializer,
                           CreateStudentTimetableSerializer, EditStudentTimetableSerializer)
import json as _json


def _norm_phone(v):
    """전화번호에서 숫자만 추출(학부모 매칭 키)."""
    return "".join(ch for ch in (v or "") if ch.isdigit())


def resolve_program_label(value):
    """등록 과정 코드 → 표시 라벨(선택 목록 기준)."""
    if not value:
        return ""
    o = OptionItem.objects.filter(category="program", value=value).first()
    return o.label if o else value


def lesson_duration(school_type, weekly):
    """학교급·주횟수별 수업 1회 길이(분). 초등 이하는 주1회 90/주2+ 60,
    중등 이상(및 기타)은 주1회 120/주2+ 90."""
    weekly = weekly or 1
    if school_type == "ELEMENTARY":
        return 90 if weekly <= 1 else 60
    return 120 if weekly <= 1 else 90


def get_or_create_guardian(student, lead, branch, login_id="", password=""):
    """학생의 학부모(보호자) 계정을 전화번호로 찾거나 생성하고 자녀로 연결한다(11 §9).
    동일 전화번호의 학부모가 이미 있으면(형제 등록 등) 그 계정에 연결만 한다."""
    norm = _norm_phone(lead.parent_phone)
    parent_user = None
    if norm:
        prof = AcademyProfile.objects.select_related("user").filter(
            role=AcademyRole.PARENT, phone=norm).first()
        if prof:
            parent_user = prof.user
    if parent_user is None:
        username = (login_id or "").strip().lower() or ("p" + norm if norm else "")
        if not username:
            return None  # 전화번호도 아이디도 없으면 학부모 계정 생략
        if User.objects.filter(username=username).exists():
            # 충돌 시 일련번호 부여
            base, i = username, 1
            while User.objects.filter(username=username).exists():
                i += 1
                username = "%s%d" % (base, i)
        pw = (password or "").strip() or (norm or username)
        parent_user = User.objects.create(username=username, is_disabled=False)
        parent_user.set_password(pw)
        parent_user.save()
        UserProfile.objects.create(user=parent_user, real_name=lead.parent_name or "학부모")
        profile = apply_role(parent_user, AcademyRole.PARENT, branch)
        profile.phone = norm
        profile.save(update_fields=["phone"])
    GuardianStudent.objects.get_or_create(parent=parent_user, student=student,
                                          defaults={"relation": "학부모"})
    return parent_user
from ..services import (apply_role, staff_scope, can_manage_branch, can_view_branch,
                        managed_branch_ids, viewable_branch_ids,
                        editable_branch_ids, can_manage_staff)


class SignupRequestAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """가입 신청 목록. 전지점 역할은 전체, 지점 역할은 자기 지점만 조회."""
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch and not viewable_branch_ids(request.user):
            return self.error("No branch scope assigned")

        qs = SignupRequest.objects.select_related("user", "requested_branch", "reviewed_by")
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        if not all_branch:
            qs = qs.filter(requested_branch_id__in=(viewable_branch_ids(request.user) or []))

        data = self.paginate_data(request, qs, SignupRequestSerializer)
        return self.success(data)


class SignupApproveAPI(APIView):
    @validate_serializer(SignupApproveSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        req = SignupRequest.objects.filter(id=data["id"]).select_related("user", "requested_branch").first()
        if not req:
            return self.error("Signup request does not exist")
        if req.status != SignupStatus.PENDING:
            return self.error("This request has already been processed")

        role = data.get("role") or req.requested_role

        # 전지점 역할(HQ/HR) 부여는 전지점 권한자(본부)만 가능
        actor_all, actor_branch, actor_role = staff_scope(request.user)
        if role in ALL_BRANCH_ROLES and not actor_all:
            return self.error("Only HQ admin can grant this role")

        branch = req.requested_branch
        if data.get("branch_id"):
            branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
            if not branch:
                return self.error("Invalid branch")

        if role not in ALL_BRANCH_ROLES:
            if not branch:
                return self.error("Branch is required for this role")
            if not can_manage_branch(request.user, branch.id):
                return self.error("No permission for this branch")

        apply_role(req.user, role, branch)
        req.user.is_disabled = False
        req.user.save()

        req.status = SignupStatus.APPROVED
        req.requested_role = role
        req.requested_branch = None if role in ALL_BRANCH_ROLES else branch
        req.reviewed_by = request.user
        req.reviewed_at = now()
        req.save()
        return self.success(SignupRequestSerializer(req).data)


class SignupRejectAPI(APIView):
    @validate_serializer(SignupRejectSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        req = SignupRequest.objects.filter(id=data["id"]).select_related("user", "requested_branch").first()
        if not req:
            return self.error("Signup request does not exist")
        if req.status != SignupStatus.PENDING:
            return self.error("This request has already been processed")
        if not can_manage_branch(request.user, req.requested_branch_id):
            return self.error("No permission for this branch")

        req.user.is_disabled = True
        req.user.save()
        req.status = SignupStatus.REJECTED
        req.reject_reason = data.get("reason", "") or ""
        req.reviewed_by = request.user
        req.reviewed_at = now()
        req.save()
        return self.success(SignupRequestSerializer(req).data)


class AssignRoleAPI(APIView):
    @validate_serializer(AssignRoleSerializer)
    @admin_role_required
    def post(self, request):
        """사용자 역할/지점 부여·변경 (admin_type 동기화)."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        data = request.data
        target = User.objects.filter(id=data["user_id"]).first()
        if not target:
            return self.error("User does not exist")

        role = data["role"]
        branch, managed_ids, err = _validate_role_branches(request, role)
        if err:
            return self.error(err)

        profile = apply_role(target, role, branch)
        _apply_managed(profile, role, managed_ids)
        return self.success({"user_id": target.id, "role": role,
                             "branch_id": branch.id if branch else None,
                             "managed_branch_ids": managed_ids})


def _staff_no_prefix(role, branch):
    """사번 앞 2자리: 전지점 역할/미배정은 00, 그 외는 지점 코드 숫자(예: B002→02)."""
    if role in ALL_BRANCH_ROLES or branch is None:
        return "00"
    digits = "".join(ch for ch in (branch.code or "") if ch.isdigit())
    n = int(digits) if digits else 0
    return "%02d" % (n % 100)


def gen_staff_no(role, branch):
    """지점 prefix + 일련 3자리 사번 생성(지점별 최대값+1)."""
    prefix = _staff_no_prefix(role, branch)
    maxseq = 0
    for p in AcademyProfile.objects.filter(staff_no__startswith=prefix).exclude(staff_no=""):
        tail = p.staff_no[len(prefix):]
        if tail.isdigit():
            maxseq = max(maxseq, int(tail))
    return "%s%03d" % (prefix, maxseq + 1)


def _staff_brief(profile):
    u = profile.user
    real_name = ""
    try:
        real_name = u.userprofile.real_name or ""
    except Exception:
        real_name = ""
    branch = None
    if profile.branch_id and profile.branch:
        branch = {"id": profile.branch.id, "code": profile.branch.code, "name": profile.branch.name}
    hr = getattr(u, "staff_profile", None)
    hr_completed = bool(hr and hr.is_complete())
    managed = [{"id": b.id, "name": b.name} for b in profile.managed_branches.all()]
    return {
        "user_id": u.id, "username": u.username, "real_name": real_name,
        "staff_no": profile.staff_no or u.username,
        "role": profile.role, "role_label": dict(ACADEMY_ROLE_CHOICES).get(profile.role, profile.role),
        "branch": branch, "managed_branches": managed, "is_active": not u.is_disabled,
        "is_deleted": profile.is_deleted,
        "hr_completed": hr_completed,
    }


class StaffAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """직원(교직원 역할) 계정 목록. 전지점 역할은 전체, 지점 역할은 자기 지점만."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch and branch_id is None and not managed_branch_ids(request.user):
            return self.error("No branch scope assigned")
        qs = AcademyProfile.objects.select_related(
            "user", "user__staff_profile", "branch").filter(role__in=STAFF_ROLES)
        if request.GET.get("show_deleted") != "1":
            qs = qs.filter(is_deleted=False)
        if not all_branch:
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        qs = qs.order_by("branch_id", "role", "user__username")
        return self.success([_staff_brief(p) for p in qs])

    @validate_serializer(CreateStaffSerializer)
    @admin_role_required
    def post(self, request):
        """직원 계정 생성(활성). 역할/지점 부여 + admin_type 동기화."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        data = request.data
        role = data["role"]
        if role not in STAFF_ROLES:
            return self.error("Invalid staff role")

        branch, managed_ids, err = _validate_role_branches(request, role)
        if err:
            return self.error(err)

        email = (data.get("email") or "").lower() or None
        if email and User.objects.filter(email=email).exists():
            return self.error("Email already exists")

        with transaction.atomic():
            # 사번을 로그인 아이디로 자동 생성(중복 시 다음 일련번호로 재시도)
            staff_no = gen_staff_no(role, branch)
            while User.objects.filter(username=staff_no).exists():
                prefix, tail = staff_no[:2], staff_no[2:]
                staff_no = "%s%03d" % (prefix, (int(tail) if tail.isdigit() else 0) + 1)
            user = User.objects.create(username=staff_no, email=email, is_disabled=False)
            user.set_password(data["password"])
            user.save()
            UserProfile.objects.create(user=user, real_name=data["real_name"])
            profile = apply_role(user, role, branch)
            profile.staff_no = staff_no
            profile.save(update_fields=["staff_no"])
            _apply_managed(profile, role, managed_ids)
        profile = AcademyProfile.objects.select_related("user", "branch").get(pk=profile.pk)
        return self.success(_staff_brief(profile))


class StaffStatusAPI(APIView):
    @validate_serializer(StaffStatusSerializer)
    @admin_role_required
    def post(self, request):
        """직원 계정 활성/비활성 전환."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        data = request.data
        profile = AcademyProfile.objects.select_related("user", "branch").filter(
            user_id=data["user_id"], role__in=STAFF_ROLES).first()
        if not profile:
            return self.error("Staff does not exist")
        if profile.user_id == request.user.id:
            return self.error("본인 계정은 변경할 수 없습니다.")
        if profile.is_all_branch():
            actor_all, _, _ = staff_scope(request.user)
            if not actor_all:
                return self.error("No permission")
        elif not can_manage_branch(request.user, profile.branch_id):
            return self.error("No permission for this branch")

        profile.user.is_disabled = not data["is_active"]
        profile.user.save()
        return self.success(_staff_brief(profile))


class StaffDeleteAPI(APIView):
    @admin_role_required
    def post(self, request):
        """직원 소프트삭제(숨김)/복원. 원장 이상(직원관리 권한)만. 데이터는 보존."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        data = request.data
        profile = AcademyProfile.objects.select_related("user", "branch").filter(
            user_id=data.get("user_id"), role__in=STAFF_ROLES).first()
        if not profile:
            return self.error("Staff does not exist")
        if profile.user_id == request.user.id:
            return self.error("본인 계정은 삭제할 수 없습니다.")
        if profile.user.is_super_admin():
            return self.error("최고 관리자 계정은 삭제할 수 없습니다.")
        if profile.is_all_branch():
            actor_all, _, _ = staff_scope(request.user)
            if not actor_all:
                return self.error("No permission")
        elif not can_manage_branch(request.user, profile.branch_id):
            return self.error("No permission for this branch")
        deleted = data.get("deleted", True)
        profile.is_deleted = bool(deleted)
        profile.save(update_fields=["is_deleted"])
        # 삭제 시 로그인도 차단(복원 시 활성으로 되돌림)
        profile.user.is_disabled = bool(deleted)
        profile.user.save(update_fields=["is_disabled"])
        return self.success(_staff_brief(profile))


class StaffReissueSabunAPI(APIView):
    @admin_role_required
    def post(self, request):
        """사번(로그인 아이디) 재발급 — 현재 역할·소속 지점 기준으로 다시 생성.
        이력은 user.id 로 연결되어 보존되고 로그인 아이디만 바뀐다. 원장 이상만."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        profile = AcademyProfile.objects.select_related("user", "branch").filter(
            user_id=request.data.get("user_id"), role__in=STAFF_ROLES).first()
        if not profile:
            return self.error("Staff does not exist")
        if profile.user.is_super_admin():
            return self.error("최고 관리자 계정은 변경할 수 없습니다.")
        if profile.is_all_branch():
            actor_all, _, _ = staff_scope(request.user)
            if not actor_all:
                return self.error("No permission")
        elif not can_manage_branch(request.user, profile.branch_id):
            return self.error("No permission for this branch")
        new_no = gen_staff_no(profile.role, profile.branch)
        while User.objects.filter(username=new_no).exclude(id=profile.user_id).exists():
            prefix, tail = new_no[:2], new_no[2:]
            new_no = "%s%03d" % (prefix, (int(tail) if tail.isdigit() else 0) + 1)
        old_no = profile.user.username
        if new_no == old_no:
            return self.error("이미 현재 소속 기준 사번입니다.")
        profile.user.username = new_no
        profile.user.save(update_fields=["username"])
        profile.staff_no = new_no
        profile.save(update_fields=["staff_no"])
        return self.success({"old_sabun": old_no, "new_sabun": new_no})


def _parse_managed(request):
    """요청의 managed_branch_ids → 유효 지점 id 리스트. 부여자가 해당 지점을
    수정 권한(=관리)으로 보유한 경우만 열람권을 위임할 수 있다."""
    raw = request.data.get("managed_branch_ids") or []
    mbids = [int(b) for b in raw if str(b).isdigit()]
    valid = list(Branch.objects.filter(id__in=mbids, is_active=True).values_list("id", flat=True))
    for bid in valid:
        if not can_manage_branch(request.user, bid):
            return None, "관리 권한이 없는 지점이 포함되어 있습니다."
    return valid, None


def _validate_role_branches(request, role):
    """역할에 맞는 주 소속(수정) 지점 + 열람지점(겸직) 산출·검증.
    반환 (branch, managed_ids, error_msg|None).
    - 지부장(REGIONAL_MANAGER): 수정 지점 없음(branch=None), 열람지점 1개 이상 필수.
    - 그 외 단일지점 역할: 주 소속 지점(수정) 필수 + 선택적 열람지점(지부장 겸직)."""
    data = request.data
    actor_all, _, _ = staff_scope(request.user)
    if role in ALL_BRANCH_ROLES:
        if not actor_all:
            return None, [], "Only HQ admin can grant this role"
        return None, [], None
    managed, merr = _parse_managed(request)
    if merr:
        return None, [], merr
    if role == AcademyRole.REGIONAL_MANAGER:
        if not managed:
            return None, [], "지부장은 열람 지점을 1개 이상 선택해야 합니다."
        return None, managed, None
    # 단일지점 역할(원장/부원장/강사/조교/외부): 주 소속 지점 + 선택 열람지점(겸직)
    if not data.get("branch_id"):
        return None, [], "Branch is required for this role"
    branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
    if not branch:
        return None, [], "Invalid branch"
    if not can_manage_branch(request.user, branch.id):
        return None, [], "No permission for this branch"
    return branch, managed, None


def _apply_managed(profile, role, managed_ids):
    if managed_ids:
        profile.managed_branches.set(Branch.objects.filter(id__in=managed_ids))
    else:
        profile.managed_branches.clear()


def _can_manage_staff_user(request, prof):
    if prof is None:
        return False
    if not can_manage_staff(request.user):
        return False
    if prof.is_all_branch():
        actor_all, _, _ = staff_scope(request.user)
        return actor_all
    return can_manage_branch(request.user, prof.branch_id)


class StaffDetailAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """직원 인사 상세(프로필+서류+전체 이력). user_id 지정."""
        from .oj import _staff_profile_data, _doc_data
        uid = request.GET.get("user_id")
        prof = AcademyProfile.objects.select_related("user", "branch").filter(
            user_id=uid, role__in=STAFF_ROLES).first()
        if not prof:
            return self.error("직원이 아닙니다.")
        if not _can_manage_staff_user(request, prof):
            return self.error("권한이 없습니다.")
        sp = StaffProfile.objects.filter(user_id=uid).first()
        docs = [_doc_data(d) for d in StaffDocument.objects.filter(user_id=uid)]
        hist = []
        for h in StaffProfileHistory.objects.filter(user_id=uid).select_related("actor")[:200]:
            an = ""
            if h.actor_id:
                try:
                    an = h.actor.userprofile.real_name or h.actor.username
                except Exception:
                    an = h.actor.username
            hist.append({"field": h.field, "old": h.old_value, "new": h.new_value,
                         "actor": an, "time": str(h.create_time)[:16]})
        return self.success({"staff": _staff_brief(prof),
                             "profile": (_staff_profile_data(sp) if sp else None),
                             "documents": docs, "history": hist})

    @admin_role_required
    def post(self, request):
        """관리자가 직원 기본 인사정보(주소·연락처) 수정 + 이력 기록."""
        from .oj import TRACKED_HR_FIELDS, record_hr_history
        uid = request.data.get("user_id")
        prof = AcademyProfile.objects.filter(user_id=uid, role__in=STAFF_ROLES).first()
        if not prof:
            return self.error("직원이 아닙니다.")
        if not _can_manage_staff_user(request, prof):
            return self.error("권한이 없습니다.")
        sp, _ = StaffProfile.objects.get_or_create(user_id=uid)
        before = {f: getattr(sp, f) for f in TRACKED_HR_FIELDS}
        d = request.data
        for f in ("zipcode", "address", "address_detail", "phone"):
            if f in d:
                setattr(sp, f, d.get(f) or "")
        sp.save()
        after = {f: getattr(sp, f) for f in TRACKED_HR_FIELDS}
        record_hr_history(sp.user, request.user, before, after)
        return self.success("저장되었습니다.")


class StaffDocUploadAdminAPI(APIView):
    request_parsers = ()

    @admin_role_required
    def post(self, request):
        """직원 계약서·서류 업로드(관리자). user_id, group(서류함), title, doc_date, visible_to_staff, file."""
        from .oj import _doc_data
        uid = request.POST.get("user_id")
        prof = AcademyProfile.objects.filter(user_id=uid, role__in=STAFF_ROLES).first()
        if not prof:
            return self.error("직원이 아닙니다.")
        if not _can_manage_staff_user(request, prof):
            return self.error("권한이 없습니다.")
        f = request.FILES.get("file")
        if not f:
            return self.error("파일이 없습니다.")
        if f.size > 16 * 1024 * 1024:
            return self.error("파일이 너무 큽니다(최대 16MB).")
        suffix = _os.path.splitext(f.name)[-1].lower()
        if suffix not in [".gif", ".jpg", ".jpeg", ".bmp", ".png", ".pdf", ".doc", ".docx", ".hwp", ".hwpx", ".xls", ".xlsx"]:
            return self.error("지원하지 않는 형식입니다.")
        _os.makedirs(_settings.UPLOAD_DIR, exist_ok=True)
        name = "doc_" + _rand_str(16) + suffix
        with open(_os.path.join(_settings.UPLOAD_DIR, name), "wb") as out:
            for chunk in f:
                out.write(chunk)
        group = request.POST.get("group", "") or ""
        dd = request.POST.get("doc_date") or ""
        order = StaffDocument.objects.filter(user_id=uid, group=group).count()
        doc = StaffDocument.objects.create(
            user_id=uid, uploaded_by=request.user, group=group,
            title=(request.POST.get("title", "") or f.name),
            url=f"{_settings.UPLOAD_PREFIX}/{name}",
            doc_date=(_to_date(dd) if dd else None), order=order,
            visible_to_staff=(request.POST.get("visible_to_staff") == "true"))
        return self.success(_doc_data(doc))


class StaffDocAdminAPI(APIView):
    @admin_role_required
    def put(self, request):
        """서류 메타 수정(서류함·설명·작성일·직원표시)."""
        from .oj import _doc_data
        d = request.data
        doc = StaffDocument.objects.filter(id=d.get("id")).first()
        if not doc:
            return self.error("문서가 없습니다.")
        prof = AcademyProfile.objects.filter(user_id=doc.user_id).first()
        if not _can_manage_staff_user(request, prof):
            return self.error("권한이 없습니다.")
        for f in ("group", "title"):
            if f in d:
                setattr(doc, f, d.get(f) or "")
        if "doc_date" in d:
            doc.doc_date = _to_date(d["doc_date"]) if d.get("doc_date") else None
        if "visible_to_staff" in d:
            doc.visible_to_staff = bool(d["visible_to_staff"])
        doc.save()
        return self.success(_doc_data(doc))

    @admin_role_required
    def delete(self, request):
        doc = StaffDocument.objects.filter(id=request.GET.get("id")).first()
        if not doc:
            return self.error("문서가 없습니다.")
        prof = AcademyProfile.objects.filter(user_id=doc.user_id).first()
        if not _can_manage_staff_user(request, prof):
            return self.error("권한이 없습니다.")
        doc.delete()
        return self.success("Deleted")


class StaffDocReorderAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """같은 서류함 내 순서 재정렬(ids 순서대로)."""
        ids = request.data.get("ids", [])
        for i, did in enumerate(ids):
            StaffDocument.objects.filter(id=did).update(order=i)
        return self.success("Reordered")


class HRNoticeAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """인사 변경 통보(미읽음) 목록. 지점 스코프."""
        all_branch, branch_id, role = staff_scope(request.user)
        qs = HRNotice.objects.select_related("staff", "branch").filter(is_read=False)
        if not all_branch:
            if branch_id is None:
                return self.success([])
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        out = []
        for n in qs[:100]:
            out.append({"id": n.id, "message": n.message, "kind": n.kind,
                        "branch": n.branch.name if n.branch_id else None,
                        "create_time": str(n.create_time)[:16]})
        return self.success(out)

    @admin_role_required
    def post(self, request):
        """통보 읽음 처리(id 지정 시 단건, 없으면 스코프 내 전체)."""
        all_branch, branch_id, role = staff_scope(request.user)
        qs = HRNotice.objects.filter(is_read=False)
        if not all_branch:
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        nid = request.data.get("id")
        if nid:
            qs = qs.filter(id=nid)
        qs.update(is_read=True)
        return self.success("Read")


class ClassAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """반 목록(지점 스코프). class_id 지정 시 단건."""
        qs = CourseClass.objects.select_related("branch", "instructor")
        class_id = request.GET.get("id")
        if class_id:
            obj = qs.filter(id=class_id).first()
            if not obj:
                return self.error("Class does not exist")
            if not can_view_branch(request.user, obj.branch_id):
                return self.error("No permission for this branch")
            return self.success(CourseClassSerializer(obj).data)

        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch:
            if not viewable_branch_ids(request.user):
                return self.error("No branch scope assigned")
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        bid = request.GET.get("branch_id")
        if bid:
            qs = qs.filter(branch_id=bid)
        return self.success(self.paginate_data(request, qs, CourseClassSerializer))

    @validate_serializer(CreateClassSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
        if not branch:
            return self.error("Invalid branch")
        if not can_manage_branch(request.user, branch.id):
            return self.error("No permission for this branch")
        instructor = None
        if data.get("instructor_id"):
            instructor = User.objects.filter(id=data["instructor_id"]).first()
            if not instructor:
                return self.error("Instructor does not exist")
        obj = CourseClass.objects.create(
            branch=branch, name=data["name"],
            track=data.get("track", "") or "", level=data.get("level", "") or "",
            instructor=instructor,
        )
        return self.success(CourseClassSerializer(obj).data)

    @validate_serializer(EditClassSerializer)
    @admin_role_required
    def put(self, request):
        data = request.data
        obj = CourseClass.objects.filter(id=data["id"]).first()
        if not obj:
            return self.error("Class does not exist")
        if not can_manage_branch(request.user, obj.branch_id):
            return self.error("No permission for this branch")
        for f in ("name", "track", "level", "is_active"):
            if f in data and data[f] is not None:
                setattr(obj, f, data[f])
        if "instructor_id" in data:
            if data["instructor_id"]:
                instructor = User.objects.filter(id=data["instructor_id"]).first()
                if not instructor:
                    return self.error("Instructor does not exist")
                obj.instructor = instructor
            else:
                obj.instructor = None
        obj.save()
        return self.success(CourseClassSerializer(obj).data)

    @admin_role_required
    def delete(self, request):
        class_id = request.GET.get("id")
        obj = CourseClass.objects.filter(id=class_id).first()
        if not obj:
            return self.error("Class does not exist")
        if not can_manage_branch(request.user, obj.branch_id):
            return self.error("No permission for this branch")
        obj.delete()
        return self.success()


class ClassEnrollmentAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """반 수강생 목록."""
        course_class = CourseClass.objects.filter(id=request.GET.get("class_id")).first()
        if not course_class:
            return self.error("Class does not exist")
        if not can_view_branch(request.user, course_class.branch_id):
            return self.error("No permission for this branch")
        qs = course_class.enrollments.select_related("student").all()
        return self.success(EnrollmentSerializer(qs, many=True).data)

    @validate_serializer(EnrollSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        course_class = CourseClass.objects.filter(id=data["class_id"]).first()
        if not course_class:
            return self.error("Class does not exist")
        if not can_manage_branch(request.user, course_class.branch_id):
            return self.error("No permission for this branch")
        student = User.objects.filter(id=data["student_id"]).first()
        if not student:
            return self.error("Student does not exist")
        enrollment, created = ClassEnrollment.objects.get_or_create(
            course_class=course_class, student=student, defaults={"is_active": True})
        if not created and not enrollment.is_active:
            enrollment.is_active = True
            enrollment.save()
        return self.success(EnrollmentSerializer(enrollment).data)

    @admin_role_required
    def delete(self, request):
        enrollment = ClassEnrollment.objects.filter(id=request.GET.get("id")).select_related("course_class").first()
        if not enrollment:
            return self.error("Enrollment does not exist")
        if not can_manage_branch(request.user, enrollment.course_class.branch_id):
            return self.error("No permission for this branch")
        enrollment.delete()
        return self.success()


class TimetableSlotAdminAPI(APIView):
    @validate_serializer(SetTimetableSlotSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        course_class = CourseClass.objects.filter(id=data["class_id"]).first()
        if not course_class:
            return self.error("Class does not exist")
        if not can_manage_branch(request.user, course_class.branch_id):
            return self.error("No permission for this branch")
        if data["end_time"] <= data["start_time"]:
            return self.error("end_time must be after start_time")
        slot = TimetableSlot.objects.create(
            course_class=course_class, day_of_week=data["day_of_week"],
            start_time=data["start_time"], end_time=data["end_time"],
            room=data.get("room", "") or "")
        return self.success(CourseClassSerializer(course_class).data)

    @admin_role_required
    def delete(self, request):
        slot = TimetableSlot.objects.filter(id=request.GET.get("id")).select_related("course_class").first()
        if not slot:
            return self.error("Slot does not exist")
        if not can_manage_branch(request.user, slot.course_class.branch_id):
            return self.error("No permission for this branch")
        slot.delete()
        return self.success()


class ClassSessionAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """반의 수업 회차 목록(date_from/date_to 옵션)."""
        course_class = CourseClass.objects.select_related("branch").filter(id=request.GET.get("class_id")).first()
        if not course_class:
            return self.error("Class does not exist")
        if not can_view_branch(request.user, course_class.branch_id):
            return self.error("No permission for this branch")
        qs = course_class.sessions.all()
        if request.GET.get("date_from"):
            qs = qs.filter(date__gte=request.GET["date_from"])
        if request.GET.get("date_to"):
            qs = qs.filter(date__lte=request.GET["date_to"])
        return self.success(self.paginate_data(request, qs, ClassSessionSerializer))

    @validate_serializer(CreateSessionSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        course_class = CourseClass.objects.filter(id=data["class_id"]).first()
        if not course_class:
            return self.error("Class does not exist")
        if not can_manage_branch(request.user, course_class.branch_id):
            return self.error("No permission for this branch")
        obj, created = ClassSession.objects.get_or_create(
            course_class=course_class, date=data["date"], start_time=data.get("start_time"),
            defaults={"end_time": data.get("end_time"), "topic": data.get("topic", "") or ""})
        if not created:
            return self.error("Session already exists for this date/time")
        return self.success(ClassSessionSerializer(obj).data)

    @admin_role_required
    def delete(self, request):
        session = ClassSession.objects.filter(id=request.GET.get("id")).select_related("course_class").first()
        if not session:
            return self.error("Session does not exist")
        if not can_manage_branch(request.user, session.course_class.branch_id):
            return self.error("No permission for this branch")
        session.delete()
        return self.success()


class GenerateSessionsAPI(APIView):
    @validate_serializer(GenerateSessionsSerializer)
    @admin_role_required
    def post(self, request):
        """시간표 슬롯을 바탕으로 기간 내 수업 회차를 자동 생성."""
        data = request.data
        course_class = CourseClass.objects.filter(id=data["class_id"]).first()
        if not course_class:
            return self.error("Class does not exist")
        if not can_manage_branch(request.user, course_class.branch_id):
            return self.error("No permission for this branch")
        from_date, to_date = _to_date(data["from_date"]), _to_date(data["to_date"])
        if to_date < from_date:
            return self.error("to_date must be on or after from_date")
        if (to_date - from_date).days > 366:
            return self.error("Date range too large (max 366 days)")

        slots_by_day = {}
        for slot in course_class.timetable_slots.all():
            slots_by_day.setdefault(slot.day_of_week, []).append(slot)
        if not slots_by_day:
            return self.error("No timetable slots to generate from")

        created = 0
        d = from_date
        while d <= to_date:
            for slot in slots_by_day.get(d.weekday(), []):
                _, was_created = ClassSession.objects.get_or_create(
                    course_class=course_class, date=d, start_time=slot.start_time,
                    defaults={"end_time": slot.end_time, "status": SessionStatus.SCHEDULED})
                if was_created:
                    created += 1
            d += timedelta(days=1)
        return self.success({"created": created})


class AttendanceAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """회차 출결 명부: 수강생 전원 + (있으면) 기록된 출결 상태."""
        session = ClassSession.objects.select_related("course_class").filter(id=request.GET.get("session_id")).first()
        if not session:
            return self.error("Session does not exist")
        if not can_view_branch(request.user, session.course_class.branch_id):
            return self.error("No permission for this branch")

        recs = {r.student_id: r for r in session.attendances.select_related("student").all()}
        roster = []
        enrollments = session.course_class.enrollments.filter(is_active=True).select_related("student")
        for en in enrollments:
            r = recs.get(en.student_id)
            roster.append({
                "student": _student_brief(en.student),
                "status": r.status if r else None,
                "memo": r.memo if r else "",
            })
        return self.success({"session": ClassSessionSerializer(session).data, "roster": roster})

    @validate_serializer(MarkAttendanceSerializer)
    @admin_role_required
    def post(self, request):
        """회차 출결 일괄 입력(upsert)."""
        data = request.data
        session = ClassSession.objects.select_related("course_class").filter(id=data["session_id"]).first()
        if not session:
            return self.error("Session does not exist")
        if not can_manage_branch(request.user, session.course_class.branch_id):
            return self.error("No permission for this branch")

        enrolled_ids = set(session.course_class.enrollments.filter(is_active=True).values_list("student_id", flat=True))
        updated = 0
        for item in data["records"]:
            if item["student_id"] not in enrolled_ids:
                continue
            AttendanceRecord.objects.update_or_create(
                session=session, student_id=item["student_id"],
                defaults={"status": item["status"], "memo": item.get("memo", "") or "",
                          "marked_by": request.user})
            updated += 1
        if session.status == SessionStatus.SCHEDULED:
            session.status = SessionStatus.DONE
            session.save()
        return self.success({"updated": updated})


# ── 상담 신청(리드) → 등록 전환 (80) ──

class LeadAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """리드(상담 신청) 목록. 전지점 역할은 전체, 지점 역할은 자기 지점만.
        소프트삭제(is_hidden): 본부(전지점)는 show_deleted 토글로 보기/감추기, 그 외는 항상 제외."""
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch and not viewable_branch_ids(request.user):
            return self.error("No branch scope assigned")
        is_mgr = _is_manager(request.user)  # 원장(지점장) 이상
        qs = Lead.objects.select_related("branch", "converted_user").prefetch_related(
            "logs__author", "logs__edited_by", "logs__edits__actor")
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        if not all_branch:
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        if not is_mgr:
            qs = qs.filter(is_hidden=False)
        elif request.GET.get("show_deleted") != "1":
            qs = qs.filter(is_hidden=False)
        return self.success([LeadSerializer(l, context={"show_hidden": is_mgr}).data for l in qs[:300]])


class LeadDeleteAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """리드 소프트삭제(숨김)/복원. 숨김=any 관리(지점), 복원=본부만."""
        lead = Lead.objects.filter(id=request.data.get("lead_id")).first()
        if not lead:
            return self.error("Lead does not exist")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("No permission for this branch")
        hidden = bool(request.data.get("hidden", True))
        if not hidden and not _is_manager(request.user):  # 복원은 원장 이상
            return self.error("복원은 원장 이상만 가능합니다.")
        lead.is_hidden = hidden
        lead.deleted_by = request.user if hidden else None
        lead.deleted_at = now() if hidden else None
        lead.save()
        return self.success({"lead_id": lead.id, "is_hidden": lead.is_hidden})


class CounselingNoteAdminAPI(APIView):
    @validate_serializer(AddCounselingNoteSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        lead = Lead.objects.filter(id=data["lead_id"]).first()
        if not lead:
            return self.error("Lead does not exist")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("No permission for this branch")
        CounselingLog.objects.create(
            lead=lead, author=request.user,
            channel=data.get("channel", "") or "VISIT",
            summary=data["summary"],
            counsel_at=data.get("counsel_at"),
            next_contact_at=data.get("next_contact_at"))
        if lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.COUNSELING
            lead.save()
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)

    @admin_role_required
    def put(self, request):
        """상담기록 수정(이유 없이 즉시). 매 수정마다 직전 내용 이력 보존(전체 추적)."""
        log = CounselingLog.objects.select_related("lead").filter(id=request.data.get("log_id")).first()
        if not log:
            return self.error("기록이 없습니다.")
        if not can_manage_branch(request.user, log.lead.branch_id):
            return self.error("No permission for this branch")
        new_summary = (request.data.get("summary") or "").strip()
        if not new_summary:
            return self.error("내용을 입력하세요.")
        CounselingLogEdit.objects.create(log=log, actor=request.user, old_summary=log.summary)
        log.prev_summary = log.summary
        log.summary = new_summary
        log.edited_by = request.user
        log.edited_at = now()
        log.save()
        return self.success(LeadSerializer(log.lead, context={"show_hidden": _is_manager(request.user)}).data)

    @admin_role_required
    def delete(self, request):
        """상담기록 소프트삭제(숨김)/복원."""
        log = CounselingLog.objects.select_related("lead").filter(id=request.GET.get("log_id")).first()
        if not log:
            return self.error("기록이 없습니다.")
        if not can_manage_branch(request.user, log.lead.branch_id):
            return self.error("No permission for this branch")
        hidden = request.GET.get("hidden", "1") == "1"
        if not hidden and not _is_manager(request.user):
            return self.error("복원은 원장 이상만 가능합니다.")
        log.is_hidden = hidden
        log.save()
        return self.success(LeadSerializer(log.lead, context={"show_hidden": _is_manager(request.user)}).data)


class PrefsAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        prof = getattr(request.user, "academy_profile", None)
        try:
            return self.success(_json.loads(prof.prefs) if (prof and prof.prefs) else {})
        except (ValueError, TypeError):
            return self.success({})

    @admin_role_required
    def post(self, request):
        """UI 설정 저장(부분 병합). 예: {show_deleted_leads: true}"""
        prof = getattr(request.user, "academy_profile", None)
        if not prof:
            return self.error("프로필이 없습니다.")
        try:
            cur = _json.loads(prof.prefs) if prof.prefs else {}
        except (ValueError, TypeError):
            cur = {}
        data = request.data if isinstance(request.data, dict) else {}
        cur.update(data)
        prof.prefs = _json.dumps(cur, ensure_ascii=False)
        prof.save(update_fields=["prefs"])
        return self.success(cur)


class ConvertLeadAdminAPI(APIView):
    @validate_serializer(ConvertLeadSerializer)
    @admin_role_required
    def post(self, request):
        """등록 전환: 리드를 활성 학생 계정으로 전환(계정 생성 + 학생 등록 정보)."""
        data = request.data
        lead = Lead.objects.select_related("branch").filter(id=data["lead_id"]).first()
        if not lead:
            return self.error("Lead does not exist")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("No permission for this branch")
        if lead.status == LeadStatus.CONVERTED:
            return self.error("This lead has already been converted")

        username = data["login_id"].lower()
        if User.objects.filter(username=username).exists():
            return self.error("Login ID already exists")

        with transaction.atomic():
            user = User.objects.create(username=username, is_disabled=False)
            user.set_password(data["password"])
            user.save()
            UserProfile.objects.create(user=user, real_name=lead.student_name)
            apply_role(user, AcademyRole.STUDENT, lead.branch)
            StudentProfile.objects.create(
                user=user,
                birth_date=data.get("birth_date"),
                gender=data.get("gender", "") or "",
                zipcode=data.get("zipcode", "") or "",
                address=data.get("address", "") or "",
                address_detail=data.get("address_detail", "") or "",
                student_phone=data.get("student_phone", "") or "",
                parent_name=lead.parent_name,
                parent_phone=lead.parent_phone,
                school_type=lead.school_type,
                school_name=lead.school_name,
                grade=lead.grade,
                enrollment_date=now().date(),
                enrollment_status=EnrollmentStatus.ENROLLED,
                program=data.get("program", "") or "",
                program_language=data.get("program_language", "") or "",
                program_custom=data.get("program_custom", "") or "",
                weekly_sessions=data.get("weekly_sessions"),
                class_schedule=data.get("class_schedule", "") or "",
                programs=data.get("programs", "") or "",
                lesson_start_date=data.get("lesson_start_date"),
                schedule_pending=bool(data.get("schedule_pending")),
                consent_privacy=bool(data.get("consent_privacy")),
                consent_guardian_name=data.get("consent_guardian_name", "") or "",
                consent_signature=data.get("consent_signature", "") or "",
                consent_date=data.get("consent_date") or now().date(),
            )
            # 입회원 신청서의 요일/시간(class_schedule)으로 개별 시간표 자동 생성(12).
            # '추후 안내'면 미생성. 수업 길이는 학교급·주횟수 규칙으로 자동 계산.
            schedule_raw = data.get("class_schedule") or ""
            try:
                schedule = _json.loads(schedule_raw) if schedule_raw else []
            except (ValueError, TypeError):
                schedule = []
            if not data.get("schedule_pending"):
                dur = lesson_duration(lead.school_type, data.get("weekly_sessions"))
                for row in schedule:
                    try:
                        wd = int(row.get("day"))
                        tm = (row.get("time") or "").strip()
                    except (AttributeError, TypeError, ValueError):
                        continue
                    if not (0 <= wd <= 6) or not tm:
                        continue
                    prog = (row.get("program") or "")
                    freq = row.get("frequency") or "WEEKLY"
                    subj = row.get("subject") or resolve_program_label(prog)
                    StudentTimetable.objects.create(
                        student=user, branch=lead.branch, class_type=LessonType.PRIVATE,
                        weekday=wd, start_time=tm, duration_minutes=dur,
                        program=prog, subject=subj, frequency=freq,
                        active_from=data.get("lesson_start_date"))
            # 학부모(보호자) 계정 생성/연결 — 자녀 기록 열람용(11 §9)
            parent_user = get_or_create_guardian(
                user, lead, lead.branch,
                login_id=data.get("parent_login_id", ""),
                password=data.get("parent_password", ""))
            lead.status = LeadStatus.CONVERTED
            lead.converted_user = user
            lead.save()
        result = LeadSerializer(lead).data
        if parent_user is not None:
            result["parent_account"] = {"username": parent_user.username,
                                        "is_new": parent_user.last_login is None}
        return self.success(result)


class BranchAdminAPI(APIView):
    """지점 관리(본부 관리자 전용). 추가/이름수정/활성토글. 삭제는 막고 비활성만 권장."""

    def _require_hq(self, request):
        actor_all, _, _ = staff_scope(request.user)
        return actor_all

    @admin_role_required
    def get(self, request):
        if not self._require_hq(request):
            return self.error("본부 관리자만 지점을 관리할 수 있습니다.")
        out = []
        for b in Branch.objects.all():
            out.append({"id": b.id, "code": b.code, "name": b.name, "is_active": b.is_active,
                        "member_count": AcademyProfile.objects.filter(branch_id=b.id).count()})
        return self.success(out)

    @admin_role_required
    def post(self, request):
        if not self._require_hq(request):
            return self.error("본부 관리자만 지점을 추가할 수 있습니다.")
        name = (request.data.get("name") or "").strip()
        if not name:
            return self.error("지점 이름을 입력하세요.")
        # 코드 자동 생성(B0NN): 기존 숫자 최대+1
        maxn = 0
        for b in Branch.objects.all():
            d = "".join(ch for ch in (b.code or "") if ch.isdigit())
            if d:
                maxn = max(maxn, int(d))
        code = "B%03d" % (maxn + 1)
        while Branch.objects.filter(code=code).exists():
            maxn += 1
            code = "B%03d" % (maxn + 1)
        b = Branch.objects.create(code=code, name=name)
        return self.success({"id": b.id, "code": b.code, "name": b.name})

    @admin_role_required
    def put(self, request):
        if not self._require_hq(request):
            return self.error("본부 관리자만 수정할 수 있습니다.")
        b = Branch.objects.filter(id=request.data.get("id")).first()
        if not b:
            return self.error("지점이 없습니다.")
        if "name" in request.data:
            nm = (request.data.get("name") or "").strip()
            if not nm:
                return self.error("지점 이름을 입력하세요.")
            b.name = nm
        if "is_active" in request.data:
            b.is_active = bool(request.data.get("is_active"))
        b.save()
        return self.success("ok")


class CloseLeadAdminAPI(APIView):
    @validate_serializer(CloseLeadSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        lead = Lead.objects.filter(id=data["lead_id"]).first()
        if not lead:
            return self.error("Lead does not exist")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("No permission for this branch")
        if lead.status == LeadStatus.CONVERTED:
            return self.error("Converted lead cannot be closed")
        lead.status = LeadStatus.CLOSED
        lead.close_reason = data.get("reason", "") or ""
        lead.save()
        return self.success(LeadSerializer(lead).data)


class OptionAdminAPI(APIView):
    """포털 선택 목록(드롭다운) 관리. 전사 공통 값이라 본부/인사 관리자만 편집 가능."""

    def _require_hq(self, request):
        actor_all, _, _ = staff_scope(request.user)
        return actor_all

    @admin_role_required
    def get(self, request):
        """카테고리별 옵션 목록(비활성 포함). category 미지정 시 전체."""
        if not self._require_hq(request):
            return self.error("본부/인사 관리자만 목록을 관리할 수 있습니다.")
        qs = OptionItem.objects.all()
        category = request.GET.get("category")
        if category:
            qs = qs.filter(category=category)
        return self.success(OptionItemSerializer(qs, many=True).data)

    @validate_serializer(CreateOptionSerializer)
    @admin_role_required
    def post(self, request):
        if not self._require_hq(request):
            return self.error("본부/인사 관리자만 목록을 관리할 수 있습니다.")
        data = request.data
        value = data["value"].strip()
        if not value:
            return self.error("값(코드)을 입력하세요.")
        if OptionItem.objects.filter(category=data["category"], value=value).exists():
            return self.error("이미 존재하는 값입니다.")
        opt = OptionItem.objects.create(
            category=data["category"], value=value, label=data["label"],
            order=data.get("order") or 0, allow_custom=bool(data.get("allow_custom")))
        return self.success(OptionItemSerializer(opt).data)

    @validate_serializer(UpdateOptionSerializer)
    @admin_role_required
    def put(self, request):
        if not self._require_hq(request):
            return self.error("본부/인사 관리자만 목록을 관리할 수 있습니다.")
        data = request.data
        opt = OptionItem.objects.filter(id=data["id"]).first()
        if not opt:
            return self.error("Option does not exist")
        # value(코드)는 기존 레코드 참조 보호를 위해 변경 불가. label/order/활성/맞춤만 수정.
        if "label" in data:
            opt.label = data["label"]
        if "order" in data:
            opt.order = data["order"]
        if "is_active" in data:
            opt.is_active = data["is_active"]
        if "allow_custom" in data:
            opt.allow_custom = data["allow_custom"]
        opt.save()
        return self.success(OptionItemSerializer(opt).data)

    @admin_role_required
    def delete(self, request):
        if not self._require_hq(request):
            return self.error("본부/인사 관리자만 목록을 관리할 수 있습니다.")
        opt = OptionItem.objects.filter(id=request.GET.get("id")).first()
        if not opt:
            return self.error("Option does not exist")
        opt.delete()
        return self.success("Deleted")


class OptionReorderAPI(APIView):
    @validate_serializer(ReorderOptionSerializer)
    @admin_role_required
    def post(self, request):
        """카테고리 내 항목 순서를 ids 배열 순서대로 0,1,2…로 재설정."""
        actor_all, _, _ = staff_scope(request.user)
        if not actor_all:
            return self.error("본부/인사 관리자만 목록을 관리할 수 있습니다.")
        data = request.data
        for idx, oid in enumerate(data["ids"]):
            OptionItem.objects.filter(id=oid, category=data["category"]).update(order=idx)
        qs = OptionItem.objects.filter(category=data["category"])
        return self.success(OptionItemSerializer(qs, many=True).data)


class StudentListAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """학생(STUDENT 역할) 목록. 학부모/직원 제외. 주 횟수·현재 시간표 슬롯 수 포함."""
        all_branch, branch_id, role = staff_scope(request.user)
        qs = AcademyProfile.objects.select_related("user", "user__student_profile").filter(
            role=AcademyRole.STUDENT)
        if not all_branch:
            if not viewable_branch_ids(request.user):
                return self.error("No branch scope assigned")
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        counts = dict(StudentTimetable.objects.exclude(status="ENDED")
                      .values("student_id").annotate(c=Count("id"))
                      .values_list("student_id", "c"))
        # 보호자 수 집계
        gcounts = dict(GuardianStudent.objects.values("student_id")
                       .annotate(c=Count("id")).values_list("student_id", "c"))
        out = []
        for p in qs:
            u = p.user
            real_name = ""
            try:
                real_name = u.userprofile.real_name or ""
            except Exception:
                real_name = ""
            sp = getattr(u, "student_profile", None)
            out.append({"id": u.id, "username": u.username, "real_name": real_name,
                        "branch": (p.branch.name if p.branch_id else ""),
                        "branch_id": p.branch_id,
                        "school_type": (sp.school_type if sp else ""),
                        "school_name": (sp.school_name if sp else ""),
                        "grade": (sp.grade if sp else ""),
                        "parent_name": (sp.parent_name if sp else ""),
                        "parent_phone": (sp.parent_phone if sp else ""),
                        "enrollment_status": (sp.enrollment_status if sp else EnrollmentStatus.ENROLLED),
                        "weekly_sessions": (sp.weekly_sessions if sp else None),
                        "guardian_count": gcounts.get(u.id, 0),
                        "slot_count": counts.get(u.id, 0)})
        return self.success(out)


class StudentWeeklyAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """학생 등록 정보의 주 교육 회수를 수정(시간표 수에 맞춤)."""
        sid = request.data.get("student_id")
        ws = request.data.get("weekly_sessions")
        prof = AcademyProfile.objects.filter(user_id=sid).first()
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        sp = StudentProfile.objects.filter(user_id=sid).first()
        if not sp:
            return self.error("학생 등록 정보가 없습니다.")
        try:
            sp.weekly_sessions = int(ws)
        except (TypeError, ValueError):
            return self.error("주 횟수 값이 올바르지 않습니다.")
        sp.save(update_fields=["weekly_sessions"])
        return self.success({"student_id": sid, "weekly_sessions": sp.weekly_sessions})


class StudentTimetableAdminAPI(APIView):
    """학생별 개별 수업 시간표(12) 관리. 지점 스코프."""

    def _branch_ok(self, request, branch_id):
        return can_manage_branch(request.user, branch_id)

    @admin_role_required
    def get(self, request):
        """student_id 로 특정 학생의 시간표, 또는 branch/weekday 로 지점 전체 조회."""
        qs = StudentTimetable.objects.select_related(
            "student", "branch", "instructor").exclude(status="ENDED")
        student_id = request.GET.get("student_id")
        if student_id:
            qs = qs.filter(student_id=student_id)
        weekday = request.GET.get("weekday")
        if weekday not in (None, ""):
            qs = qs.filter(weekday=weekday)
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch:
            if not viewable_branch_ids(request.user):
                return self.error("No branch scope assigned")
            qs = qs.filter(branch_id__in=(viewable_branch_ids(request.user) or []))
        return self.success(StudentTimetableSerializer(qs, many=True).data)

    @validate_serializer(CreateStudentTimetableSerializer)
    @admin_role_required
    def post(self, request):
        data = request.data
        student = User.objects.filter(id=data["student_id"]).first()
        if not student:
            return self.error("Student does not exist")
        profile = getattr(student, "academy_profile", None)
        branch = profile.branch if profile else None
        if not branch:
            return self.error("학생의 소속 지점이 없습니다.")
        if not self._branch_ok(request, branch.id):
            return self.error("No permission for this branch")
        instructor = None
        if data.get("instructor_id"):
            instructor = User.objects.filter(id=data["instructor_id"]).first()
        prog = data.get("program", "") or ""
        # 동일 학생·요일·시각·과정 중복 방지(과정이 다르면 같은 시간대 허용 — 격주 번갈아 수강 등)
        if StudentTimetable.objects.filter(student=student, weekday=data["weekday"],
                                           start_time=data["start_time"], program=prog
                                           ).exclude(status="ENDED").exists():
            return self.error("같은 요일·시각에 같은 과정 수업이 이미 있습니다.")
        slot = StudentTimetable.objects.create(
            student=student, branch=branch,
            class_type=data.get("class_type") or LessonType.PRIVATE,
            weekday=data["weekday"], start_time=data["start_time"],
            duration_minutes=data.get("duration_minutes") or 60,
            instructor=instructor, program=prog,
            subject=data.get("subject") or resolve_program_label(prog),
            frequency=data.get("frequency") or "WEEKLY",
            room=data.get("room", "") or "")
        slot = StudentTimetable.objects.select_related("student", "branch", "instructor").get(pk=slot.pk)
        TimetableChange.objects.create(
            student=student, actor=request.user, action="CREATE",
            reason=data.get("reason", "") or "신규 등록",
            detail=f"{_WD[slot.weekday]} {str(slot.start_time)[:5]} {slot.subject or ''}".strip())
        return self.success(StudentTimetableSerializer(slot).data)

    @validate_serializer(EditStudentTimetableSerializer)
    @admin_role_required
    def put(self, request):
        data = request.data
        slot = StudentTimetable.objects.select_related("branch", "student").filter(id=data["id"]).first()
        if not slot:
            return self.error("Timetable does not exist")
        if not self._branch_ok(request, slot.branch_id):
            return self.error("No permission for this branch")
        reason = (data.get("reason") or "").strip()
        if not reason:
            return self.error("변경 이유를 입력하세요.")
        before = {"weekday": slot.weekday, "start_time": str(slot.start_time)[:5],
                  "duration_minutes": slot.duration_minutes, "program": slot.program,
                  "frequency": slot.frequency, "instructor_id": slot.instructor_id}
        for f in ("weekday", "start_time", "duration_minutes", "subject", "room", "status", "frequency"):
            if f in data:
                setattr(slot, f, data[f])
        if "program" in data:
            slot.program = data["program"] or ""
            slot.subject = resolve_program_label(slot.program)
        if "instructor_id" in data:
            slot.instructor = User.objects.filter(id=data["instructor_id"]).first() if data["instructor_id"] else None
        slot.save()
        # 변경 항목 요약 (기존값 → 변경값)
        labels = {"weekday": "요일", "start_time": "시각", "duration_minutes": "수업길이",
                  "program": "과정", "frequency": "반복", "instructor_id": "강사"}
        after = {"weekday": slot.weekday, "start_time": str(slot.start_time)[:5],
                 "duration_minutes": slot.duration_minutes, "program": slot.program,
                 "frequency": slot.frequency, "instructor_id": slot.instructor_id}

        def _fmt(field, val):
            if field == "instructor_id" and val in (None, ""):
                return "미배정"
            if field == "program" and val in (None, ""):
                return "미지정"
            if val in (None, ""):
                return "-"
            if field == "weekday":
                return _WD[val] if isinstance(val, int) and 0 <= val < len(_WD) else str(val)
            if field == "duration_minutes":
                return f"{val}분"
            if field == "frequency":
                return {"WEEKLY": "매주", "BIWEEKLY": "격주"}.get(val, str(val))
            if field == "program":
                return resolve_program_label(val) or "미지정"
            if field == "instructor_id":
                u = User.objects.filter(id=val).first()
                if not u:
                    return "미배정"
                try:
                    return u.userprofile.real_name or u.username
                except Exception:
                    return u.username
            return str(val)

        parts = [f"{labels[k]} {_fmt(k, before.get(k))} → {_fmt(k, after.get(k))}"
                 for k in labels if before.get(k) != after.get(k)]
        TimetableChange.objects.create(
            student=slot.student, actor=request.user, action="UPDATE", reason=reason,
            detail=("; ".join(parts))[:255] if parts else "수정")
        slot = StudentTimetable.objects.select_related("student", "branch", "instructor").get(pk=slot.pk)
        return self.success(StudentTimetableSerializer(slot).data)

    @admin_role_required
    def delete(self, request):
        slot = StudentTimetable.objects.select_related("branch", "student").filter(id=request.GET.get("id")).first()
        if not slot:
            return self.error("Timetable does not exist")
        if not self._branch_ok(request, slot.branch_id):
            return self.error("No permission for this branch")
        reason = (request.GET.get("reason") or "").strip()
        if not reason:
            return self.error("삭제 이유를 입력하세요.")
        TimetableChange.objects.create(
            student=slot.student, actor=request.user, action="DELETE", reason=reason,
            detail=f"{_WD[slot.weekday]} {str(slot.start_time)[:5]} {slot.subject or ''} 삭제".strip())
        slot.delete()
        return self.success("Deleted")


MANAGER_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN, AcademyRole.REGIONAL_MANAGER,
                 AcademyRole.BRANCH_MANAGER, AcademyRole.VICE_PRINCIPAL}


def _is_manager(user):
    _, _, role = staff_scope(user)
    return role in MANAGER_ROLES or user.is_super_admin()


class TimetableChangeAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """학생 시간표 변경 이력. student_id."""
        sid = request.GET.get("student_id")
        prof = AcademyProfile.objects.filter(user_id=sid).first()
        if prof and not can_view_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        manager = _is_manager(request.user)
        out = []
        ACT = {"CREATE": "등록", "UPDATE": "수정", "DELETE": "삭제", "EDIT": "이력수정"}
        for c in TimetableChange.objects.filter(student_id=sid).select_related("actor")[:200]:
            an = ""
            if c.actor_id:
                try:
                    an = c.actor.userprofile.real_name or c.actor.username
                except Exception:
                    an = c.actor.username
            out.append({"id": c.id, "action": ACT.get(c.action, c.action), "reason": c.reason,
                        "detail": c.detail, "actor": an, "time": str(c.create_time)[:16],
                        "can_edit": (c.action != "EDIT") and (manager or c.actor_id == request.user.id)})
        return self.success(out)


class TimetableChangeEditAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """이력 사유 수정. 원장 이상은 전체, 그 외는 본인 작성분만. 수정도 이력으로 기록."""
        cid = request.data.get("id")
        new_reason = (request.data.get("reason") or "").strip()
        if not new_reason:
            return self.error("사유를 입력하세요.")
        c = TimetableChange.objects.select_related("student").filter(id=cid).first()
        if not c:
            return self.error("이력이 없습니다.")
        prof = AcademyProfile.objects.filter(user_id=c.student_id).first()
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        if not _is_manager(request.user) and c.actor_id != request.user.id:
            return self.error("본인이 작성한 이력만 수정할 수 있습니다.")
        old = c.reason
        c.reason = new_reason
        c.save(update_fields=["reason"])
        TimetableChange.objects.create(
            student=c.student, actor=request.user, action="EDIT", reason=new_reason,
            detail=(f"이력 사유 수정: {old} → {new_reason}")[:255])
        return self.success("ok")


# ── 학생 회원관리(5차): 상세·수정·상태변경·보호자·통합 상담 ──

def _name_of(u):
    if not u:
        return None
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username


def _student_profile_dict(sp):
    if not sp:
        return {}
    return {
        "real_name": "",  # 상위에서 채움
        "birth_date": str(sp.birth_date) if sp.birth_date else "",
        "gender": sp.gender or "",
        "zipcode": sp.zipcode or "", "address": sp.address or "", "address_detail": sp.address_detail or "",
        "student_phone": sp.student_phone or "",
        "parent_name": sp.parent_name or "", "parent_phone": sp.parent_phone or "",
        "school_type": sp.school_type or "", "school_name": sp.school_name or "", "grade": sp.grade or "",
        "enrollment_date": str(sp.enrollment_date) if sp.enrollment_date else "",
        "lesson_start_date": str(sp.lesson_start_date) if sp.lesson_start_date else "",
        "weekly_sessions": sp.weekly_sessions,
        "program": sp.program or "",
    }


def _get_or_create_student_lead(student):
    """학생의 통합 상담 타임라인용 Lead 컨테이너. 등록 전환 학생은 이미 lead 존재.
    없으면(직접 생성 등) 최소 정보로 1건 생성해 converted_user 로 연결한다."""
    lead = Lead.objects.filter(converted_user=student).order_by("id").first()
    if lead:
        return lead
    prof = getattr(student, "academy_profile", None)
    sp = getattr(student, "student_profile", None)
    branch = (prof.branch if prof and prof.branch_id else None) or Branch.objects.first()
    return Lead.objects.create(
        branch=branch,
        parent_name=(sp.parent_name if sp else "") or "",
        parent_phone=(sp.parent_phone if sp else "") or "",
        student_name=_name_of(student),
        school_type=(sp.school_type if sp else "") or "",
        school_name=(sp.school_name if sp else "") or "",
        grade=(sp.grade if sp else "") or "",
        status=LeadStatus.CONVERTED, converted_user=student)


class StudentDetailAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """학생 상세: 인적사항·보호자·상태이력·통합 상담 타임라인."""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_view_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        sp = getattr(u, "student_profile", None)
        guardians = []
        for g in GuardianStudent.objects.select_related("parent").filter(student=u):
            pp = getattr(g.parent, "academy_profile", None)
            guardians.append({"link_id": g.id, "parent_id": g.parent_id, "username": g.parent.username,
                              "name": _name_of(g.parent), "relation": g.relation,
                              "phone": (pp.phone if pp else "")})
        history = [{"id": c.id, "from": c.from_status, "to": c.to_status, "reason": c.reason,
                    "effective_date": str(c.effective_date) if c.effective_date else "",
                    "actor": _name_of(c.actor) if c.actor_id else "", "time": str(c.create_time)[:16]}
                   for c in StudentStatusChange.objects.filter(student=u).select_related("actor")[:200]]
        lead = Lead.objects.filter(converted_user=u).order_by("id").first()
        lead_data = LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data if lead else None
        pdict = _student_profile_dict(sp)
        pdict["real_name"] = _name_of(u)
        return self.success({
            "id": u.id, "username": u.username, "real_name": _name_of(u),
            "branch": (prof.branch.name if prof and prof.branch_id else ""),
            "branch_id": prof.branch_id if prof else None,
            "enrollment_status": sp.enrollment_status if sp else EnrollmentStatus.ENROLLED,
            "profile": pdict, "guardians": guardians, "status_history": history,
            "lead": lead_data, "lead_id": lead.id if lead else None,
        })

    @admin_role_required
    def put(self, request):
        """학생 인적사항 수정."""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        sp, _ = StudentProfile.objects.get_or_create(user=u)
        rn = (data.get("real_name") or "").strip()
        if rn:
            up, _ = UserProfile.objects.get_or_create(user=u)
            up.real_name = rn
            up.save(update_fields=["real_name"])
        for f in ("gender", "zipcode", "address", "address_detail", "student_phone",
                  "parent_name", "parent_phone", "school_type", "school_name", "grade", "program"):
            if f in data:
                setattr(sp, f, data.get(f) or "")
        for df in ("birth_date", "lesson_start_date", "enrollment_date"):
            if df in data:
                setattr(sp, df, data.get(df) or None)
        if "weekly_sessions" in data:
            sp.weekly_sessions = data.get("weekly_sessions") or None
        sp.save()
        return self.success("ok")


class StudentStatusAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """등록상태 변경(재원/휴원/퇴원/재등록) + 이력 영구 기록."""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        to_status = data.get("status")
        if to_status not in (EnrollmentStatus.ENROLLED, EnrollmentStatus.ON_LEAVE, EnrollmentStatus.WITHDRAWN):
            return self.error("상태 값이 올바르지 않습니다.")
        sp, _ = StudentProfile.objects.get_or_create(user=u)
        from_status = sp.enrollment_status
        if from_status == to_status:
            return self.error("이미 해당 상태입니다.")
        sp.enrollment_status = to_status
        sp.save(update_fields=["enrollment_status"])
        StudentStatusChange.objects.create(
            student=u, from_status=from_status, to_status=to_status,
            reason=(data.get("reason") or "").strip(),
            effective_date=data.get("effective_date") or None, actor=request.user)
        return self.success("ok")


class StudentGuardianAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """보호자 연결/계정 생성. {student_id, parent_phone, parent_name, relation, password?}"""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        branch = (prof.branch if prof and prof.branch_id else None) or Branch.objects.first()
        norm = _norm_phone(data.get("parent_phone"))
        if not norm:
            return self.error("보호자 연락처를 입력하세요.")
        parent_user = None
        pp = AcademyProfile.objects.select_related("user").filter(role=AcademyRole.PARENT, phone=norm).first()
        if pp:
            parent_user = pp.user
        if parent_user is None:
            username = "p" + norm
            if User.objects.filter(username=username).exists():
                base, i = username, 1
                while User.objects.filter(username=username).exists():
                    i += 1
                    username = "%s%d" % (base, i)
            pw = (data.get("password") or "").strip() or norm
            parent_user = User.objects.create(username=username, is_disabled=False)
            parent_user.set_password(pw)
            parent_user.save()
            UserProfile.objects.create(user=parent_user, real_name=(data.get("parent_name") or "학부모"))
            p2 = apply_role(parent_user, AcademyRole.PARENT, branch)
            p2.phone = norm
            p2.save(update_fields=["phone"])
        elif (data.get("parent_name") or "").strip():
            up, _ = UserProfile.objects.get_or_create(user=parent_user)
            if not (up.real_name or "").strip():
                up.real_name = data.get("parent_name")
                up.save(update_fields=["real_name"])
        link, created = GuardianStudent.objects.get_or_create(
            parent=parent_user, student=u,
            defaults={"relation": data.get("relation") or "학부모"})
        if not created and (data.get("relation") or "").strip():
            link.relation = data.get("relation")
            link.save(update_fields=["relation"])
        return self.success({"username": parent_user.username, "is_new": parent_user.last_login is None})

    @admin_role_required
    def delete(self, request):
        """보호자 연결 해제."""
        link = GuardianStudent.objects.select_related("student").filter(id=request.GET.get("link_id")).first()
        if not link:
            return self.error("연결이 없습니다.")
        prof = getattr(link.student, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        link.delete()
        return self.success("ok")

    @admin_role_required
    def put(self, request):
        """보호자 비밀번호 초기화. {parent_id, password}"""
        data = request.data
        p = User.objects.filter(id=data.get("parent_id")).first()
        if not p:
            return self.error("보호자 계정이 없습니다.")
        ok = _is_manager(request.user)
        if not ok:
            for g in GuardianStudent.objects.select_related("student").filter(parent=p):
                gp = getattr(g.student, "academy_profile", None)
                if gp and can_manage_branch(request.user, gp.branch_id):
                    ok = True
                    break
        if not ok:
            return self.error("권한이 없습니다.")
        pw = (data.get("password") or "").strip()
        if len(pw) < 6:
            return self.error("비밀번호는 6자 이상이어야 합니다.")
        p.set_password(pw)
        p.save()
        return self.success("ok")


class StudentCounselAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """등록 학생 통합 상담 기록 추가(필요 시 lead 컨테이너 생성). 수정/삭제는 lead/note 재사용."""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        summary = (data.get("summary") or "").strip()
        if not summary:
            return self.error("상담 내용을 입력하세요.")
        lead = _get_or_create_student_lead(u)
        CounselingLog.objects.create(
            lead=lead, author=request.user,
            channel=data.get("channel") or "VISIT",
            summary=summary, counsel_at=data.get("counsel_at") or None)
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)
