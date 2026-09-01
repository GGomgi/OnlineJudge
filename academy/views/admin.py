import re
import secrets
from datetime import timedelta, datetime, date as date_cls

from django.utils.timezone import now
from django.db.models import Count, Q, F


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
                      Lead, LeadStatus, CounselingLog, CounselingLogEdit, CounselReservation, StudentProfile, EnrollmentStatus,
                      OptionItem, StudentTimetable, LessonType, GuardianStudent,
                      StaffProfile, HRNotice, StaffDocument, StaffProfileHistory, StudentRegisterLog,
                      TimetableChange, StudentStatusChange, StudentCredential, StaffChangeLog, DailyAttendance,
                      AttendanceChange, LessonOccurrence, OccurrenceStatus, LessonProgress,
                      MsgTemplateGroup, MsgTemplate, FixedTemplate, KioskDevice, KioskDeviceStatus,
                      Holiday, WorkSchedule,
                      staff_field_label, staff_value_text)
_WD = ["월", "화", "수", "목", "금", "토", "일"]


def _now_kst_str():
    """현재 시각을 KST(UTC+9) 'YYYY-MM-DD HH:MM' 문자열로 반환(이력·타임스탬프 표시용).
    컨테이너 시계·now()는 UTC이므로 사용자 표시 시각은 반드시 +9h. 새 시각 기록 시 항상 사용."""
    return str(now() + timedelta(hours=9))[:16]


def _kst_dt_str(dt):
    """저장된 UTC datetime을 KST(+9h) 'YYYY-MM-DD HH:MM' 문자열로. API 응답에 create_time/update_time 등
    사용자 표시용 시각을 담을 때는 str(dt)[:16] 대신 반드시 이 함수를 써야 한다(그렇지 않으면 9시간 어긋남)."""
    if not dt:
        return ""
    return str(dt + timedelta(hours=9))[:16]
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
                           ConvertLeadSerializer, StudentRegisterSerializer, CloseLeadSerializer,
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


def get_or_create_guardian(student, parent_name, parent_phone, branch, login_id="", password="", relation=""):
    """학생의 학부모(보호자) 계정을 전화번호로 찾거나 생성하고 자녀로 연결한다(11 §9).
    동일 전화번호의 학부모가 이미 있으면(형제 등록 등) 그 계정에 연결만 한다."""
    norm = _norm_phone(parent_phone)
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
        pw = (password or "").strip() or (norm[-4:] if norm else username)  # 기본 비번: 연락처 뒤 4자리
        parent_user = User.objects.create(username=username, is_disabled=False)
        parent_user.set_password(pw)
        parent_user.save()
        UserProfile.objects.create(user=parent_user, real_name=parent_name or "학부모")
        profile = apply_role(parent_user, AcademyRole.PARENT, branch)
        profile.phone = norm
        profile.save(update_fields=["phone"])
    GuardianStudent.objects.get_or_create(parent=parent_user, student=student,
                                          defaults={"relation": (relation or "").strip() or "학부모"})
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

        old = AcademyProfile.objects.filter(user=target).first()
        old_role = old.role if old else ""
        old_branch = (old.branch.name if (old and old.branch_id) else "본부/미지정") if old else ""
        profile = apply_role(target, role, branch)
        _apply_managed(profile, role, managed_ids)
        new_branch = branch.name if branch else "본부/미지정"
        parts = []
        if old_role != role:
            parts.append("역할 %s→%s" % (_role_label(old_role), _role_label(role)))
        if old_branch != new_branch:
            parts.append("지점 %s→%s" % (old_branch, new_branch))
        if parts:
            _log_staff(target, request.user, "ROLE", "; ".join(parts), data.get("reason"))
        return self.success({"user_id": target.id, "role": role,
                             "branch_id": branch.id if branch else None,
                             "managed_branch_ids": managed_ids})


def _role_label(role):
    return dict(ACADEMY_ROLE_CHOICES).get(role, role)


def _log_staff(staff, actor, ctype, detail, reason):
    StaffChangeLog.objects.create(staff=staff, actor=actor, change_type=ctype,
                                  detail=(detail or "")[:255], reason=(reason or "")[:255])


def _staff_no_prefix(role, branch):
    """사번 앞 2자리: 전지점 역할/미배정은 00, 그 외는 지점 코드 숫자(예: B002→02)."""
    if role in ALL_BRANCH_ROLES or branch is None:
        return "00"
    digits = "".join(ch for ch in (branch.code or "") if ch.isdigit())
    n = int(digits) if digits else 0
    return "%02d" % (n % 100)


def gen_enroll_no(branch):
    """원번 생성: 지점 prefix(2자리) + 일련 4자리(지점별 최대값+1)."""
    if branch is None:
        prefix = "00"
    else:
        digits = "".join(ch for ch in (branch.code or "") if ch.isdigit())
        prefix = "%02d" % ((int(digits) if digits else 0) % 100)
    maxseq = 0
    for sp in StudentProfile.objects.filter(enroll_no__startswith=prefix).exclude(enroll_no=""):
        tail = sp.enroll_no[len(prefix):]
        if tail.isdigit():
            maxseq = max(maxseq, int(tail))
    return "%s%04d" % (prefix, maxseq + 1)


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


class InstructorListAPI(APIView):
    @admin_role_required
    def get(self, request):
        """담당 강사 선택용 경량 목록(인사관리 권한 불필요). 열람 가능 지점의 교직원."""
        view = viewable_branch_ids(request.user)
        qs = AcademyProfile.objects.select_related(
            "user", "user__staff_profile", "branch").filter(
            role__in=STAFF_ROLES, user__is_disabled=False)
        if view is not None:
            qs = qs.filter(branch_id__in=view)
        qs = qs.order_by("branch_id", "user__username")
        return self.success([_staff_brief(p) for p in qs])


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
        if request.GET.get("show_inactive") != "1":
            qs = qs.filter(user__is_disabled=False)
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

        was_active = not profile.user.is_disabled
        profile.user.is_disabled = not data["is_active"]
        profile.user.save()
        if was_active != bool(data["is_active"]):
            _log_staff(profile.user, request.user, "ACTIVE",
                       "활성화" if data["is_active"] else "비활성화", data.get("reason"))
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
        new_branch = profile.branch.name if profile.branch_id else "본부"
        profile.user.username = new_no
        profile.user.save(update_fields=["username"])
        profile.staff_no = new_no
        profile.save(update_fields=["staff_no"])
        _log_staff(profile.user, request.user, "SABUN",
                   "사번 %s → %s (%s)" % (old_no, new_no, new_branch), request.data.get("reason"))
        return self.success({"old_sabun": old_no, "new_sabun": new_no, "branch": new_branch})

    @admin_role_required
    def get(self, request):
        """재발급 미리보기: 현재 사번 → 새 사번(현재 소속 기준)."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        profile = AcademyProfile.objects.select_related("user", "branch").filter(
            user_id=request.GET.get("user_id"), role__in=STAFF_ROLES).first()
        if not profile:
            return self.error("Staff does not exist")
        new_no = gen_staff_no(profile.role, profile.branch)
        while User.objects.filter(username=new_no).exclude(id=profile.user_id).exists():
            prefix, tail = new_no[:2], new_no[2:]
            new_no = "%s%03d" % (prefix, (int(tail) if tail.isdigit() else 0) + 1)
        return self.success({"old_sabun": profile.user.username, "new_sabun": new_no,
                             "branch": (profile.branch.name if profile.branch_id else "본부"),
                             "changed": new_no != profile.user.username})


class StaffHistoryAPI(APIView):
    @admin_role_required
    def get(self, request):
        """직원 변경 이력(역할/지점/활성/사번)."""
        if not can_manage_staff(request.user):
            return self.error("직원 관리 권한이 없습니다.")
        uid = request.GET.get("user_id")
        TYPE = {"ROLE": "역할/지점", "ACTIVE": "활성상태", "SABUN": "사번"}
        out = []
        for c in StaffChangeLog.objects.filter(staff_id=uid).select_related("actor")[:200]:
            an = ""
            if c.actor_id:
                try:
                    an = c.actor.userprofile.real_name or c.actor.username
                except Exception:
                    an = c.actor.username
            out.append({"id": c.id, "type": TYPE.get(c.change_type, c.change_type),
                        "detail": c.detail, "reason": c.reason,
                        "actor": an, "time": _kst_dt_str(c.create_time)})
        return self.success(out)


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
            return None, [], "본부 관리자만 부여할 수 있는 역할입니다."
        return None, [], None
    # 지부장 부여는 본부만(여러 지점 위임이라)
    if role == AcademyRole.REGIONAL_MANAGER:
        if not actor_all:
            return None, [], "지부장은 본부 관리자만 부여할 수 있습니다."
        managed, merr = _parse_managed(request)
        if merr:
            return None, [], merr
        if not managed:
            return None, [], "지부장은 열람 지점을 1개 이상 선택해야 합니다."
        return None, managed, None
    # 단일지점 역할(원장/부원장/강사/조교)
    if not actor_all:
        # 원장 등 지점 관리자: 본인 소속 지점으로 강제, 겸직 열람지점 불가(타 지점 지정 금지)
        own = editable_branch_ids(request.user)
        if not own:
            return None, [], "소속 지점이 없어 직원을 만들 수 없습니다."
        branch = Branch.objects.filter(id=own[0], is_active=True).first()
        if not branch:
            return None, [], "소속 지점이 유효하지 않습니다."
        return branch, [], None
    # 본부: 지점 지정 + 선택적 열람지점(겸직)
    managed, merr = _parse_managed(request)
    if merr:
        return None, [], merr
    if not data.get("branch_id"):
        return None, [], "지점을 선택하세요."
    branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
    if not branch:
        return None, [], "Invalid branch"
    return branch, managed, None


def _apply_managed(profile, role, managed_ids):
    # 소속 지점은 어차피 관리 대상이라(editable_branch_ids 가 늘 넣는다) 겸직 목록에
    # 또 넣을 필요가 없다. 넣어 두면 목록에 '김포 +김포' 처럼 같은 이름이 두 번 보인다.
    ids = [b for b in (managed_ids or []) if b != profile.branch_id]
    if ids:
        profile.managed_branches.set(Branch.objects.filter(id__in=ids))
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
                         "actor": an, "reason": h.reason, "time": _kst_dt_str(h.create_time)})
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
        # 직원이 잘못 넣은 것을 고쳐 달라고 기다리면 며칠씩 밀린다. 원장이 바로 고치고 이력에 남긴다.
        # 다만 서명·동의는 본인만 한다 — 대신 해 줄 수 있는 것이 아니다.
        for f in ("dependents", "emergency_contacts"):
            if f in d:
                v = d.get(f)
                setattr(sp, f, v if isinstance(v, str) else _json.dumps(v or [], ensure_ascii=False))
        if "dependents_decided" in d:
            sp.dependents_decided = bool(d.get("dependents_decided"))
        # 서류 '해당사항 없음' — 조교·아르바이트는 성적증명서 같은 것이 필요 없을 수 있다.
        # 면제는 원장 이상만 정한다(직원 본인이 스스로 빼면 뜻이 없다).
        if "waived_docs" in d:
            role = getattr(getattr(request.user, "academy_profile", None), "role", "")
            if not (role in DIRECTOR_UP_ROLES or role == AcademyRole.HR_ADMIN
                    or request.user.is_super_admin()):
                return self.error("해당사항 없음은 원장 이상만 정할 수 있습니다.")
            raw = d.get("waived_docs") or []
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except (ValueError, TypeError):
                    raw = []
            sp.waived_docs = _json.dumps([str(x) for x in raw][:20], ensure_ascii=False)
        sp.save()
        after = {f: getattr(sp, f) for f in TRACKED_HR_FIELDS}
        record_hr_history(sp.user, request.user, before, after,
                          (request.data.get("reason") or "").strip()[:500])
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
                        # 누가 바꿨는지 알아야 그 직원 인사정보로 바로 갈 수 있다
                        "staff_id": n.staff_id, "staff": _name_of(n.staff) if n.staff_id else "",
                        "branch": n.branch.name if n.branch_id else None,
                        "create_time": _kst_dt_str(n.create_time)})
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
            "logs__author", "logs__edited_by", "logs__edits__actor",
            "reservations__created_by")
        status = request.GET.get("status")
        if status == "RESERVED":  # 상담예약중: 미래 ACTIVE 예약 보유(미전환)
            qs = qs.filter(status=LeadStatus.NEW, reservations__status="ACTIVE",
                           reservations__scheduled_at__gte=now()).distinct()
        elif status == "NEW":  # 상담: NEW 이면서 미래 예약 없음
            qs = qs.filter(status=LeadStatus.NEW).exclude(
                reservations__status="ACTIVE", reservations__scheduled_at__gte=now())
        elif status:
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


class LeadEnrollAckAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """학부모 수정('수정됨' 표시) 확인 처리. {lead_id} → enroll_edited=False."""
        lead = Lead.objects.filter(id=request.data.get("lead_id")).first()
        if not lead:
            return self.error("상담 신청이 없습니다.")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("권한이 없습니다.")
        lead.enroll_edited = False
        lead.save(update_fields=["enroll_edited"])
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)


class EnrollLinkAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """등록 링크 생성/재발급. {lead_id}. 7일 유효 토큰 발급."""
        lead = Lead.objects.filter(id=request.data.get("lead_id")).first()
        if not lead:
            return self.error("상담 신청이 없습니다.")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("권한이 없습니다.")
        if lead.status == LeadStatus.CONVERTED:
            return self.error("이미 등록 완료된 상담입니다.")
        lead.enroll_token = _rand_str(24)
        lead.enroll_token_expires = now() + timedelta(days=7)
        if lead.enroll_status != "SUBMITTED":
            lead.enroll_status = "SENT"
        lead.save(update_fields=["enroll_token", "enroll_token_expires", "enroll_status"])
        path = "/portal/?enroll=" + lead.enroll_token
        url = request.build_absolute_uri(path)
        branch_name = lead.branch.name if lead.branch else ""
        message = _fill_vars(_fixed_body(lead.branch_id, "enroll_link"), {
            "지점명": branch_name, "학원명": branch_name,
            "학생명": lead.student_name or "학생",
            "학부모명": lead.parent_name or "학부모",
            "링크": url,
        })
        return self.success({"token": lead.enroll_token,
                             "path": path, "url": url,
                             "expires": str(lead.enroll_token_expires)[:16],
                             "message": message})


class LeadEditAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """상담(리드) 기본 정보 수정 + 변경 이력. {lead_id, ...필드}"""
        data = request.data
        lead = Lead.objects.filter(id=data.get("lead_id")).first()
        if not lead:
            return self.error("상담 신청이 없습니다.")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("권한이 없습니다.")
        fields = [("parent_name", "학부모 이름", None), ("parent_phone", "학부모 연락처", None),
                  ("student_name", "자녀 이름", None), ("school_type", "학교 구분", "school_type"),
                  ("school_name", "학교 이름", None), ("grade", "학년", None),
                  ("purpose", "학생의 목표", "counseling_purpose"),
                  ("purpose_detail", "목표 상세", None), ("interest", "문의", None)]

        def _disp(category, val):
            v = (val or "").strip()
            if not v:
                return "(없음)"
            if category:
                o = OptionItem.objects.filter(category=category, value=v).first()
                if o:
                    return o.label
            return v

        changed = []
        for f, label, cat in fields:
            if f in data:
                oldv = getattr(lead, f)
                newv = (data.get(f) or "").strip()
                if oldv != newv:
                    setattr(lead, f, newv)
                    changed.append("%s(%s▸%s)" % (label, _disp(cat, oldv), _disp(cat, newv)))
        bid = data.get("branch_id")
        if bid and bid != lead.branch_id:
            b = Branch.objects.filter(id=bid).first()
            if b and can_manage_branch(request.user, b.id):
                oldb = lead.branch.name if lead.branch else "(없음)"
                lead.branch = b
                changed.append("지점(%s▸%s)" % (oldb, b.name))
        if "channel" in data and (data.get("channel") or "") != lead.channel:
            _CH = {"VISIT": "방문", "CALL": "전화", "MESSAGE": "문자", "ETC": "기타"}
            changed.append("상담 방법(%s▸%s)" % (_CH.get(lead.channel, lead.channel or "(없음)"),
                                            _CH.get(data.get("channel"), data.get("channel") or "(없음)")))
            lead.channel = data.get("channel") or "VISIT"
        if "counsel_at" in data:
            newc = _parse_kst_local_dt(data.get("counsel_at"))
            if newc != lead.counsel_at:
                changed.append("상담 일시(%s▸%s)" % (_kst_dt_str(lead.counsel_at) or "(없음)",
                                                 _kst_dt_str(newc) or "(없음)"))
                lead.counsel_at = newc
        if changed:
            try:
                log = _json.loads(lead.edit_log) if lead.edit_log else []
            except (ValueError, TypeError):
                log = []
            log.append({"time": _now_kst_str(), "by": _name_of(request.user),
                        "changes": ", ".join(changed)})
            lead.edit_log = _json.dumps(log, ensure_ascii=False)
            lead.save()
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)


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
            counsel_at=_parse_kst_local_dt(data.get("counsel_at")),
            next_contact_at=data.get("next_contact_at"))
        # 상태 단순화: 상담기록을 남겨도 '상담(NEW)' 유지(상담중 개념 폐지).
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


class ReservationAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """상담 예약 추가. {lead_id 또는 student_id, at 또는 date+time, note?}. 등록 후에도 계속 가능."""
        data = request.data
        if data.get("student_id") and not data.get("lead_id"):
            u = User.objects.filter(id=data.get("student_id")).first()
            if not u:
                return self.error("학생이 없습니다.")
            prof = getattr(u, "academy_profile", None)
            if prof and not can_manage_branch(request.user, prof.branch_id):
                return self.error("No permission for this branch")
            lead = _get_or_create_student_lead(u)
        else:
            lead = Lead.objects.filter(id=data.get("lead_id")).first()
        if not lead:
            return self.error("상담 신청이 없습니다.")
        if not can_manage_branch(request.user, lead.branch_id):
            return self.error("No permission for this branch")
        at = (data.get("at") or "").strip()  # 단일 일시 'YYYY-MM-DDTHH:MM'
        d = (data.get("date") or "").strip()
        t = (data.get("time") or "").strip()
        if at:
            try:
                day_s, time_s = at.replace(" ", "T").split("T")[:2]
                day = datetime.strptime(day_s, "%Y-%m-%d").date()
                sched = _kst_to_utc(day, time_s[:5])
            except (ValueError, AttributeError):
                return self.error("예약 일시 형식이 올바르지 않습니다.")
        elif d and t:
            try:
                sched = _kst_to_utc(datetime.strptime(d, "%Y-%m-%d").date(), t)
            except (ValueError, AttributeError):
                return self.error("날짜/시간 형식이 올바르지 않습니다.")
        else:
            return self.error("예약 일시를 입력하세요.")
        CounselReservation.objects.create(
            lead=lead, scheduled_at=sched, note=(data.get("note") or "").strip(),
            channel=(data.get("channel") or "VISIT"),
            created_by=request.user)
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)

    @admin_role_required
    def put(self, request):
        """상담 예약 일정 변경(약속시간 변경) + 사유·변경 이력. {reservation_id, at 또는 date+time, note?, reason}"""
        data = request.data
        r = CounselReservation.objects.select_related("lead").filter(id=data.get("reservation_id")).first()
        if not r:
            return self.error("예약이 없습니다.")
        if not can_manage_branch(request.user, r.lead.branch_id):
            return self.error("No permission for this branch")
        if r.status != CounselReservation.ACTIVE:
            return self.error("이미 처리된(기록작성/취소) 예약은 변경할 수 없습니다.")
        at = (data.get("at") or "").strip()
        d = (data.get("date") or "").strip()
        t = (data.get("time") or "").strip()
        reason = (data.get("reason") or "").strip()
        if not reason:
            return self.error("약속시간 변경 사유를 입력하세요.")
        try:
            if at:
                day_s, time_s = at.replace(" ", "T").split("T")[:2]
                sched = _kst_to_utc(datetime.strptime(day_s, "%Y-%m-%d").date(), time_s[:5])
            elif d and t:
                sched = _kst_to_utc(datetime.strptime(d, "%Y-%m-%d").date(), t)
            else:
                return self.error("예약 일시를 입력하세요.")
        except (ValueError, AttributeError):
            return self.error("예약 일시 형식이 올바르지 않습니다.")
        # 변경 이력(이전 값·사유 보존)
        new_note = (data.get("note") or "").strip()
        try:
            log = _json.loads(r.edit_log) if r.edit_log else []
        except (ValueError, TypeError):
            log = []
        log.append({"time": _now_kst_str(), "by": _name_of(request.user),
                    "old_at": _hm_kst(r.scheduled_at) and (str((r.scheduled_at + timedelta(hours=9)).date()) + " " + _hm_kst(r.scheduled_at)),
                    "old_note": r.note, "reason": reason})
        r.edit_log = _json.dumps(log, ensure_ascii=False)
        r.scheduled_at = sched
        r.note = new_note
        r.save()
        return self.success(LeadSerializer(r.lead, context={"show_hidden": _is_manager(request.user)}).data)

    @admin_role_required
    def delete(self, request):
        """상담 예약 취소(ACTIVE→CANCELLED). 사유 필수. ?reservation_id=&reason="""
        r = CounselReservation.objects.select_related("lead").filter(id=request.GET.get("reservation_id")).first()
        if not r:
            return self.error("예약이 없습니다.")
        if not can_manage_branch(request.user, r.lead.branch_id):
            return self.error("No permission for this branch")
        if r.status != CounselReservation.ACTIVE:
            return self.error("이미 처리된 예약입니다.")
        reason = (request.GET.get("reason") or "").strip()
        if not reason:
            return self.error("취소 사유를 입력하세요.")
        r.status = CounselReservation.CANCELLED
        r.cancel_reason = reason
        r.save(update_fields=["status", "cancel_reason"])
        return self.success(LeadSerializer(r.lead, context={"show_hidden": _is_manager(request.user)}).data)


class ReservationCompleteAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """예약된 상담을 실제로 진행한 뒤 상담 기록 작성(ACTIVE→DONE). {reservation_id, summary, channel?}"""
        data = request.data
        r = CounselReservation.objects.select_related("lead").filter(id=data.get("reservation_id")).first()
        if not r:
            return self.error("예약이 없습니다.")
        if not can_manage_branch(request.user, r.lead.branch_id):
            return self.error("No permission for this branch")
        if r.status != CounselReservation.ACTIVE:
            return self.error("이미 처리된 예약입니다.")
        summary = (data.get("summary") or "").strip()
        if not summary:
            return self.error("상담 내용을 입력하세요.")
        channel = (data.get("channel") or r.channel or "VISIT")
        log = CounselingLog.objects.create(
            lead=r.lead, author=request.user, channel=channel, summary=summary,
            counsel_at=r.scheduled_at)
        r.status = CounselReservation.DONE
        r.channel = channel
        r.completed_log = log
        r.save(update_fields=["status", "channel", "completed_log"])
        return self.success(LeadSerializer(r.lead, context={"show_hidden": _is_manager(request.user)}).data)


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


def _create_student_from_lead(request, lead, data, source="LEAD"):
    """리드를 활성 학생 계정으로 전환(계정+학생등록+시간표+보호자). (result, error) 반환.
    상담 전환·직접 등록 공용."""
    username = data["login_id"].lower()
    if User.objects.filter(username=username).exists():
        return None, "Login ID already exists"

    # 보호자 이름(입회원 신청서에서 입력/수정) 반영 — 보호자 계정·학생 기록에 사용
    pn = (data.get("parent_name") or "").strip()
    if pn and pn != lead.parent_name:
        lead.parent_name = pn
        lead.save(update_fields=["parent_name"])

    with transaction.atomic():
        user = User.objects.create(username=username, is_disabled=False)
        user.set_password(data["password"])
        user.save()
        UserProfile.objects.create(user=user, real_name=lead.student_name)
        apply_role(user, AcademyRole.STUDENT, lead.branch)
        StudentProfile.objects.create(
            user=user,
            enroll_no=gen_enroll_no(lead.branch),
            birth_date=data.get("birth_date"),
            gender=data.get("gender", "") or "",
            zipcode=data.get("zipcode", "") or "",
            address=data.get("address", "") or "",
            address_detail=data.get("address_detail", "") or "",
            student_phone=data.get("student_phone", "") or "",
            parent_name=lead.parent_name,
            parent_phone=lead.parent_phone,
            parent_relation=data.get("parent_relation", "") or "",
            notify_optin=bool(data.get("notify_optin")),
            guardian2_phone=data.get("guardian2_phone", "") or "",
            guardian2_relation=data.get("guardian2_relation", "") or "",
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
            consent_paper=bool(data.get("consent_paper")),
            consent_guardian_name=data.get("consent_guardian_name", "") or "",
            consent_signature=data.get("consent_signature", "") or "",
            # 동의를 받지 않았으면 동의일도 비워 둔다(등록일이 동의일로 둔갑하면 안 됨)
            consent_date=(data.get("consent_date")
                          or (now().date() if (data.get("consent_privacy") or data.get("consent_paper")) else None)),
            memo=data.get("memo", "") or "",
        )
        # 입회원 신청서의 요일/시간(class_schedule)으로 개별 시간표 자동 생성(12).
        # '추후 안내'면 미생성. 수업 길이는 학교급·주횟수 규칙으로 자동 계산.
        schedule_raw = data.get("class_schedule") or ""
        try:
            schedule = _json.loads(schedule_raw) if schedule_raw else []
        except (ValueError, TypeError):
            schedule = []
        if not data.get("schedule_pending"):
            default_dur = lesson_duration(lead.school_type, data.get("weekly_sessions"))
            # 회차끼리 시간이 겹치면(시각이 달라도 수업시간 때문에 물리는 경우 포함) 등록 자체를 막는다.
            # 격주 번갈아 수강은 같은 자리에 두 슬롯을 두는 정상 케이스라 제외.
            _chk = []
            for row in schedule:
                try:
                    _wd = int(row.get("day")); _tm = (row.get("time") or "").strip()
                    _du = int(row.get("duration") or 0) or default_dur
                except (AttributeError, TypeError, ValueError):
                    continue
                if 0 <= _wd <= 6 and _tm:
                    _chk.append((_wd, _tm, _du, (row.get("frequency") or "WEEKLY") == "BIWEEKLY"))
            for _i in range(len(_chk)):
                for _j in range(_i + 1, len(_chk)):
                    a, b = _chk[_i], _chk[_j]
                    if a[0] != b[0] or (a[3] and b[3]):
                        continue
                    if _time_overlaps(a[1], a[2], b[1], b[2]):
                        return self.error("%d회차와 %d회차 수업 시간이 겹칩니다: %s %s(%d분) ↔ %s(%d분)"
                                          % (_i + 1, _j + 1, _WD[a[0]], a[1], a[2], b[1], b[2]))
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
                # 회차별로 수업시간(초등 예외 등)·담당 선생님을 직접 지정할 수 있음. 미지정 시 자동 계산값.
                try:
                    dur = int(row.get("duration")) or default_dur
                except (TypeError, ValueError):
                    dur = default_dur
                instructor_id = row.get("instructor_id") or None
                # 격주 번갈아 짝 슬롯(week_offset=1)은 시작일을 1주 밀어 반대 주차에 수업
                af = data.get("lesson_start_date")
                if freq == "BIWEEKLY" and row.get("week_offset") and af:
                    try:
                        af = (datetime.strptime(af, "%Y-%m-%d").date() + timedelta(days=7)).isoformat()
                    except (ValueError, TypeError):
                        pass
                StudentTimetable.objects.create(
                    student=user, branch=lead.branch, class_type=LessonType.PRIVATE,
                    weekday=wd, start_time=tm, duration_minutes=dur, instructor_id=instructor_id,
                    program=prog, subject=subj, frequency=freq,
                    active_from=af)
        # 학부모(보호자) 계정 생성/연결 — 자녀 기록 열람용(11 §9)
        parent_user = get_or_create_guardian(
            user, lead.parent_name, lead.parent_phone, lead.branch,
            login_id=data.get("parent_login_id", ""),
            password=data.get("parent_password", ""),
            relation=data.get("parent_relation", ""))
        lead.status = LeadStatus.CONVERTED
        lead.converted_user = user
        lead.is_hidden = True  # 등록 전환 완료 시 상담 목록에서 자동 숨김
        lead.save()
        # 원비를 등록할 때 정해 둘 수 있다. 비워 두면 자동(기준표에서 계산)이다.
        _tm = data.get("tuition_mode")
        if _tm in ("MANUAL", "UNDECIDED"):
            from ..models import StudentTuition
            _amt = str(data.get("tuition_amount") or "").replace(",", "").strip()
            StudentTuition.objects.update_or_create(
                student=user,
                defaults={"mode": _tm, "updated_by": (request.user if request else None),
                          "manual_amount": (int(_amt) if _amt.isdigit() else None)})
        # 누가 언제 누구를 등록했는지. 상세는 남기지 않는다 — 뒤에 고친 것은 각자의 이력에 남는다
        StudentRegisterLog.objects.create(
            student=user, actor=(request.user if request else None),
            source=source, branch=lead.branch)
    result = LeadSerializer(lead).data
    if parent_user is not None:
        result["parent_account"] = {"username": parent_user.username,
                                    "is_new": parent_user.last_login is None}
    # 키오스크 등하원 안내 음성. 트랜잭션이 끝난 뒤에 만든다 — 외부 통신이라 느리거나
    # 실패할 수 있는데, 그것 때문에 등록이 통째로 되돌려지면 안 된다.
    from ..services_voice import build_student_voice
    build_student_voice(user)
    return result, None


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
        result, err = _create_student_from_lead(request, lead, data)
        if err:
            return self.error(err)
        return self.success(result)


class StudentRegisterAdminAPI(APIView):
    @validate_serializer(StudentRegisterSerializer)
    @admin_role_required
    def post(self, request):
        """상담 없이 학생 직접 등록. 입력값으로 숨김 리드를 만들고 동일 전환 로직 재사용."""
        data = request.data
        branch = Branch.objects.filter(id=data["branch_id"]).first()
        if not branch:
            return self.error("지점이 없습니다.")
        if not can_manage_branch(request.user, branch.id):
            return self.error("이 지점에 학생을 등록할 권한이 없습니다.")
        student_name = (data.get("student_name") or "").strip()
        if not student_name:
            return self.error("학생 성명을 입력하세요.")
        if User.objects.filter(username=data["login_id"].lower()).exists():
            return self.error("Login ID already exists")
        lead = Lead.objects.create(
            branch=branch, student_name=student_name,
            parent_name=(data.get("parent_name") or "").strip(),
            parent_phone=(data.get("parent_phone") or "").strip(),
            school_type=data.get("school_type") or "",
            school_name=data.get("school_name") or "",
            grade=data.get("grade") or "",
            status=LeadStatus.NEW, is_hidden=True)
        result, err = _create_student_from_lead(request, lead, data, source="DIRECT")
        if err:
            lead.delete()
            return self.error(err)
        return self.success(result)


class QuickRegisterAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """상담 없이 최소 정보로 '등록 대기' 리드 생성 + 부모 작성 링크 발급.
        직원 입력값(성별·생일·관계·알림)은 enroll_data 프리필로 저장 → 부모/전환 시 자동 채움."""
        data = request.data
        branch = Branch.objects.filter(id=data.get("branch_id")).first()
        if not branch:
            return self.error("지점이 없습니다.")
        if not can_manage_branch(request.user, branch.id):
            return self.error("이 지점에 등록할 권한이 없습니다.")
        sn = (data.get("student_name") or "").strip()
        pp = (data.get("parent_phone") or "").strip()
        if not sn:
            return self.error("학생 성명을 입력하세요.")
        if not data.get("gender"):
            return self.error("성별을 선택하세요.")
        if not data.get("birth_date"):
            return self.error("생년월일을 입력하세요.")
        if not pp:
            return self.error("보호자 연락처를 입력하세요.")
        if not data.get("parent_relation"):
            return self.error("보호자 관계를 선택하세요.")
        lead = Lead.objects.create(
            branch=branch, student_name=sn,
            parent_name=(data.get("parent_name") or "").strip(), parent_phone=pp,
            school_type=data.get("school_type") or "", school_name=data.get("school_name") or "",
            grade=data.get("grade") or "", status=LeadStatus.NEW)
        seed = {
            "student_name": sn, "gender": data.get("gender") or "",
            "birth_date": data.get("birth_date") or "",
            "parent_name": (data.get("parent_name") or "").strip(), "parent_phone": pp,
            "parent_relation": data.get("parent_relation") or "", "notify_optin": bool(data.get("notify_optin")),
            "school_type": data.get("school_type") or "", "school_name": data.get("school_name") or "",
            "grade": data.get("grade") or "",
        }
        lead.enroll_data = _json.dumps(seed, ensure_ascii=False)
        lead.enroll_token = _rand_str(24)
        lead.enroll_token_expires = now() + timedelta(days=7)
        lead.enroll_status = "SENT"
        lead.save()
        result = LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data
        url = request.build_absolute_uri("/portal/?enroll=" + lead.enroll_token)
        bn = branch.name
        result["message"] = _fill_vars(_fixed_body(branch.id, "enroll_link"), {
            "지점명": bn, "학원명": bn, "학생명": sn,
            "학부모명": (data.get("parent_name") or "학부모"), "링크": url})
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
            staff_c = AcademyProfile.objects.filter(branch_id=b.id, role__in=STAFF_ROLES).count()
            student_c = AcademyProfile.objects.filter(branch_id=b.id, role=AcademyRole.STUDENT).count()
            tt_c = StudentTimetable.objects.filter(branch_id=b.id).count()
            lead_c = Lead.objects.filter(branch_id=b.id).count()
            class_c = CourseClass.objects.filter(branch_id=b.id).count()
            out.append({"id": b.id, "code": b.code, "name": b.name, "is_active": b.is_active,
                        "staff_count": staff_c, "student_count": student_c,
                        "timetable_count": tt_c, "lead_count": lead_c, "class_count": class_c,
                        "deletable": (staff_c == 0 and student_c == 0 and tt_c == 0
                                      and lead_c == 0 and class_c == 0)})
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

    @admin_role_required
    def delete(self, request):
        if not self._require_hq(request):
            return self.error("본부 관리자만 삭제할 수 있습니다.")
        b = Branch.objects.filter(id=request.GET.get("id")).first()
        if not b:
            return self.error("지점이 없습니다.")
        blockers = []
        staff_c = AcademyProfile.objects.filter(branch_id=b.id, role__in=STAFF_ROLES).count()
        student_c = AcademyProfile.objects.filter(branch_id=b.id, role=AcademyRole.STUDENT).count()
        tt_c = StudentTimetable.objects.filter(branch_id=b.id).count()
        lead_c = Lead.objects.filter(branch_id=b.id).count()
        class_c = CourseClass.objects.filter(branch_id=b.id).count()
        if staff_c:
            blockers.append("직원 %d명" % staff_c)
        if student_c:
            blockers.append("학생 %d명" % student_c)
        if tt_c:
            blockers.append("개별시간표 %d건" % tt_c)
        if lead_c:
            blockers.append("상담 %d건" % lead_c)
        if class_c:
            blockers.append("반/특강 %d건" % class_c)
        if blockers:
            return self.error("연결된 정보가 있어 삭제할 수 없습니다 (" + ", ".join(blockers)
                              + "). 비활성(폐점)으로 처리하세요.")
        b.delete()
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
        out = []
        for o in qs:
            d = OptionItemSerializer(o).data
            d["usage"] = _option_usage(o.category, o.value)
            out.append(d)
        return self.success(out)

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
            order=data.get("order") or 0, allow_custom=bool(data.get("allow_custom")),
            color=data.get("color") or "")
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
        if "color" in data:
            opt.color = data["color"] or ""
        opt.save()
        return self.success(OptionItemSerializer(opt).data)

    @admin_role_required
    def delete(self, request):
        if not self._require_hq(request):
            return self.error("본부/인사 관리자만 목록을 관리할 수 있습니다.")
        opt = OptionItem.objects.filter(id=request.GET.get("id")).first()
        if not opt:
            return self.error("Option does not exist")
        used = _option_usage(opt.category, opt.value)
        if used:
            return self.error("연결된 정보가 %d건 있어 삭제할 수 없습니다. 비활성으로 처리하세요." % used)
        opt.delete()
        return self.success("Deleted")


def _option_usage(category, value):
    """선택 목록 값이 실제로 사용된 건수(삭제 가드·표시용)."""
    n = 0
    if category == "school_type":
        n += StudentProfile.objects.filter(school_type=value).count()
        n += Lead.objects.filter(school_type=value).count()
    elif category == "program":
        n += StudentProfile.objects.filter(program=value).count()
        n += StudentTimetable.objects.filter(program=value).count()
    elif category == "program_language":
        n += StudentProfile.objects.filter(program_language=value).count()
    elif category == "counseling_purpose":
        n += Lead.objects.filter(purpose=value).count()
    return n


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


# 학생 정보에서 '있어야 정상'인 항목 — 하나라도 비면 목록에서 표시해 알린다
_REQUIRED_INFO = [
    ("birth_date", "생년월일"), ("gender", "성별"), ("address", "주소"),
    ("parent_phone", "보호자 연락처"), ("parent_name", "보호자 이름"),
    ("school_name", "학교"), ("consent_privacy", "개인정보 동의"),
]


def _missing_info(sp):
    if not sp:
        return [lb for _, lb in _REQUIRED_INFO]
    out = []
    for f, lb in _REQUIRED_INFO:
        v = getattr(sp, f, None)
        if f == "consent_privacy":
            # 종이로 받아 둔 것도 동의다. 형태가 다를 뿐 없는 게 아니다.
            if not v and not getattr(sp, "consent_paper", False):
                out.append(lb)
        elif v in (None, ""):
            out.append(lb)
    return out


def _student_list_extra(rows, want):
    """학생 목록의 선택 열 값. 필요한 것만 통째로 한 번씩 읽어 학생별로 묶는다."""
    ids = [r["id"] for r in rows]
    if not ids:
        return
    today = (now() + timedelta(hours=9)).date()
    ex = {i: {} for i in ids}

    for sp in StudentProfile.objects.filter(user_id__in=ids).exclude(pending_status=""):
        ex[sp.user_id]["pending"] = {"status": sp.pending_status, "date": str(sp.pending_date),
                                     "reason": sp.pending_reason}

    if "missing" in want or "verify" in want:
        for sp in StudentProfile.objects.filter(user_id__in=ids):
            if "missing" in want:
                ex[sp.user_id]["missing"] = _missing_info(sp)
            if "verify" in want:
                ex[sp.user_id]["enrollment_date"] = str(sp.enrollment_date) if sp.enrollment_date else ""

    if "weekdays" in want or "instructor" in want or "times" in want:
        for slot in StudentTimetable.objects.filter(student_id__in=ids).exclude(
                status="ENDED").select_related("instructor"):
            if not _slot_active_on(slot, today):
                continue
            e = ex[slot.student_id]
            e.setdefault("weekdays", {}).setdefault(slot.weekday, []).append(str(slot.start_time)[:5])
            e.setdefault("durations", []).append(slot.duration_minutes)
            if slot.instructor_id:
                names = e.setdefault("instructors", [])
                nm = _name_of(slot.instructor)
                if nm not in names:
                    names.append(nm)

    if "month" in want:
        m0 = today.replace(day=1)
        starts = {}
        for o in LessonOccurrence.objects.filter(
                student_id__in=ids, date__gte=m0, date__lte=today).only(
                "student_id", "date", "start_time", "status"):
            if o.status == OccurrenceStatus.ABSENT:
                e = ex[o.student_id]
                e["m_absent"] = e.get("m_absent", 0) + 1
            key = (o.student_id, o.date)
            t = _t2m(o.start_time)
            if key not in starts or t < starts[key]:
                starts[key] = t
        for a in DailyAttendance.objects.filter(
                student_id__in=ids, date__gte=m0, date__lte=today,
                check_in_at__isnull=False).only("student_id", "date", "check_in_at"):
            ref = starts.get((a.student_id, a.date))
            if ref is not None and _t2m(_hm_kst(a.check_in_at)) - ref > 5:
                e = ex[a.student_id]
                e["m_late"] = e.get("m_late", 0) + 1

    if "mk" in want:
        made = set(LessonOccurrence.objects.filter(
            is_makeup=True, makeup_for__isnull=False).values_list("makeup_for_id", flat=True))
        for o in LessonOccurrence.objects.filter(
                student_id__in=ids, status=OccurrenceStatus.ABSENT,
                is_makeup=False, no_makeup=False).only("id", "student_id"):
            if o.id not in made:
                e = ex[o.student_id]
                e["mk_pending"] = e.get("mk_pending", 0) + 1

    if "last_attend" in want:
        for a in DailyAttendance.objects.filter(
                student_id__in=ids, check_in_at__isnull=False).order_by("student_id", "-date").only(
                "student_id", "date"):
            e = ex[a.student_id]
            if "last_attend" not in e:
                e["last_attend"] = str(a.date)
                e["last_attend_days"] = (today - a.date).days

    if "verify" in want:
        from ..models import ProfileVerification
        for v in ProfileVerification.objects.filter(
                student_id__in=ids,
                status__in=[ProfileVerification.SENT, ProfileVerification.SUBMITTED]):
            ex[v.student_id]["verify"] = {
                "status": v.status,
                "days": (now().date() - v.create_time.date()).days}

    if "sibling" in want:
        from ..models import GuardianStudent
        parents = {}
        for pid, sid in GuardianStudent.objects.filter(student_id__in=ids).values_list("parent_id", "student_id"):
            parents.setdefault(pid, []).append(sid)
        sib = {}
        if parents:
            total = {}
            for pid, sid in GuardianStudent.objects.filter(
                    parent_id__in=parents.keys()).values_list("parent_id", "student_id"):
                total.setdefault(pid, set()).add(sid)
            for pid, sids in parents.items():
                n = len(total.get(pid, set()))
                for sid in sids:
                    sib[sid] = max(sib.get(sid, 0), n - 1)
        for i in ids:
            if sib.get(i):
                ex[i]["siblings"] = sib[i]

    for r in rows:
        r["extra"] = ex.get(r["id"], {})


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
        # 지금 적용중인 시간표만 집계('적용 시작일' 분할로 남은 지난 이력 행 제외)
        today_kst = (now() + timedelta(hours=9)).date()
        active_tt = StudentTimetable.objects.exclude(status="ENDED").filter(
            Q(active_until__isnull=True) | Q(active_until__gte=today_kst)).filter(
            Q(active_from__isnull=True) | Q(active_from__lte=today_kst))
        counts = dict(active_tt.values("student_id").annotate(c=Count("id"))
                      .values_list("student_id", "c"))
        # 보호자 수 집계
        gcounts = dict(GuardianStudent.objects.values("student_id")
                       .annotate(c=Count("id")).values_list("student_id", "c"))
        # 수강 중(미종료) 과목명 집계
        subj_map = {}
        for sid, subj in active_tt.values_list("student_id", "subject"):
            if subj:
                subj_map.setdefault(sid, [])
                if subj not in subj_map[sid]:
                    subj_map[sid].append(subj)
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
                        "enroll_no": (sp.enroll_no if sp else ""),
                        "birth_date": (str(sp.birth_date) if (sp and sp.birth_date) else ""),
                        "branch": (p.branch.name if p.branch_id else ""),
                        "branch_id": p.branch_id,
                        "school_type": (sp.school_type if sp else ""),
                        "school_name": (sp.school_name if sp else ""),
                        "grade": (sp.grade if sp else ""),
                        "parent_name": (sp.parent_name if sp else ""),
                        "parent_phone": (sp.parent_phone if sp else ""),
                        "parent_relation": (sp.parent_relation if sp else ""),
                        "student_phone": (sp.student_phone if sp else ""),
                        "enrollment_status": (sp.enrollment_status if sp else EnrollmentStatus.ENROLLED),
                        "weekly_sessions": (sp.weekly_sessions if sp else None),
                        "guardian_count": gcounts.get(u.id, 0),
                        "subjects": subj_map.get(u.id, []),
                        "slot_count": counts.get(u.id, 0),
                        "status_history": []})
        # 켠 열에 필요한 값만 계산해 붙인다. 전부 계산하면 목록이 무거워지므로
        # 화면이 cols= 로 알려준 것만 만든다.
        want = {c for c in (request.GET.get("cols") or "").split(",") if c}
        if want:
            _student_list_extra(out, want)

        # 휴원/퇴원 학생은 상태 변경 이력을 함께 내려 목록에서 호버로 보기
        non_enrolled = [r["id"] for r in out
                        if r["enrollment_status"] in (EnrollmentStatus.ON_LEAVE, EnrollmentStatus.WITHDRAWN)]
        if non_enrolled:
            hist = {}
            for c in StudentStatusChange.objects.select_related("actor").filter(
                    student_id__in=non_enrolled).order_by("-create_time"):
                hist.setdefault(c.student_id, []).append({
                    "from": c.from_status, "to": c.to_status, "reason": c.reason,
                    "date": (str(c.effective_date) if c.effective_date else str(c.create_time)[:10]),
                    "actor": (_name_of(c.actor) if c.actor_id else "")})
            for r in out:
                if r["id"] in hist:
                    r["status_history"] = hist[r["id"]]
        return self.success(out)


def _bulk_parse_date(s):
    s = (s or "").strip().replace("/", "-").replace(".", "-")
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _resolve_opt_value(category, text):
    """선택목록 라벨 또는 값(코드)을 value(코드)로 해석. 매칭 없으면 ''.
    정확히 일치하는 항목이 없으면 라벨 포함관계(예: '프로그래밍'↔'프로그래밍언어')로 한 번 더 시도."""
    t = (text or "").strip()
    if not t:
        return ""
    items = list(OptionItem.objects.filter(category=category))
    for o in items:
        if t == o.value or t == o.label:
            return o.value
    for o in items:
        if t in o.label or o.label in t:
            return o.value
    return ""


def _opt_label(category, value):
    """선택목록 value(코드) → 표시 라벨. 없으면 value 그대로."""
    if not value:
        return ""
    o = OptionItem.objects.filter(category=category, value=value).first()
    return o.label if o else value


_LANG_ABBR = {"Python": "Py", "Java": "Ja"}  # 시간표 표시는 짧게(C/C++/C#는 이미 짧음)


def _parse_program_token(tok):
    """'프로그래밍(파이썬)'·'웹'·'개인맞춤(로봇)' → {value, language, custom, subject}.
    괄호 또는 콜론(:)으로 세부(언어/맞춤 내용)를 지정. LANG 과정은 세부=언어."""
    tok = (tok or "").strip()
    if not tok:
        return None
    label, detail = tok, ""
    if tok.endswith(")") and "(" in tok:
        label = tok[:tok.index("(")].strip()
        detail = tok[tok.index("(") + 1:-1].strip()
    elif ":" in tok:
        label, detail = [s.strip() for s in tok.split(":", 1)]
    val = _resolve_opt_value("program", label)
    out = {"value": val, "language": "", "custom": "", "subject": ""}
    if not val:  # 등록되지 않은 과정명 → 맞춤 과정(자유 입력)
        out["custom"] = tok
        out["subject"] = tok
        return out
    prog_label = _opt_label("program", val)
    if val == "LANG":
        lang_val = _resolve_opt_value("program_language", detail) if detail else ""
        out["language"] = lang_val or detail
        lang_label = _opt_label("program_language", lang_val) or detail or prog_label
        out["subject"] = _LANG_ABBR.get(lang_label, lang_label)
    elif detail:
        out["custom"] = detail
        out["subject"] = detail
    else:
        out["subject"] = prog_label
    return out


BULK_TT_SLOTS = 4  # 일괄등록 양식에 회차별 컬럼(요일/시각/과정/수업시간/선생님)을 몇 회차까지 둘지


def _bulk_build_timetable_from_cols(r):
    """'N회차 요일/시각/과정/수업시간/선생님' 컬럼(최대 BULK_TT_SLOTS)에서 시간표 항목을 구성.
    항목 형식은 {weekday,start_time,program,subject,duration,instructor_name}. 비어있는 회차는 건너뜀."""
    from datetime import time as _t
    items, warns = [], []
    for i in range(1, BULK_TT_SLOTS + 1):
        wd_s = (r.get("tt%d_weekday" % i) or "").strip()
        tm_s = (r.get("tt%d_time" % i) or "").strip()
        prog_s = (r.get("tt%d_program" % i) or "").strip()
        dur_s = (r.get("tt%d_duration" % i) or "").strip()
        instr_s = (r.get("tt%d_instructor" % i) or "").strip()
        if not (wd_s or tm_s or prog_s or dur_s or instr_s):
            continue  # 이 회차는 전부 비어있음 → 사용 안 함
        if not wd_s or not tm_s:
            warns.append("%d회차: 요일·시각을 모두 입력하세요." % i)
            continue
        wd = _WD.index(wd_s) if wd_s in _WD else -1
        if wd < 0:
            warns.append("%d회차 요일 인식 불가: '%s'" % (i, wd_s))
            continue
        try:
            hh, mm = tm_s.split(":")
            _t(int(hh), int(mm))
            tm = "%02d:%02d" % (int(hh), int(mm))
        except (ValueError, AttributeError):
            warns.append("%d회차 시각 형식 오류: '%s' (HH:MM)" % (i, tm_s))
            continue
        pt = _parse_program_token(prog_s) or {"value": "", "language": "", "subject": prog_s or "수업"}
        duration = None
        if dur_s:
            m = re.match(r"(\d+)", dur_s)
            if m:
                duration = int(m.group(1))
            else:
                warns.append("%d회차 수업시간 형식 오류(숫자만): '%s'" % (i, dur_s))
        items.append({"weekday": wd, "start_time": tm, "program": pt.get("value", ""),
                      "language": pt.get("language", ""), "subject": pt.get("subject") or "수업",
                      "duration": duration, "instructor_name": instr_s})
    return items, warns


def _bulk_resolve_row(actor, row, branches, seen_ids):
    """한 행을 검증·해석. 생성하지 않고 결과/오류/경고/시간표 미리보기 반환."""
    r = {k: ("" if v is None else str(v).strip()) for k, v in (row or {}).items()}
    res = {"student_name": r.get("student_name", ""), "ok": False, "error": "",
           "warnings": [], "login_id": "", "timetable_preview": []}
    name = r.get("student_name", "")
    if not name:
        res["error"] = "학생 이름이 비어 있습니다."
        return res, None
    branch = branches.get(r.get("branch", ""))
    if not branch:
        res["error"] = "지점을 찾을 수 없습니다: %s" % r.get("branch", "")
        return res, None
    if not can_manage_branch(actor, branch.id):
        res["error"] = "이 지점에 권한이 없습니다: %s" % branch.name
        return res, None
    bd = _bulk_parse_date(r.get("birth_date"))
    login_id = (r.get("login_id") or "").lower()
    if not login_id:
        if not bd:
            res["error"] = "아이디가 없고 생년월일도 없어 아이디 자동 생성 불가"
            return res, None
        login_id = name.replace(" ", "") + "%02d%02d" % (bd.month, bd.day)
    res["login_id"] = login_id
    if login_id in seen_ids:
        res["error"] = "파일 안에 중복된 아이디: %s" % login_id
        return res, None
    if User.objects.filter(username=login_id).exists():
        res["error"] = "이미 존재하는 아이디: %s" % login_id
        return res, None
    pw = r.get("password") or (r.get("parent_phone", "").replace("-", "")) or "123456"
    if len(pw) < 6:
        pw = (pw + "000000")[:6]
    ws = r.get("weekly_sessions", "")
    try:
        ws = int(float(ws)) if ws else None
    except ValueError:
        ws = None
    tt_items, tt_warns = _bulk_build_timetable_from_cols(r)
    # 시간표에 담당 선생님 이름이 있으면 해당 지점 교직원에서 매칭(못 찾으면 경고, 미배정으로 진행)
    staff_by_name = {}
    for p in AcademyProfile.objects.filter(role__in=STAFF_ROLES, branch=branch).select_related("user", "user__userprofile"):
        staff_by_name[_name_of(p.user)] = p.user_id
        staff_by_name[p.user.username] = p.user_id
    for it in tt_items:
        nm = it.get("instructor_name")
        if nm:
            iid = staff_by_name.get(nm)
            if iid:
                it["instructor_id"] = iid
            else:
                it["instructor_id"] = None
                tt_warns.append("담당 선생님을 찾을 수 없음(미배정으로 진행): '%s'" % nm)
        else:
            it["instructor_id"] = None
    # 같은 학생 안에서 회차끼리 시간이 겹치는지 검사(주 2회 이상 + 수업시간 조합에서 자주 발생).
    # 수업시간을 안 적었으면 학교급·주횟수 기본값으로 계산해 같은 기준으로 본다.
    _def_dur = lesson_duration(r.get("school_type", ""), len(tt_items) or 1)
    for i in range(len(tt_items)):
        for j in range(i + 1, len(tt_items)):
            a, b = tt_items[i], tt_items[j]
            if a["weekday"] != b["weekday"]:
                continue
            if _time_overlaps(a["start_time"], a.get("duration") or _def_dur,
                              b["start_time"], b.get("duration") or _def_dur):
                res["error"] = ("%d회차와 %d회차 수업 시간이 겹칩니다: %s %s(%d분) ↔ %s(%d분)"
                                % (i + 1, j + 1, _WD[a["weekday"]],
                                   a["start_time"], a.get("duration") or _def_dur,
                                   b["start_time"], b.get("duration") or _def_dur))
                res["warnings"] = tt_warns
                return res
    res["warnings"] = tt_warns
    res["timetable_preview"] = [
        "%s %s %s%s%s" % (_WD[it["weekday"]], it["start_time"], it["subject"],
                          ("(%d분)" % it["duration"]) if it.get("duration") else "",
                          ("·%s" % it["instructor_name"]) if it.get("instructor_name") else "")
        for it in tt_items]
    if ws is None and tt_items:
        ws = len(tt_items)
    progs = []
    for tok in r.get("programs", "").replace("，", ",").split(","):
        pt = _parse_program_token(tok)
        if pt:
            progs.append({"value": pt["value"], "language": pt["language"], "custom": pt["custom"]})
    resolved = {
        "name": name, "branch": branch, "login_id": login_id, "password": pw,
        "birth_date": bd, "gender": {"남": "M", "여": "F", "M": "M", "F": "F"}.get(r.get("gender", ""), ""),
        "zipcode": r.get("zipcode", ""), "address": r.get("address", ""),
        "address_detail": r.get("address_detail", ""), "student_phone": r.get("student_phone", ""),
        "parent_name": r.get("parent_name", ""), "parent_phone": r.get("parent_phone", ""),
        "parent_relation": r.get("parent_relation", ""),
        "notify_optin": (r.get("notify_optin", "") or "").strip() == "수신",
        "guardian2_phone": r.get("guardian2_phone", ""), "guardian2_relation": r.get("guardian2_relation", ""),
        "school_type": _resolve_opt_value("school_type", r.get("school_type")),
        "school_name": r.get("school_name", ""), "grade": r.get("grade", ""),
        "programs": progs, "weekly_sessions": ws,
        "enrollment_date": _bulk_parse_date(r.get("enrollment_date")) or now().date(),
        "lesson_start_date": _bulk_parse_date(r.get("lesson_start_date")),
        "timetable": tt_items,
        "memo": r.get("memo", ""),
        "legacy_url": (r.get("legacy_url", "") or "").strip()[:500],
        "consent_paper": (r.get("consent_paper", "") or "").strip() in ("종이로 받음", "종이", "Y", "y", "1", "O", "o"),
    }
    res["ok"] = True
    return res, resolved


class BulkRegisterAPI(APIView):
    @admin_role_required
    def post(self, request):
        """엑셀 일괄등록. {rows:[...], commit:false|true}.
        commit=false: 검증만(생성 없음). commit=true: 검증 통과 행만 생성(계정+등록정보+시간표)."""
        rows = request.data.get("rows") or []
        commit = bool(request.data.get("commit"))
        if not isinstance(rows, list):
            return self.error("rows 형식 오류")
        if len(rows) > 500:
            return self.error("한 번에 최대 500행까지 등록할 수 있습니다.")
        branches = {}
        for b in Branch.objects.all():
            branches[b.name] = b
            if b.code:
                branches[b.code] = b
        # 한 번의 일괄 등록을 하나로 묶는 값. 사용 이력에서 이름을 한 줄로 모아 보여 준다.
        self._actor = request.user
        self._batch = str(now().timestamp())[:14] + "-" + str(request.user.id)
        results, seen_ids = [], set()
        for i, row in enumerate(rows):
            res, resolved = _bulk_resolve_row(request.user, row, branches, seen_ids)
            res["row"] = i + 1
            if res["ok"] and res.get("login_id"):
                seen_ids.add(res["login_id"])
            if res["ok"] and commit:
                try:
                    self._create(resolved)
                    res["created"] = True
                except Exception as e:
                    res["ok"] = False
                    res["error"] = "생성 실패: %s" % e
            results.append(res)
        return self.success({"total": len(results), "commit": commit,
                             "ok": sum(1 for x in results if x["ok"]),
                             "fail": sum(1 for x in results if not x["ok"]),
                             "results": results})

    def _create(self, d):
        with transaction.atomic():
            user = User.objects.create(username=d["login_id"], is_disabled=False)
            user.set_password(d["password"])
            user.save()
            UserProfile.objects.create(user=user, real_name=d["name"])
            apply_role(user, AcademyRole.STUDENT, d["branch"])
            StudentRegisterLog.objects.create(
                student=user, actor=self._actor, source="BULK",
                branch=d["branch"], batch=self._batch)
            StudentProfile.objects.create(
                user=user, enroll_no=gen_enroll_no(d["branch"]),
                birth_date=d["birth_date"], gender=d["gender"],
                zipcode=d["zipcode"], address=d["address"], address_detail=d["address_detail"],
                student_phone=d["student_phone"],
                parent_name=d["parent_name"], parent_phone=d["parent_phone"],
                parent_relation=d.get("parent_relation", "") or "", notify_optin=bool(d.get("notify_optin")),
                guardian2_phone=d.get("guardian2_phone", "") or "", guardian2_relation=d.get("guardian2_relation", "") or "",
                school_type=d["school_type"], school_name=d["school_name"], grade=d["grade"],
                enrollment_date=d["enrollment_date"], enrollment_status=EnrollmentStatus.ENROLLED,
                program=(d["programs"][0]["value"] if d["programs"] else ""),
                programs=_json.dumps(d["programs"], ensure_ascii=False),
                weekly_sessions=d["weekly_sessions"], lesson_start_date=d["lesson_start_date"],
                legacy_url=d.get("legacy_url", "") or "",
                consent_paper=bool(d.get("consent_paper")),
                memo=d.get("memo", "") or "")
            default_dur = lesson_duration(d["school_type"], d["weekly_sessions"])
            for it in d["timetable"]:
                StudentTimetable.objects.create(
                    student=user, branch=d["branch"], class_type=LessonType.PRIVATE,
                    weekday=it["weekday"], start_time=it["start_time"],
                    duration_minutes=it.get("duration") or default_dur,
                    instructor_id=it.get("instructor_id"),
                    program=it["program"], subject=it["subject"], frequency="WEEKLY",
                    active_from=d["lesson_start_date"])
            # 학부모(보호자) 계정 생성/연결 — 전환(개별 등록) 흐름과 동일하게 처리(11 §9)
            get_or_create_guardian(user, d["parent_name"], d["parent_phone"], d["branch"],
                                   relation=d.get("parent_relation", ""))
        # 키오스크 등하원 안내 음성(개별 등록과 동일). 트랜잭션 밖에서 만든다 —
        # 외부 통신이라 느리거나 실패할 수 있는데, 그것 때문에 등록이 되돌려지면 안 된다.
        from ..services_voice import build_student_voice
        build_student_voice(user)


class BulkExportAPI(APIView):
    @admin_role_required
    def get(self, request):
        """기존 학생을 일괄등록 양식과 동일 컬럼으로 내보내기(JSON). 프론트가 xlsx로 변환."""
        all_branch, branch_id, role = staff_scope(request.user)
        qs = AcademyProfile.objects.select_related("user", "user__student_profile", "branch").filter(
            role=AcademyRole.STUDENT)
        if not all_branch:
            view = viewable_branch_ids(request.user)
            if not view:
                return self.error("No branch scope assigned")
            qs = qs.filter(branch_id__in=view)
        sl_label = {o.value: o.label for o in OptionItem.objects.filter(category="school_type")}
        pg_label = {o.value: o.label for o in OptionItem.objects.filter(category="program")}
        lang_label = {o.value: o.label for o in OptionItem.objects.filter(category="program_language")}

        def _prog_token(v, lang="", custom=""):
            """StudentProfile.programs / StudentTimetable 데이터를 일괄등록 표기('과정명(언어)')로 재구성."""
            if v == "LANG":
                lb = lang_label.get(lang, lang)
                return "%s(%s)" % (pg_label.get(v, v), lb) if lb else pg_label.get(v, v)
            if v:
                return pg_label.get(v, v)
            return custom or ""

        WD = _WD
        tt_map = {}
        # 지금 적용중인 것만(시간표 변경·휴원으로 기간이 끝난 지난 행이 섞이면 안 됨)
        _today_x = (now() + timedelta(hours=9)).date()
        for s in StudentTimetable.objects.exclude(status="ENDED").filter(
                Q(active_until__isnull=True) | Q(active_until__gte=_today_x)).filter(
                Q(active_from__isnull=True) | Q(active_from__lte=_today_x)).values(
                "student_id", "weekday", "start_time", "subject", "program"):
            token = _prog_token(s["program"], lang=s["subject"]) if s["program"] == "LANG" else (
                _prog_token(s["program"]) or s["subject"] or "수업")
            tt_map.setdefault(s["student_id"], []).append(
                "%s %s %s" % (WD[s["weekday"]] if 0 <= s["weekday"] <= 6 else "?",
                              str(s["start_time"])[:5], token))
        out = []
        for p in qs.order_by("branch_id", "user__username"):
            u, sp = p.user, getattr(p.user, "student_profile", None)
            try:
                rn = u.userprofile.real_name or ""
            except Exception:
                rn = ""
            progs = []
            if sp and sp.programs:
                try:
                    for pr in _json.loads(sp.programs):
                        progs.append(_prog_token(pr.get("value"), lang=pr.get("language", ""), custom=pr.get("custom", "")))
                except (ValueError, TypeError):
                    pass
            out.append({
                "원번": (sp.enroll_no if sp else ""), "지점": (p.branch.name if p.branch_id else ""),
                "학생이름": rn, "생년월일": (str(sp.birth_date) if (sp and sp.birth_date) else ""),
                "성별": {"M": "남", "F": "여"}.get((sp.gender if sp else ""), ""),
                "학교구분": sl_label.get((sp.school_type if sp else ""), ""),
                "학교이름": (sp.school_name if sp else ""), "학년": (sp.grade if sp else ""),
                "보호자이름": (sp.parent_name if sp else ""), "보호자연락처": (sp.parent_phone if sp else ""),
                "보호자관계": (sp.parent_relation if sp else ""),
                "등하원알림": ("수신" if (sp and sp.notify_optin) else "미수신"),
                "기타보호자연락처": (sp.guardian2_phone if sp else ""), "기타보호자관계": (sp.guardian2_relation if sp else ""),
                "학생연락처": (sp.student_phone if sp else ""), "우편번호": (sp.zipcode if sp else ""),
                "주소": (sp.address if sp else ""), "상세주소": (sp.address_detail if sp else ""),
                "아이디": u.username, "비밀번호": "",
                "등록과정": ", ".join([x for x in progs if x]),
                "주횟수": (sp.weekly_sessions if (sp and sp.weekly_sessions) else ""),
                "시간표": ", ".join(tt_map.get(u.id, [])),
                "등록일": (str(sp.enrollment_date) if (sp and sp.enrollment_date) else ""),
                "수업시작일": (str(sp.lesson_start_date) if (sp and sp.lesson_start_date) else ""),
                "기타(요청사항)": (sp.memo if sp else ""),
            })
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


def sync_program_to_profile(student, program, language=""):
    """시간표에 넣은 과목을 등록 과정에도 담는다.

    둘을 따로 고치게 두었더니 과정을 바꿀 때마다 두 군데를 손봐야 했고, 등록 과정에
    없는 과목은 시간표에서 고를 수조차 없었다. 시간표가 실제로 듣는 수업이므로
    그쪽을 따라간다. 빼는 것은 하지 않는다 — 지난 과정도 등록 이력이기 때문."""
    if not program:
        return
    sp = getattr(student, "student_profile", None)
    if not sp:
        return
    try:
        progs = _json.loads(sp.programs) if sp.programs else []
    except (ValueError, TypeError):
        progs = []
    lang = language or ""
    # 언어를 모르는 채로 프로그래밍언어를 담으면 '프로그래밍언어(C)' 옆에 언어 없는
    # '프로그래밍언어' 가 따로 생긴다(38명에게 그렇게 쌓였다). 모르면 담지 않는다.
    if program == "LANG" and not lang:
        return
    for p in progs:
        if not isinstance(p, dict):
            continue
        if p.get("value") == program and (program != "LANG" or (p.get("language") or "") == lang):
            return                      # 이미 있다
    progs.append({"value": program, "language": lang, "custom": ""})
    sp.programs = _json.dumps(progs, ensure_ascii=False)
    if not sp.program:
        sp.program = program
        sp.program_language = lang
    sp.save(update_fields=["programs", "program", "program_language"])


class StudentTimetableAdminAPI(APIView):
    """학생별 개별 수업 시간표(12) 관리. 지점 스코프."""

    def _branch_ok(self, request, branch_id):
        return can_manage_branch(request.user, branch_id)

    @admin_role_required
    def get(self, request):
        """student_id 로 특정 학생의 시간표, 또는 branch/weekday 로 지점 전체 조회.
        상태별로 골라 본다. status=ACTIVE,PAUSED,ENDED (기본 ACTIVE 만).
        ENDED 는 두 갈래다 — 퇴원 처리로 끝난 것과 시간표를 지운 것(요일 변경 포함)."""
        qs = StudentTimetable.objects.select_related("student", "branch", "instructor")
        want = (request.GET.get("status") or "").split(",")
        want = [x for x in want if x in ("ACTIVE", "PAUSED", "ENDED")]
        if not want:
            want = ["ACTIVE"] if request.GET.get("show_ended") != "1" else ["ACTIVE", "PAUSED", "ENDED"]
        qs = qs.filter(status__in=want)
        # '사용중'은 오늘 적용중인 것만이어야 한다. 기간이 지난 줄은 상태가 ACTIVE 로 남아
        # 있어 함께 섞여 나왔다. 지난 것은 include_past=1 일 때만 더한다.
        _today_tt = (now() + timedelta(hours=9)).date()
        if request.GET.get("include_past") != "1":
            qs = qs.filter(Q(active_until__isnull=True) | Q(active_until__gte=_today_tt))
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
        # 동일 학생·요일·시각·과정 중복 방지(과정이 다르면 같은 시간대 허용 — 격주 번갈아 수강 등).
        # 유효기간이 겹칠 때만 중복이다. 예전에 쓰다 끊긴 시간표까지 보면 '원래 시간으로
        # 되돌리기'가 막힌다(바로 아래 _find_slot_conflict 는 이미 기간을 보고 있었음).
        # 새로 추가하는 수업은 '오늘부터'로 본다. active_from 을 비워두면 과거 무한대가 되어
        # 이미 끝난 지난 시간표와도 겹친다고 나온다.
        af_new = _to_date(data["active_from"]) if data.get("active_from") else (now() + timedelta(hours=9)).date()
        au_new = _to_date(data["active_until"]) if data.get("active_until") else None
        if au_new and au_new < af_new:
            return self.error("끝나는 날(%s)이 시작일(%s)보다 앞설 수 없습니다." % (au_new, af_new))
        dup = [x for x in StudentTimetable.objects.filter(
                   student=student, weekday=data["weekday"],
                   start_time=data["start_time"], program=prog).exclude(status="ENDED")
               if _period_overlaps(af_new, au_new, x.active_from, x.active_until)]
        if dup:
            return self.error("같은 요일·시각에 같은 과정 수업이 이미 있습니다.")
        dur_new = data.get("duration_minutes") or 60
        conf = _find_slot_conflict(student.id, data["weekday"], data["start_time"], dur_new, af_new, au_new)
        if conf:
            return self.error(_conflict_msg(_name_of(student), "%s요일" % _WD[conf.weekday],
                                            conf.start_time, conf.duration_minutes, "정규수업"))
        slot = StudentTimetable.objects.create(
            student=student, branch=branch,
            class_type=data.get("class_type") or LessonType.PRIVATE,
            weekday=data["weekday"], start_time=data["start_time"],
            duration_minutes=data.get("duration_minutes") or 60,
            instructor=instructor, program=prog,
            subject=data.get("subject") or resolve_program_label(prog),
            frequency=data.get("frequency") or "WEEKLY",
            room=data.get("room", "") or "",
            # 적용 시작일을 남겨야 나중에 같은 요일·시각으로 되돌릴 때 지난 행과 구분된다.
            # 비워 두면 과거 무한대가 되어 2022년 수업까지 만들어진다 — 오늘부터로 본다.
            active_from=af_new, active_until=au_new)
        sync_program_to_profile(student, prog, data.get("language") or "")
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
        if data.get("effective_date"):
            return self._put_from_date(request, slot, data, reason)
        before = {"weekday": slot.weekday, "start_time": str(slot.start_time)[:5],
                  "duration_minutes": slot.duration_minutes, "program": slot.program,
                  "frequency": slot.frequency, "instructor_id": slot.instructor_id}
        for f in ("weekday", "start_time", "duration_minutes", "subject", "room", "status", "frequency"):
            if f in data:
                setattr(slot, f, data[f])
        # 끝나는 날은 기간 그 자체라 이력을 나눌 것이 없다. 늘 이 자리에서 바로 고친다.
        # 직렬화기를 거치지 않고 들어오는 길도 있어(EditStudentTimetableSerializer 밖의 호출)
        # 글자로 올 수 있다. 날짜로 맞춘 뒤에 견준다.
        if "active_until" in data:
            au = data.get("active_until")
            if isinstance(au, str):
                au = _to_date(au) if au.strip() else None
            if au and slot.active_from and au < slot.active_from:
                return self.error("끝나는 날이 시작일(%s)보다 앞설 수 없습니다." % slot.active_from)
            slot.active_until = au
        if "program" in data:
            slot.program = data["program"] or ""
            # subject를 명시적으로 함께 보냈으면 그 값을 존중(언어 과정 등 자동 라벨로는 표현 못하는 값 보존).
            # 없을 때만 프로그램 라벨로 자동 채움.
            if not (data.get("subject") or "").strip():
                slot.subject = resolve_program_label(slot.program)
        if "instructor_id" in data:
            slot.instructor = User.objects.filter(id=data["instructor_id"]).first() if data["instructor_id"] else None
        conf = _find_slot_conflict(slot.student_id, slot.weekday, slot.start_time, slot.duration_minutes,
                                   slot.active_from, slot.active_until, exclude_id=slot.id)
        if conf:
            return self.error(_conflict_msg(_name_of(slot.student), "%s요일" % _WD[conf.weekday],
                                            conf.start_time, conf.duration_minutes, "정규수업"))
        slot.save()
        sync_program_to_profile(slot.student, slot.program, data.get("language") or "")
        # 전체수정("처음부터 잘못 입력") — 이력을 나누지 않으므로 이미 만들어져 있던 수업 인스턴스도
        # 전부 새 값에 맞춘다. 안 그러면 요일을 바꿨을 때 옛 요일 수업이 남은 채 새 요일 수업이 따로
        # 생겨 두 요일이 섞여 보인다. 실제 기록(등원·결석·비고·수업일지·연결된 보강)은 보존.
        _reconcile_slot_occurrences(
            slot, LessonOccurrence.objects.filter(source_timetable=slot, is_makeup=False))
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

    def _put_from_date(self, request, slot, data, reason):
        """"이 날짜부터 이후 전체" 적용: 기존 시간표는 그 전날까지만 유효하게 종료하고,
        새 값으로 새 시간표를 그 날짜부터 시작하도록 분기 생성. 이미 찍혀있던 그 날짜 이후
        수업 인스턴스(스냅샷) 중 개별 수정(이 날짜만 변경) 안 된 것·아직 등원 안 한 것만
        새 시간표에 맞게 정리(요일이 안 맞으면 삭제, 맞으면 값 갱신)."""
        eff = data["effective_date"]
        if isinstance(eff, str):
            try:
                eff = datetime.strptime(eff, "%Y-%m-%d").date()
            except ValueError:
                return self.error("적용 시작일 형식이 올바르지 않습니다.")
        old_weekday = slot.weekday
        # 새 값이 이 학생의 다른 시간표와 겹치면 막는다(옛 행은 eff 전날로 끝나므로 자기 자신과는 안 겹침)
        conf = _find_slot_conflict(
            slot.student_id, data.get("weekday", slot.weekday), data.get("start_time", slot.start_time),
            data.get("duration_minutes", slot.duration_minutes), eff, None, exclude_id=slot.id)
        if conf:
            return self.error(_conflict_msg(_name_of(slot.student), "%s요일" % _WD[conf.weekday],
                                            conf.start_time, conf.duration_minutes, "정규수업"))
        # 기존 시간표는 그 전날까지만 유효.
        # 같은 날 두 번 고치면 옛 줄의 시작일이 이미 eff 이라, 끝나는 날이 시작일보다
        # 앞서는 죽은 줄이 남는다. 하루도 안 쓴 줄이므로 새 줄이 그 자리를 잇게 지운다.
        until = eff - timedelta(days=1)
        # 같은 날 두 번 고치면 옛 줄의 시작일이 이미 eff 이라, 끝나는 날이 시작일보다
        # 앞서는 죽은 줄이 남는다. 하루도 안 쓴 줄이라 끝난 것으로 접어 둔다 —
        # 살려 두면 주 교육 회수에 끼어 원비가 부풀어 오른다(김준수 주2회→주3회 44만원).
        from ..models import TimetableStatus
        never_ran = bool(slot.active_from) and slot.active_from > until
        if never_ran:
            slot.active_until = slot.active_from
            slot.status = TimetableStatus.ENDED
            slot.save(update_fields=["active_until", "status"])
            TimetableChange.objects.create(
                student=slot.student, actor=request.user, action="DELETE", reason=reason,
                detail=("%s %s 접음 — 같은 날 다시 고쳐 하루도 쓰지 않은 줄"
                        % (_WD[slot.weekday], str(slot.start_time)[:5]))[:255])
        else:
            slot.active_until = until
            slot.save(update_fields=["active_until"])
        # 새 시간표: 넘어온 값으로 덮어쓰고, 안 넘어온 값은 기존 것 유지
        base = slot
        new_kwargs = dict(
            student=base.student, branch=base.branch, class_type=base.class_type,
            weekday=data.get("weekday", base.weekday), start_time=data.get("start_time", base.start_time),
            duration_minutes=data.get("duration_minutes", base.duration_minutes),
            frequency=data.get("frequency", base.frequency),
            room=data.get("room", base.room), status=base.status,
            active_from=eff, active_until=None)
        if "program" in data:
            new_kwargs["program"] = data.get("program") or ""
            new_kwargs["subject"] = (data.get("subject") or "").strip() or resolve_program_label(new_kwargs["program"]) or ""
        else:
            new_kwargs["program"] = base.program
            new_kwargs["subject"] = data.get("subject") or base.subject
        if "instructor_id" in data:
            new_kwargs["instructor"] = User.objects.filter(id=data["instructor_id"]).first() if data["instructor_id"] else None
        else:
            new_kwargs["instructor"] = base.instructor
        new_slot = StudentTimetable.objects.create(**new_kwargs)
        sync_program_to_profile(new_slot.student, new_slot.program, data.get("language") or "")

        # 적용일 이후 이미 찍혀 있던 스냅샷을 새 시간표에 맞게 정리(적용일 이전은 옛 시간표 그대로).
        _reconcile_slot_occurrences(
            new_slot, LessonOccurrence.objects.filter(source_timetable=slot, date__gte=eff, is_makeup=False))

        labels = {"weekday": "요일", "start_time": "시각", "duration_minutes": "수업길이",
                  "program": "과정", "frequency": "반복"}
        old_vals = {"weekday": _WD[old_weekday], "start_time": str(base.start_time)[:5],
                    "duration_minutes": base.duration_minutes, "program": resolve_program_label(base.program) or "미지정",
                    "frequency": {"WEEKLY": "매주", "BIWEEKLY": "격주"}.get(base.frequency, base.frequency)}
        new_vals = {"weekday": _WD[new_slot.weekday], "start_time": str(new_slot.start_time)[:5],
                    "duration_minutes": new_slot.duration_minutes, "program": resolve_program_label(new_slot.program) or "미지정",
                    "frequency": {"WEEKLY": "매주", "BIWEEKLY": "격주"}.get(new_slot.frequency, new_slot.frequency)}
        parts = [f"{labels[k]} {old_vals[k]} → {new_vals[k]}" for k in labels if old_vals[k] != new_vals[k]]
        TimetableChange.objects.create(
            student=base.student, actor=request.user, action="UPDATE", reason=reason,
            detail=("%s부터 시간표 변경: %s" % (str(eff), "; ".join(parts) if parts else "수정"))[:255])
        new_slot = StudentTimetable.objects.select_related("student", "branch", "instructor").get(pk=new_slot.pk)
        return self.success(StudentTimetableSerializer(new_slot).data)

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
        # 앞으로 예정돼 있던 수업도 같이 정리한다. 안 하면 시간표는 지웠는데 수업 인스턴스만 남아
        # '오늘 운영'에 유령 수업으로 계속 뜬다(패턴이 지워지면 소속만 비고 인스턴스는 그대로라서).
        # 지난 수업과 실제 기록(등원·결석·비고·수업일지·연결된 보강)이 있는 수업은 이력이므로 보존.
        today = (now() + timedelta(hours=9)).date()
        upcoming = list(LessonOccurrence.objects.filter(
            source_timetable=slot, date__gte=today, is_makeup=False))
        rec_ids = _occ_record_ids(upcoming)
        removed = 0
        for occ in upcoming:
            if occ.id in rec_ids or occ.time_change_reason:
                continue
            occ.delete()
            removed += 1
        TimetableChange.objects.create(
            student=slot.student, actor=request.user, action="DELETE", reason=reason,
            detail=(f"{_WD[slot.weekday]} {str(slot.start_time)[:5]} {slot.subject or ''} 삭제"
                    + (f" (예정 수업 {removed}건 정리)" if removed else "")).strip())
        slot.delete()
        return self.success("Deleted")


MANAGER_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN, AcademyRole.REGIONAL_MANAGER,
                 AcademyRole.BRANCH_MANAGER, AcademyRole.VICE_PRINCIPAL}


def _is_manager(user):
    _, _, role = staff_scope(user)
    return role in MANAGER_ROLES or user.is_super_admin()


DIRECTOR_UP_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.REGIONAL_MANAGER, AcademyRole.BRANCH_MANAGER}


def _is_director_up(user):
    """원장(BRANCH_MANAGER) 이상(지부장·본부관리자)만 — 출결 키오스크 기기 등록 관리용.
    부원장(VICE_PRINCIPAL)·인사관리자 등은 제외."""
    _, _, role = staff_scope(user)
    return role in DIRECTOR_UP_ROLES or user.is_super_admin()


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
                        "kind": _tt_change_kind(c.detail),
                        "detail": c.detail, "actor": an, "time": _kst_dt_str(c.create_time),
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
        "enroll_no": sp.enroll_no or "",
        "birth_date": str(sp.birth_date) if sp.birth_date else "",
        "gender": sp.gender or "",
        "zipcode": sp.zipcode or "", "address": sp.address or "", "address_detail": sp.address_detail or "",
        "student_phone": sp.student_phone or "",
        "legacy_url": sp.legacy_url or "",
        "consent_paper": bool(sp.consent_paper),
        # 앞날로 잡아 둔 상태 변경(퇴원·휴원 예정). 그날이 되면 저절로 바뀐다.
        "pending_status": sp.pending_status or "",
        "pending_date": str(sp.pending_date) if sp.pending_date else "",
        "pending_reason": sp.pending_reason or "",
        "parent_name": sp.parent_name or "", "parent_phone": sp.parent_phone or "",
        "parent_relation": sp.parent_relation or "", "notify_optin": sp.notify_optin,
        "guardian2_phone": sp.guardian2_phone or "", "guardian2_relation": sp.guardian2_relation or "",
        "school_type": sp.school_type or "", "school_name": sp.school_name or "", "grade": sp.grade or "",
        "enrollment_date": str(sp.enrollment_date) if sp.enrollment_date else "",
        "lesson_start_date": str(sp.lesson_start_date) if sp.lesson_start_date else "",
        "weekly_sessions": sp.weekly_sessions,
        "program": sp.program or "",
        "programs": sp.programs or "",
        "memo": sp.memo or "",
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
                    "actor": _name_of(c.actor) if c.actor_id else "", "time": _kst_dt_str(c.create_time)}
                   for c in StudentStatusChange.objects.filter(student=u).select_related("actor")[:200]]
        lead = Lead.objects.filter(converted_user=u).order_by("id").first()
        lead_data = LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data if lead else None
        pdict = _student_profile_dict(sp)
        pdict["real_name"] = _name_of(u)
        try:
            profile_edits = _json.loads(sp.edit_log) if (sp and sp.edit_log) else []
        except (ValueError, TypeError):
            profile_edits = []
        # 개별 시간표 — 종료된 것도 함께 보낸다. 화면의 '과거 이력 보기' 가 갈라 보여 주는데
        # 여기서 빼 버려 지운 시간표가 이력에 아예 안 나왔다(이정윤 토요일이 그랬다).
        # 정렬은 적용 시작일 순, 같으면 요일·시각 순 — 언제 무엇이 무엇으로 바뀌었는지 눈으로 따라간다.
        timetables = []
        for s in StudentTimetable.objects.select_related("instructor", "branch").filter(
                student=u).order_by(F("active_from").asc(nulls_first=True),
                                    "weekday", "start_time"):
            timetables.append({"id": s.id, "weekday": s.weekday, "start_time": str(s.start_time)[:5],
                               "duration_minutes": s.duration_minutes, "program": s.program or "",
                               "subject": s.subject or resolve_program_label(s.program) or "미지정",
                               "instructor": ({"id": s.instructor_id, "name": _name_of(s.instructor)} if s.instructor_id else None),
                               "frequency": s.frequency, "branch": ({"id": s.branch_id, "name": s.branch.name} if s.branch_id else None),
                               "status": s.status,
                               "active_from": str(s.active_from) if s.active_from else "",
                               "active_until": str(s.active_until) if s.active_until else ""})
        return self.success({
            "id": u.id, "username": u.username, "real_name": _name_of(u),
            "branch": (prof.branch.name if prof and prof.branch_id else ""),
            "branch_id": prof.branch_id if prof else None,
            "enrollment_status": sp.enrollment_status if sp else EnrollmentStatus.ENROLLED,
            "profile": pdict, "profile_edits": profile_edits, "guardians": guardians, "status_history": history,
            "timetables": timetables,
            "lead": lead_data, "lead_id": lead.id if lead else None,
        })

    @admin_role_required
    def put(self, request):
        """학생 인적사항 수정 + 변경 이력(누가·언제·무엇을 전▸후) 기록."""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        sp, _ = StudentProfile.objects.get_or_create(user=u)
        rn = (data.get("real_name") or "").strip()
        name_changed = False
        if rn:
            up, _ = UserProfile.objects.get_or_create(user=u)
            name_changed = (up.real_name or "") != rn
            up.real_name = rn
            up.save(update_fields=["real_name"])

        FIELDS = [
            ("gender", "성별", None), ("zipcode", "우편번호", None),
            ("address", "주소", None), ("address_detail", "상세주소", None),
            ("student_phone", "학생 연락처", None),
            ("parent_name", "보호자 이름", None), ("parent_phone", "보호자 연락처", None),
            ("parent_relation", "보호자 관계", None),
            ("school_type", "학교 구분", "school_type"), ("school_name", "학교 이름", None),
            ("grade", "학년", None), ("program", "등록 과정(단일)", None),
            ("guardian2_phone", "기타 보호자 휴대폰", None), ("guardian2_relation", "기타 보호자 관계", None),
            ("memo", "요청사항·알릴사항", None),
        ]

        def _disp(f, cat, v):
            v = (v or "").strip() if isinstance(v, str) else v
            if f == "gender":
                return {"M": "남", "F": "여"}.get(v, v or "(없음)") or "(없음)"
            if cat:
                return _opt_label(cat, v) if v else "(없음)"
            return v if v not in (None, "") else "(없음)"

        changed = []
        for f, label, cat in FIELDS:
            if f in data:
                newv = (data.get(f) or "").strip()
                oldv = getattr(sp, f) or ""
                if oldv != newv:
                    changed.append({"label": label, "old": _disp(f, cat, oldv), "new": _disp(f, cat, newv)})
                setattr(sp, f, newv)
        for df, label in (("birth_date", "생년월일"), ("lesson_start_date", "수업 시작일"), ("enrollment_date", "등록일")):
            if df in data:
                newv = data.get(df) or None
                oldv = getattr(sp, df)
                oldv_s, newv_s = (str(oldv) if oldv else "(없음)"), (str(newv) if newv else "(없음)")
                if oldv_s != newv_s:
                    changed.append({"label": label, "old": oldv_s, "new": newv_s})
                setattr(sp, df, newv)
        if "consent_paper" in data:
            newv = bool(data.get("consent_paper"))
            if bool(sp.consent_paper) != newv:
                changed.append({"label": "개인정보 동의(종이 보관)",
                                "old": "예" if sp.consent_paper else "아니오",
                                "new": "예" if newv else "아니오"})
            sp.consent_paper = newv
        if "weekly_sessions" in data:
            newv = data.get("weekly_sessions") or None
            oldv = sp.weekly_sessions
            if oldv != newv:
                changed.append({"label": "주 교육 회수", "old": str(oldv) if oldv else "(없음)", "new": str(newv) if newv else "(없음)"})
            sp.weekly_sessions = newv
        if "programs" in data:
            def _fmt_progs(progs):
                parts = []
                for p in (progs or []):
                    if not isinstance(p, dict) or not p.get("value"):
                        continue
                    if p["value"] == "LANG" and p.get("language"):
                        parts.append(_opt_label("program", "LANG") + "(" + p["language"] + ")")
                    elif p.get("custom"):
                        parts.append(p["custom"])
                    else:
                        parts.append(_opt_label("program", p["value"]))
                return " · ".join(parts) if parts else "(없음)"
            raw = data.get("programs")
            try:
                new_progs = _json.loads(raw) if isinstance(raw, str) else (raw or [])
            except (ValueError, TypeError):
                new_progs = []
            try:
                old_progs = _json.loads(sp.programs) if sp.programs else []
            except (ValueError, TypeError):
                old_progs = []
            new_disp, old_disp = _fmt_progs(new_progs), _fmt_progs(old_progs)
            if new_disp != old_disp:
                item = {"label": "등록 과정", "old": old_disp, "new": new_disp}
                reason = (data.get("programs_reason") or "").strip()
                if reason:
                    item["reason"] = reason
                changed.append(item)
                sp.programs = _json.dumps(new_progs, ensure_ascii=False)
                sp.program = new_progs[0].get("value", "") if new_progs else ""
                sp.program_language = new_progs[0].get("language", "") if new_progs else ""
        if changed:
            try:
                log = _json.loads(sp.edit_log) if sp.edit_log else []
            except (ValueError, TypeError):
                log = []
            log.append({"time": _now_kst_str(), "by": _name_of(request.user), "items": changed})
            sp.edit_log = _json.dumps(log, ensure_ascii=False)

        sp.save()
        # 이름이나 성별이 바뀌면 안내 음성을 다시 만든다(이름을 읽고, 성별로 목소리가 갈린다)
        if name_changed or any(c.get("label") == "성별" for c in changed):
            from ..services_voice import build_student_voice
            build_student_voice(u)
        return self.success("ok")


class StudentStatusAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """적용일 뒤에 남은 수업을 미리 보여 준다.

        퇴원 적용일은 '그날부터 안 온다'는 뜻이다. 그런데 그 뒤에 잡힌 수업이 있으면
        어긋난다 — 오늘 퇴원인데 목요일 보강이 남는 식이다(이준영 8/25). 고르기 전에
        몇 건이 걸리는지 보여 줘야 날짜를 바로 잡을 수 있다."""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_view_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            eff = datetime.strptime(request.GET.get("after"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            eff = (now() + timedelta(hours=9)).date()
        rows = []
        for o in LessonOccurrence.objects.filter(student=u, date__gte=eff) \
                                         .exclude(status__in=["CANCELLED", "HOLIDAY"]) \
                                         .select_related("makeup_for").order_by("date")[:50]:
            att = DailyAttendance.objects.filter(student=u, date=o.date).first()
            rows.append({"date": str(o.date), "time": str(o.start_time)[:5],
                         "status": o.status, "is_makeup": o.is_makeup,
                         "is_extra": o.is_extra, "extra_reason": o.extra_reason,
                         "checked": bool(att and (att.check_in_at or att.check_out_at))})
        return self.success({"after": str(eff), "rows": rows,
                             "last": (rows[-1]["date"] if rows else "")})

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
        reason = (data.get("reason") or "").strip()
        eff_s = data.get("effective_date") or None
        eff = None
        if eff_s:
            try:
                eff = datetime.strptime(eff_s, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return self.error("적용일이 올바르지 않습니다.")
        today = (now() + timedelta(hours=9)).date()

        # 적용일이 앞날이면 그날까지는 지금 상태 그대로다. 미리 바꿔 버리면 아직 다니는
        # 학생이 재원생 수와 원비 청구 대상에서 빠져 그달 청구서가 안 나간다.
        if eff and eff > today:
            if from_status == to_status:
                return self.error("이미 해당 상태입니다.")
            sp.pending_status = to_status
            sp.pending_date = eff
            sp.pending_reason = reason
            sp.save(update_fields=["pending_status", "pending_date", "pending_reason"])
            # 시간표는 그날부터 끊는다(수업이 미리 안 잡히게)
            tt_msg = self._sync_timetables(u, to_status, request.user, reason, eff_s)
            return self.success({"scheduled": True, "date": str(eff), "timetable": tt_msg})

        if from_status == to_status:
            return self.error("이미 해당 상태입니다.")
        sp.enrollment_status = to_status
        sp.pending_status = ""
        sp.pending_date = None
        sp.pending_reason = ""
        sp.save(update_fields=["enrollment_status", "pending_status", "pending_date", "pending_reason"])
        StudentStatusChange.objects.create(
            student=u, from_status=from_status, to_status=to_status,
            reason=reason, effective_date=eff, actor=request.user)

        # 등록상태에 따라 개별 시간표 자동 처리(+이력)
        tt_msg = self._sync_timetables(u, to_status, request.user, reason, eff_s)
        return self.success({"timetable": tt_msg})

    @admin_role_required
    @admin_role_required
    def put(self, request):
        """잡아 둔 예약을 고친다. 취소하고 다시 잡으면 이력에 두 줄이 남아 어수선하다."""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        sp = StudentProfile.objects.filter(user=u).first()
        if not sp or not sp.pending_status:
            return self.error("잡아 둔 예약이 없습니다.")
        to_status = data.get("status") or sp.pending_status
        reason = (data.get("reason") or "").strip()
        if not reason:
            return self.error("사유를 적어 주세요.")
        try:
            eff = datetime.strptime(data.get("effective_date"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return self.error("적용일이 올바르지 않습니다.")
        today = (now() + timedelta(hours=9)).date()
        if eff <= today:
            return self.error("앞날로만 잡을 수 있습니다. 오늘부터 바꾸려면 [상태 변경]을 쓰세요.")
        sp.pending_status = to_status
        sp.pending_date = eff
        sp.pending_reason = reason
        sp.save(update_fields=["pending_status", "pending_date", "pending_reason"])
        tt_msg = self._sync_timetables(u, to_status, request.user, reason, str(eff))
        return self.success({"date": str(eff), "timetable": tt_msg})
    def delete(self, request):
        """예약해 둔 상태 변경 취소. 시간표는 그대로 두고 예약만 푼다(되돌리려면 다시 잡는다)."""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        sp = StudentProfile.objects.filter(user=u).first()
        if not sp or not sp.pending_status:
            return self.error("예약된 것이 없습니다.")
        sp.pending_status = ""
        sp.pending_date = None
        sp.pending_reason = ""
        sp.save(update_fields=["pending_status", "pending_date", "pending_reason"])
        return self.success({"ok": True})

    @staticmethod
    def _sync_timetables(student, to_status, actor, reason, effective_date=None):
        """등록상태에 따라 개별 시간표를 정리한다.

        시간표를 지우거나 숨기는 대신 '유효기간(active_until)'을 적용일 전날로 끊는 방식이다.
        그래야 적용일 이전 기록(수업·등원 이력)은 그대로 남아 나중에 확인할 수 있고,
        적용일 이후로는 시간표가 자동으로 안 잡힌다. 이미 만들어져 있던 적용일 이후 수업 중
        실제 기록(등원·결석·비고·수업일지·연결된 보강)이 없는 것만 지운다.

        - 휴원: 유효기간 끊고 PAUSED. 재등록 시 그 시점부터 새 기간으로 되살림.
        - 퇴원: 유효기간 끊고 ENDED. 재등록해도 되살아나지 않아 새로 등록해야 함.
        - 재등록: PAUSED 시간표를 복제해 적용일부터 새로 시작하는 시간표를 만든다.
          (원본은 지난 기간 기록으로 그대로 남는다)
        """
        from ..models import TimetableStatus
        eff = effective_date
        if isinstance(eff, str):
            try:
                eff = datetime.strptime(eff, "%Y-%m-%d").date()
            except ValueError:
                eff = None
        if not eff:
            eff = (now() + timedelta(hours=9)).date()
        slots = StudentTimetable.objects.filter(student=student)

        if to_status == EnrollmentStatus.ENROLLED:
            paused = list(slots.filter(status=TimetableStatus.PAUSED))
            if not paused:
                return "되살릴 시간표가 없습니다 — 시간표를 새로 등록해주세요."
            made = 0
            for s0 in paused:
                # 적용일부터 다시 시작하는 새 시간표(원본은 지난 기간으로 보존)
                if _find_slot_conflict(student.id, s0.weekday, s0.start_time, s0.duration_minutes,
                                       eff, None, exclude_id=s0.id):
                    continue
                StudentTimetable.objects.create(
                    student=student, branch=s0.branch, class_type=s0.class_type,
                    weekday=s0.weekday, start_time=s0.start_time, duration_minutes=s0.duration_minutes,
                    instructor=s0.instructor, program=s0.program, subject=s0.subject,
                    frequency=s0.frequency, room=s0.room, status=TimetableStatus.ACTIVE,
                    active_from=eff, active_until=None)
                made += 1
            label = "재등록 — %s부터 시간표 복원" % eff
            TimetableChange.objects.create(
                student=student, actor=actor, action="UPDATE",
                reason=reason or "등록상태 변경 자동 처리",
                detail=("%s (%d건)" % (label, made))[:255])
            return "%s %d건" % (label, made)

        if to_status == EnrollmentStatus.ON_LEAVE:
            qs = slots.filter(status=TimetableStatus.ACTIVE)
            new_status, label = TimetableStatus.PAUSED, "휴원 처리 — %s부터 시간표 중지" % eff
            action = "UPDATE"
        elif to_status == EnrollmentStatus.WITHDRAWN:
            qs = slots.exclude(status=TimetableStatus.ENDED)
            new_status, label = TimetableStatus.ENDED, "퇴원 처리 — %s부터 시간표 종료" % eff
            action = "DELETE"
        else:
            return ""

        until = eff - timedelta(days=1)
        today = (now() + timedelta(hours=9)).date()
        # 앞날 예약이면 상태를 미리 바꾸지 않는다. 아직 오지 않은 일인데 '휴원 중지'라고
        # 적히면 지금 멈춘 것으로 읽힌다. 기간만 끊어 두고, 적용일이 오면 apply_due_status 가
        # 상태를 바꾼다.
        later = eff > today
        changed = 0
        for s0 in qs:
            if not later:
                s0.status = new_status
            # 적용일 이전부터 쓰던 시간표만 기간을 끊는다(적용일 이후 시작 예정이면 통째로 끝난 것으로 본다)
            s0.active_until = until if (not s0.active_from or s0.active_from <= until) else s0.active_from
            s0.save(update_fields=(["active_until"] if later else ["status", "active_until"]))
            changed += 1

        # 적용일 이후로 이미 만들어져 있던 수업 정리 — 실제 기록이 있는 건 사실이므로 남긴다
        upcoming = list(LessonOccurrence.objects.filter(
            student=student, date__gte=eff, is_makeup=False, source_timetable__isnull=False))
        rec_ids = _occ_record_ids(upcoming)
        removed = 0
        for occ in upcoming:
            if occ.id in rec_ids or occ.time_change_reason:
                continue
            occ.delete()
            removed += 1
        if changed or removed:
            TimetableChange.objects.create(
                student=student, actor=actor, action=action,
                reason=reason or "등록상태 변경 자동 처리",
                detail=("%s (시간표 %d건%s)" % (label, changed,
                        (", 예정 수업 %d건 정리" % removed) if removed else ""))[:255])
        return "%s %d건" % (label, changed) if changed else ""


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
        parent_user = None
        # 검색에서 고른 기존 보호자(parent_id) 직접 연결
        if data.get("parent_id"):
            parent_user = User.objects.filter(id=data.get("parent_id"),
                                              academy_profile__role=AcademyRole.PARENT).first()
            if not parent_user:
                return self.error("보호자 계정을 찾을 수 없습니다.")
        norm = _norm_phone(data.get("parent_phone"))
        if parent_user is None and not norm:
            return self.error("보호자 연락처를 입력하세요.")
        if parent_user is None:
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


class GuardianSearchAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """보호자(학부모) 검색. 이름·연락처·연결 학생명으로 검색, 연결 학생 정보 포함."""
        q = (request.GET.get("q") or "").strip()
        parents = AcademyProfile.objects.select_related("user").filter(role=AcademyRole.PARENT)
        out = []
        for pp in parents[:500]:
            pu = pp.user
            try:
                pname = pu.userprofile.real_name or ""
            except Exception:
                pname = ""
            children = []
            for g in GuardianStudent.objects.select_related("student").filter(parent=pu):
                cu = g.student
                csp = getattr(cu, "student_profile", None)
                try:
                    cname = cu.userprofile.real_name or cu.username
                except Exception:
                    cname = cu.username
                children.append({"id": cu.id, "name": cname,
                                 "school_type": (csp.school_type if csp else ""),
                                 "school_name": (csp.school_name if csp else ""),
                                 "grade": (csp.grade if csp else "")})
            hay = " ".join([pname, pp.phone] + [c["name"] for c in children]).lower()
            if q and q.lower() not in hay:
                continue
            out.append({"parent_id": pu.id, "name": pname, "phone": pp.phone, "children": children})
        return self.success(out[:200])


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
            summary=summary, counsel_at=_parse_kst_local_dt(data.get("counsel_at")))
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)


class StudentCredentialAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """학생 사이트 계정 목록. ?student_id="""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_view_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        out = [{"id": c.id, "site": c.site, "login_id": c.login_id, "password": c.password}
               for c in u.site_credentials.all()]
        return self.success(out)

    @admin_role_required
    def post(self, request):
        """사이트 계정 추가/수정. id 있으면 수정, 없으면 추가. {student_id, id?, site, login_id, password}"""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        fields = {"site": (data.get("site") or "").strip(),
                  "login_id": (data.get("login_id") or "").strip(),
                  "password": (data.get("password") or "").strip()}
        if data.get("id"):
            c = StudentCredential.objects.filter(id=data.get("id"), student=u).first()
            if not c:
                return self.error("항목이 없습니다.")
            for k, v in fields.items():
                setattr(c, k, v)
            c.save()
        else:
            last = u.site_credentials.order_by("-order").first()
            c = StudentCredential.objects.create(
                student=u, order=((last.order + 1) if last else 0), **fields)
        return self.success({"id": c.id, **fields})

    @admin_role_required
    def delete(self, request):
        """사이트 계정 삭제. ?id="""
        c = StudentCredential.objects.select_related("student").filter(id=request.GET.get("id")).first()
        if not c:
            return self.error("항목이 없습니다.")
        prof = getattr(c.student, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("No permission for this branch")
        c.delete()
        return self.success(True)


class MsgTemplateGroupAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """문자 템플릿 그룹(폴더) 목록."""
        qs = MsgTemplateGroup.objects.filter(is_hidden=False).order_by("order", "id")
        return self.success([{"id": g.id, "name": g.name, "order": g.order} for g in qs])

    @admin_role_required
    def post(self, request):
        """그룹 추가/이름변경. {id?, name}"""
        name = (request.data.get("name") or "").strip()
        if not name:
            return self.error("이름을 입력하세요.")
        if request.data.get("id"):
            g = MsgTemplateGroup.objects.filter(id=request.data.get("id")).first()
            if not g:
                return self.error("그룹이 없습니다.")
            g.name = name
            g.save(update_fields=["name"])
        else:
            last = MsgTemplateGroup.objects.order_by("-order").first()
            g = MsgTemplateGroup.objects.create(name=name, order=((last.order + 1) if last else 0))
        return self.success({"id": g.id, "name": g.name})

    @admin_role_required
    def delete(self, request):
        """그룹 소프트삭제(그 안 템플릿도 함께 숨김)."""
        g = MsgTemplateGroup.objects.filter(id=request.GET.get("id")).first()
        if not g:
            return self.error("그룹이 없습니다.")
        g.is_hidden = True
        g.save(update_fields=["is_hidden"])
        MsgTemplate.objects.filter(group=g).update(is_hidden=True)
        return self.success(True)

    @admin_role_required
    def put(self, request):
        """그룹 순서 재정렬. {ids:[...]} 순서대로 0,1,2…"""
        for idx, gid in enumerate(request.data.get("ids") or []):
            MsgTemplateGroup.objects.filter(id=gid).update(order=idx)
        return self.success(True)


class MsgTemplateAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """템플릿 목록. ?group_id= (없으면 전체). 원장+는 ?show_hidden=1로 삭제분·수정이력 포함."""
        manager = _is_manager(request.user)
        show_hidden = manager and request.GET.get("show_hidden")
        qs = MsgTemplate.objects.all()
        if not show_hidden:
            qs = qs.filter(is_hidden=False)
        gid = request.GET.get("group_id")
        if gid:
            qs = qs.filter(group_id=gid)
        qs = qs.order_by("order", "id")
        out = []
        for t in qs:
            edits = []
            if manager and t.edit_log:
                try:
                    edits = _json.loads(t.edit_log)
                except (ValueError, TypeError):
                    edits = []
            out.append({"id": t.id, "group_id": t.group_id, "title": t.title,
                        "body": t.body, "is_hidden": t.is_hidden, "edits": edits})
        return self.success(out)

    @admin_role_required
    def post(self, request):
        """템플릿 추가/수정. {id?, group_id, title, body}. 수정 시 이력 기록(누가·언제·전→후)."""
        data = request.data
        title = (data.get("title") or "").strip()
        if not title:
            return self.error("제목을 입력하세요.")
        new_body = data.get("body") or ""
        if data.get("id"):
            t = MsgTemplate.objects.filter(id=data.get("id")).first()
            if not t:
                return self.error("템플릿이 없습니다.")
            items = []
            if t.title != title:
                items.append({"label": "제목", "old": t.title, "new": title})
            if t.body != new_body:
                items.append({"label": "내용", "old": t.body, "new": new_body})
            if items:
                try:
                    log = _json.loads(t.edit_log) if t.edit_log else []
                except (ValueError, TypeError):
                    log = []
                log.append({"time": _now_kst_str(), "by": _name_of(request.user), "items": items})
                t.edit_log = _json.dumps(log, ensure_ascii=False)
        else:
            last = MsgTemplate.objects.order_by("-order").first()
            t = MsgTemplate(order=((last.order + 1) if last else 0))
        t.group_id = data.get("group_id") or None
        t.title = title
        t.body = new_body
        t.save()
        return self.success({"id": t.id})

    @admin_role_required
    def delete(self, request):
        """템플릿 소프트삭제. 원장+는 ?restore=1로 복원."""
        t = MsgTemplate.objects.filter(id=request.GET.get("id")).first()
        if not t:
            return self.error("템플릿이 없습니다.")
        if request.GET.get("restore"):
            if not _is_manager(request.user):
                return self.error("복원 권한이 없습니다.")
            t.is_hidden = False
        else:
            t.is_hidden = True
        t.save(update_fields=["is_hidden"])
        return self.success(True)

    @admin_role_required
    def put(self, request):
        """템플릿 순서 재정렬. {ids:[...]} 순서대로 0,1,2…"""
        for idx, tid in enumerate(request.data.get("ids") or []):
            MsgTemplate.objects.filter(id=tid).update(order=idx)
        return self.success(True)


# ── 사이트 전용 고정 문자 템플릿(용도 고정 · 지점별 내용) ──

FIXED_TEMPLATE_DEFS = [
    {
        "key": "enroll_link",
        "title": "등록 링크 안내",
        "desc": "신규 등록 시 학부모에게 입회원서 작성 링크를 보낼 때 쓰는 문구.",
        "default": (
            "안녕하세요, {지점명}입니다.\n"
            "{학생명} 학생의 등록을 위해 아래 입회원서를 작성해 주세요.\n\n"
            "{링크}\n\n"
            "작성에 어려움이 있으시면 편하게 연락 주세요. 감사합니다."
        ),
    },
]
_FIXED_DEF_MAP = {d["key"]: d for d in FIXED_TEMPLATE_DEFS}


def _fixed_body(branch_id, key):
    """지점별 저장된 고정 템플릿 내용. 없으면 기본 문구."""
    ft = FixedTemplate.objects.filter(branch_id=branch_id, key=key).first()
    if ft and ft.body:
        return ft.body
    d = _FIXED_DEF_MAP.get(key)
    return d["default"] if d else ""


def _fill_vars(body, mapping):
    for k, v in mapping.items():
        body = body.replace("{" + k + "}", v or "")
    return body


class FixedTemplateAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """지점별 고정 템플릿 목록. 열람 가능 지점 × 용도별 내용(없으면 기본)."""
        view = viewable_branch_ids(request.user)
        bqs = Branch.objects.all().order_by("name")
        if view is not None:
            bqs = bqs.filter(id__in=view)
        out = []
        for b in bqs:
            can_edit = can_manage_branch(request.user, b.id)
            tpls = []
            for d in FIXED_TEMPLATE_DEFS:
                ft = FixedTemplate.objects.filter(branch_id=b.id, key=d["key"]).first()
                tpls.append({"key": d["key"], "title": d["title"], "desc": d["desc"],
                             "body": (ft.body if (ft and ft.body) else d["default"]),
                             "customized": bool(ft and ft.body),
                             "updated_by": _name_of(ft.updated_by) if ft else None,
                             "update_time": _kst_dt_str(ft.update_time) if ft else None})
            out.append({"branch_id": b.id, "branch_name": b.name,
                        "can_edit": can_edit, "templates": tpls})
        return self.success(out)

    @admin_role_required
    def put(self, request):
        """지점 고정 템플릿 내용 수정. {branch_id, key, body}. 해당 지점 관리 권한 필요."""
        data = request.data
        bid = data.get("branch_id")
        key = data.get("key")
        if key not in _FIXED_DEF_MAP:
            return self.error("알 수 없는 템플릿입니다.")
        if not Branch.objects.filter(id=bid).exists():
            return self.error("지점이 없습니다.")
        if not can_manage_branch(request.user, bid):
            return self.error("이 지점의 템플릿을 수정할 권한이 없습니다.")
        ft, _ = FixedTemplate.objects.get_or_create(branch_id=bid, key=key)
        ft.body = data.get("body") or ""
        ft.updated_by = request.user
        ft.save()
        return self.success({"branch_id": bid, "key": key, "body": ft.body,
                             "customized": bool(ft.body), "updated_by": _name_of(request.user),
                             "update_time": _kst_dt_str(ft.update_time)})


# ── 개발일지(Claude Code 세션 트랜스크립트 뷰어, 본부 관리자 전용) ──
import glob as _glob

DEVLOG_DIR = "/devlog_src"
_devlog_cache = {"sig": None, "items": None}


def _devlog_tool_summary(b):
    name = b.get("name") or "tool"
    inp = b.get("input") or {}
    if name == "Bash":
        return "Bash: " + (inp.get("description") or (inp.get("command") or "")[:80])
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        fp = inp.get("file_path") or inp.get("notebook_path") or ""
        return name + ": " + _os.path.basename(fp)
    if name in ("Grep", "Glob"):
        return name + ": " + (inp.get("pattern") or "")
    if name == "TodoWrite":
        return "할 일 목록 정리"
    return name


def _devlog_clean_user(content):
    import re
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
            elif isinstance(b, str):
                parts.append(b)
        text = "\n".join(parts)
    else:
        text = content or ""
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S)
    return text.strip()


def _devlog_is_noise(text):
    if not text:
        return True
    noise = ("<command", "<local-command", "Caveat:", "[Request interrupted",
             "This session is being continued", "<bash-", "<user-", "<persisted-")
    return text.startswith(noise)


def _build_devlog():
    files = sorted(_glob.glob(_os.path.join(DEVLOG_DIR, "*.jsonl")))
    sig = tuple((f, _os.path.getmtime(f)) for f in files)
    if _devlog_cache["sig"] == sig and _devlog_cache["items"] is not None:
        return _devlog_cache["items"]
    rows = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if d.get("type") not in ("user", "assistant"):
                        continue
                    msg = d.get("message")
                    if not isinstance(msg, dict):
                        continue
                    ts = d.get("timestamp") or ""
                    role = msg.get("role")
                    content = msg.get("content")
                    if role == "user":
                        if isinstance(content, list) and content and all(
                                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                            continue
                        text = _devlog_clean_user(content)
                        if _devlog_is_noise(text):
                            continue
                        rows.append((ts, "user", "msg", text, ""))
                    else:
                        if isinstance(content, str):
                            t = content.strip()
                            if t:
                                rows.append((ts, "assistant", "msg", t, ""))
                            continue
                        if isinstance(content, list):
                            for b in content:
                                if not isinstance(b, dict):
                                    continue
                                bt = b.get("type")
                                if bt == "text":
                                    t = (b.get("text") or "").strip()
                                    if t:
                                        rows.append((ts, "assistant", "msg", t, ""))
                                elif bt == "tool_use":
                                    rows.append((ts, "assistant", "tool", "", _devlog_tool_summary(b)))
        except OSError:
            continue
    rows.sort(key=lambda r: r[0])
    items = [{"i": i, "ts": (r[0][:19].replace("T", " ")), "role": r[1],
              "kind": r[2], "text": r[3], "tool": r[4]} for i, r in enumerate(rows)]
    # 빈 결과(권한 문제 등)는 캐시하지 않아 권한 복구 후 즉시 재반영되게 한다.
    if items:
        _devlog_cache["sig"] = sig
        _devlog_cache["items"] = items
    return items


class DevLogAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """개발일지: Claude Code 세션 대화를 최신순 페이지네이션. 본부 관리자 전용.
        mode=brief 면 지시/설명만(도구 동작 생략). before=필터목록 내 시작위치."""
        if not request.user.is_super_admin():
            return self.error("본부 관리자만 볼 수 있습니다.")
        items = _build_devlog()
        if request.GET.get("mode") == "brief":
            items = [it for it in items if it["kind"] == "msg"]
        total = len(items)
        try:
            limit = min(int(request.GET.get("limit", 60)), 200)
        except (TypeError, ValueError):
            limit = 60
        before = request.GET.get("before")
        if before in (None, ""):
            end = total
        else:
            try:
                end = max(0, min(total, int(before)))
            except (TypeError, ValueError):
                end = total
        start = max(0, end - limit)
        return self.success({"items": items[start:end], "start": start,
                             "has_more": start > 0, "total": total})


def _cal_today(user, d, view, bid):
    """그날과 이레 안의 일정. 달력을 열지 않아도 오늘 무엇이 있는지 보여야 한다.
    끌어온 것(휴무·시험·상담·보강)은 이미 저마다 제 자리에서 알리므로 여기선 뺀다."""
    from ..models import CalendarEvent, OptionItem, AcademyProfile
    prof = AcademyProfile.objects.filter(user=user, is_deleted=False).first()
    kmap = {o.value: (o.label, o.color or "#0f766e")
            for o in OptionItem.objects.filter(category="calendar_kind")}
    out = []
    for e in CalendarEvent.objects.filter(is_deleted=False, start_date__lte=d + timedelta(days=7)) \
                                  .select_related("branch"):
        end = e.end_date or e.start_date
        if end < d:
            continue
        if e.scope == "PRIVATE" and e.created_by_id != user.id:
            continue
        if e.scope == "BRANCH":
            if view is not None and e.branch_id not in view:
                continue
            if bid and str(e.branch_id) != str(bid):
                continue
        lb, color = kmap.get(e.kind, ("일정", "#0f766e"))
        running = e.start_date <= d <= end
        out.append({"id": e.id, "title": e.title, "kind_label": lb, "color": color,
                    "date": str(e.start_date), "end": str(end), "time": e.start_time,
                    "scope": e.scope, "branch": (e.branch.name if e.branch_id else ""),
                    "d_day": (e.start_date - d).days, "running": running})
    out.sort(key=lambda x: (not x["running"], x["date"], x["time"] or "99:99"))
    return out


def _board_unread(user):
    """읽음 확인을 켠 폴더의 안 읽은 글. 게시판에 들어가 봐야 새 글이 있는 줄 알면
    규정 개정이 전달되지 않는다. 하루에 꼭 한 번 여는 화면에 걸어 둔다."""
    from ..models import BoardFolder, BoardPost, BoardRead
    from .board import _can_see, _role_of, _is_super
    role, bid = _role_of(user)
    sup = _is_super(user)
    fs = [f for f in BoardFolder.objects.filter(is_deleted=False, need_read=True)
          if _can_see(f, role, bid, sup)]
    if not fs:
        return []
    seen = set(BoardRead.objects.filter(user=user).values_list("post_id", flat=True))
    out = {}
    for p in BoardPost.objects.filter(is_deleted=False, folder_id__in=[f.id for f in fs]) \
                              .select_related("folder").order_by("-id"):
        if p.id in seen:
            continue
        g = out.setdefault(p.folder_id, {"folder_id": p.folder_id, "folder": p.folder.name,
                                         "icon": p.folder.icon, "posts": []})
        if len(g["posts"]) < 5:
            g["posts"].append({"id": p.id, "title": p.title,
                               "time": str(p.create_time + timedelta(hours=9))[:19]})
        g["n"] = g.get("n", 0) + 1
    return sorted(out.values(), key=lambda x: -x["n"])


# ── 일일 운영 대시보드(오늘 수업 + 등원/하원 출결) ──

def _hm_kst(dt):
    """저장된 UTC datetime을 KST(+9) HH:MM 문자열로."""
    if not dt:
        return ""
    return (dt + timedelta(hours=9)).strftime("%H:%M")


EARLY_LEAVE_TAG = "EARLY_LEAVE_EXPECTED"


def _link_target_kind(occ_status, note_tag):
    """보강이 연결될 수 있는 대상인지와 그 종류(결석/조퇴예정). 둘 다 아니면 None."""
    if occ_status == OccurrenceStatus.ABSENT:
        return "absence"
    if occ_status == OccurrenceStatus.SCHEDULED and note_tag == EARLY_LEAVE_TAG:
        return "early_leave"
    return None


def _t2m(t):
    """time 또는 'HH:MM' 문자열 → 분(자정 기준).
    시리얼라이저를 거친 값은 문자열로 들어오므로 둘 다 받는다."""
    if isinstance(t, str):
        hh, mm = t.split(":")[:2]
        return int(hh) * 60 + int(mm)
    return t.hour * 60 + t.minute


def _time_overlaps(a_start, a_dur, b_start, b_dur):
    """두 수업이 실제로 겹치는지. 앞 수업이 끝나는 시각에 뒤 수업이 시작하는 '연속 수업'은 겹침 아님."""
    a0 = _t2m(a_start); a1 = a0 + (a_dur or 60)
    b0 = _t2m(b_start); b1 = b0 + (b_dur or 60)
    return a0 < b1 and b0 < a1


def _period_overlaps(af1, au1, af2, au2):
    """두 시간표 패턴의 유효기간(active_from~active_until)이 겹치는지. 없으면 무한대로 본다."""
    if au1 and af2 and au1 < af2:
        return False
    if au2 and af1 and au2 < af1:
        return False
    return True


def _find_slot_conflict(student_id, weekday, start_time, duration, active_from, active_until, exclude_id=None):
    """같은 학생의 다른 정규 시간표와 요일·시간·유효기간이 겹치는 것을 찾는다."""
    qs = StudentTimetable.objects.filter(student_id=student_id, weekday=weekday).exclude(status="ENDED")
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    for s in qs:
        if not _period_overlaps(active_from, active_until, s.active_from, s.active_until):
            continue
        if _time_overlaps(start_time, duration, s.start_time, s.duration_minutes):
            return s
    return None


def _find_day_conflict(student_id, d, start_time, duration, exclude_occ_id=None):
    """그 날짜에 이 학생이 이미 잡혀 있는 수업과 겹치는지.
    이미 만들어진 인스턴스뿐 아니라 '아직 안 만들어진 정규 시간표'까지 함께 본다
    (달력을 안 열어본 날짜는 인스턴스가 없어서, 인스턴스만 보면 겹침을 놓친다).
    반환: (시작 time, 길이, 라벨) 또는 None."""
    busy, covered = [], set()
    occs = LessonOccurrence.objects.filter(student_id=student_id, date=d)\
        .exclude(status=OccurrenceStatus.CANCELLED)
    if exclude_occ_id:
        # 지금 옮기는 수업 자신은 비교에서 빼는데, 그 수업이 어느 정규 시간표에서 나왔는지도
        # 함께 빼야 한다. 안 그러면 아래에서 그 시간표를 '아직 안 만들어진 수업'으로 보고
        # 원래 시각 그대로 비교해, 자기 자신과 겹친다고 나온다.
        ex = LessonOccurrence.objects.filter(id=exclude_occ_id).only("source_timetable_id").first()
        if ex and ex.source_timetable_id:
            covered.add(ex.source_timetable_id)
        occs = occs.exclude(id=exclude_occ_id)
    for o in occs:
        busy.append((o.start_time, o.duration_minutes, "보강" if o.is_makeup else "수업"))
        if o.source_timetable_id:
            covered.add(o.source_timetable_id)
    for s in StudentTimetable.objects.filter(student_id=student_id, weekday=d.weekday()).exclude(status="ENDED"):
        if s.id in covered or not _slot_active_on(s, d):
            continue
        busy.append((s.start_time, s.duration_minutes, "정규수업"))
    for st, du, label in busy:
        if _time_overlaps(start_time, duration, st, du):
            return (st, du, label)
    return None


def _conflict_msg(name, when, start_time, duration, label):
    """겹침 안내 문구. 언제·무엇과 겹치는지 알려줘야 바로 고칠 수 있다."""
    a0 = _t2m(start_time); a1 = a0 + (duration or 60)
    fmt = lambda m: "%02d:%02d" % (m // 60, m % 60)
    return ("%s 학생은 %s에 이미 %s이 있습니다(%s~%s). 시간이 겹쳐서 추가할 수 없습니다 — "
            "다른 시간을 고르거나 기존 수업을 먼저 정리해주세요."
            % (name, when, label, fmt(a0), fmt(a1)))


def _occ_record_ids(occs):
    """'실제로 뭔가 있었던 수업'의 id 집합 — 등원·결석/휴원·비고·수업일지·연결된 보강.
    시간표가 바뀌거나 삭제돼도 이건 사실이므로 지우지 않고 남긴다."""
    ids = [o.id for o in occs]
    if not ids:
        return set()
    att = set(DailyAttendance.objects.filter(
        student_id__in={o.student_id for o in occs}, date__in={o.date for o in occs},
        check_in_at__isnull=False).values_list("student_id", "date"))
    prog = set(LessonProgress.objects.filter(occurrence_id__in=ids, is_hidden=False)
               .values_list("occurrence_id", flat=True))
    mk = set(LessonOccurrence.objects.filter(makeup_for_id__in=ids)
             .values_list("makeup_for_id", flat=True))
    return {o.id for o in occs
            if ((o.student_id, o.date) in att or o.id in prog or o.id in mk
                or bool(o.note) or o.status != OccurrenceStatus.SCHEDULED)}


def _reconcile_slot_occurrences(slot, occ_qs):
    """시간표 패턴이 바뀌었을 때, 이미 만들어져 있던 수업 인스턴스를 그 패턴에 맞게 정리한다.
    - 패턴이 담당하는 날짜(같은 요일·유효기간 안): 소속을 이 패턴으로 맞추고, 그날만 따로 손본 적이
      없으면 시각·수업시간·강사·과정도 패턴 값으로 갱신. 소속을 안 맞추면 그 날짜를 아무도 담당하지
      않는 것으로 보여 나중에 같은 날 인스턴스가 하나 더 생기고 수업이 중복으로 뜬다.
    - 담당하지 않는 날짜(요일이 바뀐 경우 등): 실제로 뭔가 있었던 수업(등원·결석/휴원·비고·수업일지·
      연결된 보강)은 사실이므로 그대로 두고, 아무 일도 없던 예정만 삭제한다.
    반환: (갱신, 삭제, 보존) 건수."""
    occs = list(occ_qs)
    if not occs:
        return 0, 0, 0
    rec_ids = _occ_record_ids(occs)
    updated = dropped = kept = 0
    for occ in occs:
        has_record = occ.id in rec_ids
        overridden = bool(occ.time_change_reason)  # 그날만 개별 수정한 수업
        if occ.date.weekday() != slot.weekday or not _slot_active_on(slot, occ.date):
            if not overridden and not has_record:
                occ.delete()
                dropped += 1
            else:
                kept += 1
            continue
        occ.source_timetable = slot
        if overridden:
            kept += 1
        else:
            occ.start_time = slot.start_time
            occ.duration_minutes = slot.duration_minutes
            occ.program = slot.program
            occ.subject = slot.subject
            occ.instructor_id = slot.instructor_id
            updated += 1
        occ.save()
    return updated, dropped, kept


def _slot_active_on(s, d):
    """패턴이 날짜 d에 적용되는지: 유효기간(active_from~active_until)·격주 패리티 확인.
    active_from/active_until로 "이 날짜부터 이후" 시간표 분기를 표현한다(둘 다 없으면 항상 적용)."""
    if s.active_from and d < s.active_from:
        return False
    if s.active_until and d > s.active_until:
        return False
    if s.frequency == "BIWEEKLY" and s.active_from and ((d - s.active_from).days // 7) % 2 != 0:
        return False
    return True


def ensure_occurrences(d, branch_ids=None):
    """지정일 d의 정규 수업 인스턴스를 시간표 패턴에서 생성(없는 것만). branch_ids=None이면 전체."""
    wd = d.weekday()
    slots = StudentTimetable.objects.select_related("instructor", "branch").filter(
        weekday=wd, status="ACTIVE")
    if branch_ids is not None:
        slots = slots.filter(branch_id__in=branch_ids)
    existing = set(LessonOccurrence.objects.filter(date=d, source_timetable__isnull=False)
                   .values_list("source_timetable_id", flat=True))
    # 시간표를 바꾸면 옛 줄이 만들어 둔 수업이 그날 남아 있을 수 있다. 그때 새 줄로 또 만들면
    # 같은 학생이 같은 시각에 두 번 뜬다(한쪽은 결석, 한쪽은 등원 전처럼). 학생·시각으로도 본다.
    taken = set(LessonOccurrence.objects.filter(date=d, is_makeup=False)
                .exclude(status=OccurrenceStatus.CANCELLED)
                .values_list("student_id", "start_time"))
    for s in slots:
        if not _slot_active_on(s, d):
            continue
        if s.id in existing:
            continue
        if (s.student_id, s.start_time) in taken:
            continue
        LessonOccurrence.objects.get_or_create(
            source_timetable=s, date=d,
            defaults={"student_id": s.student_id, "branch_id": s.branch_id,
                      "start_time": s.start_time, "duration_minutes": s.duration_minutes,
                      "program": s.program, "subject": s.subject or resolve_program_label(s.program) or "미지정",
                      "instructor_id": s.instructor_id})


def _adhoc_lesson_rows(d, branch_ids):
    """'수업외 등원' 합성 행: 오늘 등원 체크는 됐지만 정규/보강 수업 인스턴스가 없는 학생.
    표시용 과목·수업시간·담당강사는 그 학생의 활성 시간표 중 요일이 가장 빠른 것에서 빌려온다."""
    have_occ = set(LessonOccurrence.objects.filter(date=d).exclude(status=OccurrenceStatus.CANCELLED)
                   .values_list("student_id", flat=True))
    att_qs = DailyAttendance.objects.filter(date=d, check_in_at__isnull=False)\
        .select_related("student", "student__userprofile")
    if branch_ids is not None:
        att_qs = att_qs.filter(branch_id__in=branch_ids)
    rows = []
    for a in att_qs:
        if a.student_id in have_occ:
            continue
        sp = getattr(a.student, "student_profile", None)
        prof = getattr(a.student, "academy_profile", None)
        tt = StudentTimetable.objects.filter(student_id=a.student_id, status="ACTIVE")\
            .select_related("instructor").order_by("weekday").first()
        rows.append({
            "occ_id": None, "adhoc": True, "student_id": a.student_id, "student_name": _name_of(a.student),
            "start_time": _hm_kst(a.check_in_at), "duration_minutes": (tt.duration_minutes if tt else 60),
            "time_changed": False, "time_change_reason": "", "orig_time": "",
            "subject": (tt.subject or resolve_program_label(tt.program) if tt else "") or "수업외",
            "instructor": _name_of(tt.instructor) if (tt and tt.instructor_id) else "미배정",
            "branch": (prof.branch.name if prof and prof.branch_id else ""),
            "branch_id": (prof.branch_id if prof else None),
            "biweekly": False, "is_makeup": False, "status": "SCHEDULED", "lesson_note": "", "no_makeup": False, "no_makeup_kind": "",
            "is_extra": False, "extra_reason": "",
            "school_type": (sp.school_type if sp else ""), "school_name": (sp.school_name if sp else ""),
            "grade": (sp.grade if sp else ""), "parent_phone": (sp.parent_phone if sp else ""),
            "student_phone": (sp.student_phone if sp else ""), "legacy_url": (sp.legacy_url if sp else ""),
            "att": {"in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at),
                    "note_tag": a.note_tag, "note": a.note},
            "progress": None,
        })
    return rows


def _dash_student_extra(d, lessons, late_min=5):
    """오늘 운영에서 '표시 항목'으로 켤 수 있는 부가 정보. 학생 단위로 한 번만 계산해
    내려준다(같은 학생이 여러 수업을 들어도 값은 하나).

    열마다 학생별로 따로 조회하면 44명 기준으로 조회가 수백 번이 되므로, 필요한
    데이터를 통째로 한 번씩 읽어 파이썬에서 묶는다."""
    sids = sorted({l["student_id"] for l in lessons if l.get("student_id")})
    if not sids:
        return {}
    out = {sid: {} for sid in sids}

    # 학교·학년 + 빠진 정보(학생을 앞에 두고 그 자리에서 채우려고 본다)
    for sp in StudentProfile.objects.filter(user_id__in=sids):
        out[sp.user_id]["school"] = _school_short(sp)
        out[sp.user_id]["missing"] = _missing_info(sp)
    for sid in sids:
        out[sid].setdefault("missing", [lb for _, lb in _REQUIRED_INFO])

    # 주간 요일 패턴(그날 유효한 정규 시간표만)
    for slot in StudentTimetable.objects.filter(student_id__in=sids).exclude(status="ENDED"):
        if not _slot_active_on(slot, d):
            continue
        e = out.setdefault(slot.student_id, {})
        wds = e.setdefault("weekdays", {})
        wds.setdefault(slot.weekday, []).append(str(slot.start_time)[:5])

    # 다음 수업 — 앞으로 한 달
    end = d + timedelta(days=30)
    for o in LessonOccurrence.objects.filter(
            student_id__in=sids, date__gt=d, date__lte=end).exclude(
            status=OccurrenceStatus.CANCELLED).order_by("date", "start_time"):
        e = out.setdefault(o.student_id, {})
        e.setdefault("next", []).append({
            "date": str(o.date), "wd": _WD[o.date.weekday()], "time": str(o.start_time)[:5],
            "status": o.status, "is_makeup": o.is_makeup,
            "is_extra": o.is_extra, "extra_reason": o.extra_reason,
            "subject": o.subject or ""})

    # 결석·지각 — 칸에는 이번 달 수만, 마우스를 올리면 지난달까지 날짜와 지각 분수를 본다
    m0 = d.replace(day=1)
    m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    p0 = (m0 - timedelta(days=1)).replace(day=1)        # 지난달 1일
    starts, detail = {}, {}
    for o in LessonOccurrence.objects.filter(
            student_id__in=sids, date__gte=p0, date__lte=m1).only(
            "student_id", "date", "start_time", "status"):
        this_month = o.date >= m0
        if o.status == OccurrenceStatus.ABSENT:
            e = out.setdefault(o.student_id, {})
            if this_month:
                e["m_absent"] = e.get("m_absent", 0) + 1
            detail.setdefault(o.student_id, []).append(
                {"date": str(o.date), "wd": _WD[o.date.weekday()], "kind": "absent",
                 "time": str(o.start_time)[:5], "this_month": this_month})
        key = (o.student_id, o.date)
        t = _t2m(o.start_time)
        if key not in starts or t < starts[key]:
            starts[key] = t
    for a in DailyAttendance.objects.filter(
            student_id__in=sids, date__gte=p0, date__lte=m1, check_in_at__isnull=False).only(
            "student_id", "date", "check_in_at"):
        ref = starts.get((a.student_id, a.date))
        if ref is None:
            continue
        hm = _hm_kst(a.check_in_at)
        diff = _t2m(hm) - ref
        if diff > late_min:
            this_month = a.date >= m0
            e = out.setdefault(a.student_id, {})
            if this_month:
                e["m_late"] = e.get("m_late", 0) + 1
            detail.setdefault(a.student_id, []).append(
                {"date": str(a.date), "wd": _WD[a.date.weekday()], "kind": "late",
                 "time": hm, "mins": diff, "this_month": this_month})
    for sid, rows in detail.items():
        # 결석과 지각을 따로 나누지 않고 날짜순으로 섞는다 — 둘 다 같은 '출결 문제'라
        # 섞어 봐야 "이달 들어 계속 늦네" 같은 흐름이 보인다.
        rows.sort(key=lambda r: (r["date"], r["time"]), reverse=True)
        out.setdefault(sid, {})["att_detail"] = rows[:30]
        out[sid]["prev_month"] = str(p0)[:7]

    # 미처리 보강(결석인데 보강일이 아직 없는 건)
    made = set(LessonOccurrence.objects.filter(
        is_makeup=True, makeup_for__isnull=False).values_list("makeup_for_id", flat=True))
    for o in LessonOccurrence.objects.filter(
            student_id__in=sids, status=OccurrenceStatus.ABSENT,
            is_makeup=False, no_makeup=False).only("id", "student_id"):
        if o.id in made:
            continue
        e = out.setdefault(o.student_id, {})
        e["mk_pending"] = e.get("mk_pending", 0) + 1
    return out


# 학교 구분 코드는 선택 목록의 값(ELEMENTARY/MIDDLE/HIGH/UNIVERSITY/INTL/ETC)이다.
# 예전에 'ELEM' 으로 잘못 적어 초등학교·대학교에 접미사가 안 붙었다.
_SCHOOL_SUFFIX_SHORT = {"ELEMENTARY": "초", "MIDDLE": "중", "HIGH": "고", "UNIVERSITY": "대"}


def _school_short(sp):
    """청일초등학교 5학년 → 청일초5. 국제학교처럼 접미사가 없는 곳은 이름만(달튼7)."""
    nm = (sp.school_name or "").strip()
    if not nm:
        return ""
    if nm == "성인":
        return "성인"
    suf = _SCHOOL_SUFFIX_SHORT.get(sp.school_type, "")
    if suf:
        # 이미 '청일초등학교'처럼 전체 이름이 들어 있으면 접미사를 겹쳐 붙이지 않는다
        for full in ("초등학교", "중학교", "고등학교", "대학교", "학교"):
            if nm.endswith(full):
                nm = nm[: -len(full)]
                break
        nm += suf
    g = "".join(ch for ch in (sp.grade or "") if ch.isdigit())
    return nm + g


class DashboardAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """지정일(기본 오늘)의 수업 인스턴스(정규+보강) + 등원/하원 출결."""
        ds = request.GET.get("date")
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date() if ds else now().date()
        except (TypeError, ValueError):
            d = now().date()
        wd = d.weekday()
        view = viewable_branch_ids(request.user)  # None=전체
        apply_due_status()          # 적용일이 된 예약을 여기서 반영한다
        ensure_occurrences(d, view)
        # 그날이 휴무일이면 이름을 함께 내려 화면 위에 사유를 띄운다("왜 수업이 없지?" 방지)
        from ..models import Holiday
        hq = Holiday.objects.filter(date=d, is_deleted=False)
        if view is not None:
            hq = hq.filter(Q(branch_id=None) | Q(branch_id__in=view))
        holiday_names = sorted({h.name for h in hq})
        occ = LessonOccurrence.objects.select_related(
            "student", "instructor", "branch", "source_timetable", "makeup_for").filter(
            date=d).exclude(status=OccurrenceStatus.CANCELLED)
        if view is not None:
            occ = occ.filter(branch_id__in=view)
        bid = request.GET.get("branch_id")
        if bid:
            occ = occ.filter(branch_id=bid)
        occ = occ.order_by("start_time", "id")  # 동시간대 2차 기준 고정(키오스크와 순서 일치)

        lessons = []
        sids = set()
        for o in occ:
            sids.add(o.student_id)
            sp = getattr(o.student, "student_profile", None)
            biweekly = bool(o.source_timetable and o.source_timetable.frequency == "BIWEEKLY")
            # 오늘 하루 시각 변경(정규 시간표와 다른지)
            time_changed = bool(o.source_timetable and o.source_timetable.start_time
                                and str(o.start_time)[:5] != str(o.source_timetable.start_time)[:5])
            lessons.append({
                "occ_id": o.id, "student_id": o.student_id, "student_name": _name_of(o.student),
                "start_time": str(o.start_time)[:5], "duration_minutes": o.duration_minutes,
                "time_changed": time_changed, "time_change_reason": (o.time_change_reason if time_changed else ""),
                "orig_time": (str(o.source_timetable.start_time)[:5] if (time_changed and o.source_timetable) else ""),
                "subject": o.subject or "미지정", "program": o.program or "",
                "instructor": _name_of(o.instructor) if o.instructor_id else "미배정",
                "instructor_id": o.instructor_id,
                "branch": (o.branch.name if o.branch_id else ""),
                "biweekly": biweekly, "is_makeup": o.is_makeup,
                "is_extra": o.is_extra, "extra_reason": o.extra_reason,
                "status": o.status, "lesson_note": o.note, "no_makeup": o.no_makeup,
                "no_makeup_kind": o.no_makeup_kind,
                "_absence_date": (str(o.makeup_for.date) if (o.is_makeup and o.makeup_for_id and o.makeup_for) else ""),
                "_absence_time": (str(o.makeup_for.start_time)[:5] if (o.is_makeup and o.makeup_for_id and o.makeup_for) else ""),
                "_absence_occid": (o.makeup_for_id if (o.is_makeup and o.makeup_for_id) else None),
                "school_type": (sp.school_type if sp else ""),
                "school_name": (sp.school_name if sp else ""),
                "grade": (sp.grade if sp else ""),
                "parent_phone": (sp.parent_phone if sp else ""),
                "student_phone": (sp.student_phone if sp else ""),
                "legacy_url": (sp.legacy_url if sp else ""),
            })
        att = {}
        for a in DailyAttendance.objects.filter(date=d, student_id__in=sids):
            att[a.student_id] = {"in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at),
                                 "note_tag": a.note_tag, "note": a.note}
        # 수업별 진도(있으면)
        prog = {}
        occ_ids = [l["occ_id"] for l in lessons]
        for p in LessonProgress.objects.filter(occurrence_id__in=occ_ids, is_hidden=False):
            prog[p.occurrence_id] = {"content": p.content, "homework": p.homework, "feedback": p.feedback, "memo": p.memo}
        for l in lessons:
            l["att"] = att.get(l["student_id"], {"in": "", "out": "", "note_tag": "", "note": ""})
            l["progress"] = prog.get(l["occ_id"])
        # 결석/조퇴예정 건에 연결된 보강의 진행상황(예정/완료/보강도 결석) — 표시 세분화용
        link_target_ids = [l["occ_id"] for l in lessons
                            if _link_target_kind(l["status"], l["att"]["note_tag"])]
        makeup_of = {m.makeup_for_id: m for m in LessonOccurrence.objects.filter(
            is_makeup=True, makeup_for_id__in=link_target_ids)} if link_target_ids else {}
        mk_keys = {(m.student_id, m.date) for m in makeup_of.values()}
        mk_att = {}
        if mk_keys:
            for a in DailyAttendance.objects.filter(
                    student_id__in={sid for sid, _ in mk_keys}, date__in={dt for _, dt in mk_keys}):
                if (a.student_id, a.date) in mk_keys:
                    mk_att[(a.student_id, a.date)] = a
        # 보강 쪽에서 "어떤 결석/조퇴예정을 메꾸는지" 표시하기 위해 연결 대상의 종류 판정
        target_ids = [l["_absence_occid"] for l in lessons if l.get("_absence_occid")]
        target_info = {}
        if target_ids:
            for t in LessonOccurrence.objects.filter(id__in=target_ids).only("id", "status", "student_id", "date"):
                target_info[t.id] = (t.status, t.student_id, t.date)
        target_att_tag = {}
        sd_keys = {(sid, dt) for _st, sid, dt in target_info.values()}
        if sd_keys:
            for a in DailyAttendance.objects.filter(
                    student_id__in={sid for sid, _ in sd_keys}, date__in={dt for _, dt in sd_keys}):
                if (a.student_id, a.date) in sd_keys:
                    target_att_tag[(a.student_id, a.date)] = a.note_tag
        for l in lessons:
            l["linked"] = None
            if link_target_ids and l["occ_id"] in link_target_ids:
                mk = makeup_of.get(l["occ_id"])
                if mk:
                    a = mk_att.get((mk.student_id, mk.date))
                    done = bool(a and a.check_in_at and a.check_out_at)
                    l["linked"] = {"kind": "makeup", "occ_id": mk.id, "date": str(mk.date), "start_time": str(mk.start_time)[:5],
                                   "status": mk.status, "done": done}
            elif l["_absence_date"]:
                tid = l["_absence_occid"]
                tinfo = target_info.get(tid)
                kind = "absence"
                if tinfo:
                    tstatus, tsid, tdate = tinfo
                    kind = _link_target_kind(tstatus, target_att_tag.get((tsid, tdate))) or "absence"
                l["linked"] = {"kind": kind, "occ_id": tid, "date": l["_absence_date"], "start_time": l["_absence_time"]}
            del l["_absence_date"], l["_absence_time"], l["_absence_occid"]
        # 임시휴원 — 기간이 끝나면 자동 복귀라 잊기 쉬워서 남은 일정을 같이 보여준다.
        # '연속된 한 구간'만 본다: 그 학생의 수업을 날짜순으로 훑다가 중간에 정상 수업이 끼면 거기서 끊는다
        # (하루씩 띄엄띄엄 쉬는 걸 하나로 묶으면 실제보다 길게 쉬는 것처럼 보임).
        # 아직 시작 전이면 시작 10일 전부터만 예고한다(주 1회 오는 학생도 등원 전에 미리 알 수 있게).
        TL_LEAD_DAYS = 10
        TL_LOOKBACK = 120   # 이미 시작된 휴원이 언제부터였는지 거슬러 볼 범위
        # 오늘 이후만 보면 안 된다. 임시휴원은 '수업이 있는 날'에만 표시가 붙는데, 오늘이
        # 그 학생 수업이 없는 요일이면 오늘 이후 첫 표시가 미래라서 이미 쉬는 중인데도
        # '예정'으로 나온다(유채원 7/24부터 쉬는데 8/14 예정으로 표시됨).
        tl_from = d - timedelta(days=TL_LOOKBACK)
        lv_q = LessonOccurrence.objects.filter(
            status=OccurrenceStatus.LEAVE, is_makeup=False, date__gte=tl_from)
        if view is not None:
            lv_q = lv_q.filter(branch_id__in=view)
        if bid:
            lv_q = lv_q.filter(branch_id=bid)
        lv_sids = set(lv_q.values_list("student_id", flat=True))
        by_student = {}
        if lv_sids:
            for o in LessonOccurrence.objects.filter(
                    student_id__in=lv_sids, is_makeup=False, date__gte=tl_from)\
                    .exclude(status=OccurrenceStatus.CANCELLED)\
                    .select_related("student", "student__userprofile"):
                by_student.setdefault(o.student_id, []).append(o)
        temp_leaves = []
        for sid, occs in by_student.items():
            occs.sort(key=lambda x: (x.date, x.start_time))
            # 오늘 이후로 이어질 휴원만 대상(이미 끝난 과거 휴원은 알릴 필요 없음)
            first = next((k for k, o in enumerate(occs)
                          if o.status == OccurrenceStatus.LEAVE and o.date >= d), None)
            if first is None:
                continue
            # 과거 쪽으로 이어지는지 확인해 실제 시작일을 찾는다(중간에 정상 수업이 있으면 멈춤)
            while first > 0 and occs[first - 1].status == OccurrenceStatus.LEAVE:
                first -= 1
            start = occs[first].date
            if (start - d).days > TL_LEAD_DAYS:
                continue
            end = next(k for k, o in enumerate(occs) if o is occs[first])
            while end + 1 < len(occs) and occs[end + 1].status == OccurrenceStatus.LEAVE:
                end += 1
            nxt = occs[end + 1] if end + 1 < len(occs) else None
            note = next((o.note for o in occs[first:end + 1] if o.note), "")
            temp_leaves.append({
                "student_id": sid, "name": _name_of(occs[first].student), "note": note,
                "start_date": str(start), "starts_in": (start - d).days, "upcoming": start > d,
                "last_date": str(occs[end].date), "days_left": (occs[end].date - d).days,
                # 남은 횟수(오늘 이후) — 이미 지나간 회차까지 세면 실제보다 많아 보인다
                "count": sum(1 for o in occs[first:end + 1] if o.date >= d),
                "back_date": str(nxt.date) if nxt else "",
                "back_in": (nxt.date - d).days if nxt else None,
            })
        temp_leaves.sort(key=lambda x: (x["upcoming"], x["last_date"]))

        # 수업외 등원(오늘 수업 없는데 등원 체크된 학생) 합성 행 추가
        adhoc_branch_ids = [int(bid)] if bid else view
        lessons.extend(_adhoc_lesson_rows(d, adhoc_branch_ids))
        lessons.sort(key=lambda x: x["start_time"])
        # 그날 상담 예약(KST 하루) — 위쪽 상담 일정 섹션용
        day_lo = _kst_to_utc(d, "00:00")
        day_hi = _kst_to_utc(d + timedelta(days=1), "00:00")
        rq = CounselReservation.objects.select_related("lead", "lead__branch").prefetch_related(
            "lead__logs__author").filter(
            status__in=["ACTIVE", "DONE"], scheduled_at__gte=day_lo, scheduled_at__lt=day_hi)
        if view is not None:
            rq = rq.filter(lead__branch_id__in=view)
        if bid:
            rq = rq.filter(lead__branch_id=bid)
        reservations = []
        for rv in rq.order_by("scheduled_at"):
            try:
                edits = _json.loads(rv.edit_log) if rv.edit_log else []
            except (ValueError, TypeError):
                edits = []
            lg = rv.lead
            logs = [{"author": _name_of(c.author) if c.author_id else "",
                     "channel": c.channel, "summary": c.summary,
                     "time": _kst_dt_str(c.counsel_at or c.create_time)}
                    for c in lg.logs.all() if not c.is_hidden][:6]
            reservations.append({
                "id": rv.id, "lead_id": rv.lead_id, "time": _hm_kst(rv.scheduled_at),
                "student_name": lg.student_name, "parent_name": lg.parent_name,
                "branch": (lg.branch.name if lg.branch_id else ""), "note": rv.note,
                "channel": rv.channel, "status": rv.status,
                "school_type": lg.school_type, "school_name": lg.school_name, "grade": lg.grade,
                "parent_phone": lg.parent_phone, "logs": logs, "edits": edits})
        # 앞으로 있을 상담 예약 — 내일부터 이레. 토요일에 보면 다음 주 금요일까지 들어와
        # "다음 주에 상담 있네" 를 미리 알 수 있다. 오늘 것은 위 칸에 따로 있어 뺀다.
        nq = CounselReservation.objects.select_related("lead", "lead__branch").filter(
            status="ACTIVE", scheduled_at__gte=day_hi,
            scheduled_at__lt=_kst_to_utc(d + timedelta(days=8), "00:00"))
        if view is not None:
            nq = nq.filter(lead__branch_id__in=view)
        if bid:
            nq = nq.filter(lead__branch_id=bid)
        next_resv = []
        for rv in nq.order_by("scheduled_at"):
            when = (rv.scheduled_at + timedelta(hours=9)).date()
            lg = rv.lead
            next_resv.append({
                "id": rv.id, "lead_id": rv.lead_id,
                "date": str(when), "wd": _WD[when.weekday()], "time": _hm_kst(rv.scheduled_at),
                "d_day": (when - d).days,
                "student_name": lg.student_name, "parent_name": lg.parent_name,
                "parent_phone": lg.parent_phone, "note": rv.note, "channel": rv.channel,
                "branch": (lg.branch.name if lg.branch_id else "")})

        # 자격증·대회 — 놓치면 못 돌이키는 일이라 미리 알린다. 한 달 앞까지 본다.
        # 같은 시험·같은 날·같은 상태면 한 줄에 이름을 늘어놓는다. 한 명씩 줄이 생기면
        # 여덟 명짜리 대회 하나가 화면을 다 차지한다.
        from ..models import ExamEntry, ExamStage, EXAM_STAGE_CHOICES
        eq2 = ExamEntry.objects.filter(is_deleted=False).exclude(
            stage__in=[ExamStage.JOIN_NO, ExamStage.DONE]
        ).select_related("student", "student__userprofile", "session", "session__catalog",
                         "catalog", "student__academy_profile", "student__academy_profile__branch")
        _STAGE = dict(EXAM_STAGE_CHOICES)
        exam_grp = {}
        for e in eq2:
            ap = e.apply_until or (e.session.apply_until if e.session_id else None)
            ex = e.exam_date or (e.session.exam_date if e.session_id else None)
            cat = e.catalog or (e.session.catalog if e.session_id else None)
            prof2 = getattr(e.student, "academy_profile", None)
            bid2 = prof2.branch_id if prof2 else None
            if view is not None and bid2 not in view:
                continue
            if bid and str(bid2) != str(bid):
                continue
            title = (cat.name if cat else "") or (e.session.title if e.session_id else "") or "시험"
            detail = " ".join(x for x in (e.level, e.track, e.round) if x)
            stage = _STAGE.get(e.stage, e.stage)
            who = {"student_id": e.student_id, "name": _name_of(e.student)}
            bn = (prof2.branch.name if prof2 and prof2.branch_id else "")

            def _add(kind, label, day, extra=""):
                key = (kind, str(day), title, detail, stage, bn)
                g = exam_grp.setdefault(key, {
                    "kind": kind, "label": label, "date": str(day), "wd": _WD[day.weekday()],
                    "d_day": (day - d).days, "title": title, "detail": detail,
                    "stage": stage, "branch": bn, "time": extra, "people": []})
                g["people"].append(who)

            if ap and 0 <= (ap - d).days <= 30 and not e.applied:
                _add("apply", "접수 마감", ap)
            if ex and 0 <= (ex - d).days <= 30:
                _add("exam", "시험일", ex,
                     e.exam_time or (e.session.exam_time if e.session_id else ""))
        exam_soon = sorted(exam_grp.values(),
                           key=lambda x: (x["d_day"], x["kind"] != "apply", x["title"]))
        for g in exam_soon:
            g["people"].sort(key=lambda w: w["name"])
        # 그날 보는 시험 — 아래 수업 목록에 시각 순으로 끼워 넣는다. 시험도 시각이 있으니
        # 수업과 같은 줄에 섞여야 그날 흐름이 한눈에 보인다.
        exam_today = []
        for e in eq2:
            ex = e.exam_date or (e.session.exam_date if e.session_id else None)
            if ex != d:
                continue
            prof2 = getattr(e.student, "academy_profile", None)
            bid2 = prof2.branch_id if prof2 else None
            if view is not None and bid2 not in view:
                continue
            if bid and str(bid2) != str(bid):
                continue
            cat = e.catalog or (e.session.catalog if e.session_id else None)
            title = (cat.name if cat else "") or (e.session.title if e.session_id else "") or "시험"
            tm = e.exam_time or (e.session.exam_time if e.session_id else "") or ""
            key = (tm, title, e.level, e.track, e.round)
            g = next((x for x in exam_today if x["_k"] == key), None)
            if not g:
                g = {"_k": key, "time": tm, "title": title,
                     "detail": " ".join(x for x in (e.level, e.track, e.round) if x),
                     "kind": (e.session.kind if e.session_id else "CERT"),
                     "place": e.place or (e.session.place if e.session_id else ""),
                     "people": []}
                exam_today.append(g)
            g["people"].append({"student_id": e.student_id, "name": _name_of(e.student),
                                "entry_id": e.id, "result_done": bool(e.result_at),
                                "result": e.result, "score": e.score})
        for g in exam_today:
            g.pop("_k", None)
            g["people"].sort(key=lambda w: w["name"])
            g["left"] = sum(1 for w in g["people"] if not w["result_done"])
        exam_today.sort(key=lambda x: (x["time"] or "99:99", x["title"]))

        # 학부모 등록 링크 작성 완료(등록 전환 전까지 계속 표시, 날짜와 무관)
        eq = Lead.objects.select_related("branch").filter(
            enroll_status="SUBMITTED").exclude(status=LeadStatus.CONVERTED)
        if view is not None:
            eq = eq.filter(branch_id__in=view)
        if bid:
            eq = eq.filter(branch_id=bid)
        enrolled = [{"id": l.id, "student_name": l.student_name, "parent_name": l.parent_name,
                    "branch": (l.branch.name if l.branch_id else ""),
                    "submitted_at": str(l.enroll_submitted_at + timedelta(hours=9))[:16] if l.enroll_submitted_at else "",
                    "enroll_edited": l.enroll_edited}
                   for l in eq.order_by("-enroll_edited", "-enroll_submitted_at")[:100]]
        WD = ["월", "화", "수", "목", "금", "토", "일"]
        # 지각 기준은 화면 설정값(기본 5분)이라 프론트가 알려준다
        try:
            late_min = max(0, min(60, int(request.GET.get("late_min") or 5)))
        except (TypeError, ValueError):
            late_min = 5
        extra = _dash_student_extra(d, lessons, late_min)
        # 다음 수업일 보강 — 그날 오는 학생에게 미리 안내하려고 본다. 내일이 휴무일이거나
        # 문 안 여는 요일이면 그다음 날로 넘어가며 찾는다.
        bids = [bid] if bid else (list(view) if view is not None
                                  else list(Branch.objects.values_list("id", flat=True)))
        nxt, no_ws = _next_open_day(d, bids)
        makeups = []
        if nxt:
            mq = LessonOccurrence.objects.filter(
                date=nxt, is_makeup=True).exclude(
                status=OccurrenceStatus.CANCELLED).select_related(
                "student", "student__userprofile", "branch").order_by("start_time")
            if bids:
                mq = mq.filter(branch_id__in=bids)
            makeups = [{"id": o.id, "student_id": o.student_id,
                        "name": _name_of(o.student), "time": str(o.start_time)[:5],
                        "subject": o.subject or "",
                        "branch": (o.branch.name if o.branch_id else "")}
                       for o in mq[:100]]
        return self.success({"date": str(d), "weekday": WD[wd], "lessons": lessons,
                             "student_extra": extra,
                             "total": len(lessons), "present": len(att), "reservations": reservations,
                             "enrolled_leads": enrolled, "temp_leaves": temp_leaves,
                             "next_reservations": next_resv,
                             "exam_soon": exam_soon, "exam_today": exam_today,
                             "cal_today": _cal_today(request.user, d, view, bid),
                             "board_unread": _board_unread(request.user),
                             "next_open_day": (str(nxt) if nxt else ""),
                             "next_makeups": makeups,
                             "no_work_schedule": no_ws,
                             "holidays": holiday_names})


def kst_today_admin():
    return (now() + timedelta(hours=9)).date()


def apply_due_status(branch_ids=None):
    """적용일이 된 예약을 반영한다.

    크론은 안 돌면 영영 안 바뀌어 위험하다. 매일 누군가 접속하므로 그때 확인한다 —
    아무도 안 들어온 날은 어차피 아무 일도 일어나지 않는다."""
    today = (now() + timedelta(hours=9)).date()
    qs = StudentProfile.objects.exclude(pending_status="").filter(pending_date__lte=today)
    done = 0
    for sp in qs.select_related("user"):
        frm = sp.enrollment_status
        to = sp.pending_status
        eff = sp.pending_date
        rsn = sp.pending_reason
        sp.enrollment_status = to
        sp.pending_status = ""
        sp.pending_date = None
        sp.pending_reason = ""
        sp.save(update_fields=["enrollment_status", "pending_status", "pending_date", "pending_reason"])
        if frm != to:
            StudentStatusChange.objects.create(
                student_id=sp.user_id, from_status=frm, to_status=to,
                reason=(rsn + " (예약 적용)").strip(), effective_date=eff, actor=None)
            # 예약할 때는 기간만 끊어 뒀다. 오늘이 그날이므로 이제 상태를 바꾼다.
            from ..models import TimetableStatus
            if to == EnrollmentStatus.ON_LEAVE:
                StudentTimetable.objects.filter(student_id=sp.user_id,
                                                status=TimetableStatus.ACTIVE,
                                                active_until__lt=eff).update(status=TimetableStatus.PAUSED)
            elif to == EnrollmentStatus.WITHDRAWN:
                StudentTimetable.objects.filter(student_id=sp.user_id,
                                                active_until__lt=eff) \
                                        .exclude(status=TimetableStatus.ENDED) \
                                        .update(status=TimetableStatus.ENDED)
            done += 1
    return done


_TT_ONEDAY = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}(?!부터)")


def _tt_change_kind(detail):
    """이력 한 줄이 무엇을 바꾼 것인지. 정규 시간표와 그날만 바꾼 것이 섞여 있어
    무엇을 보고 있는지 알 수 없었다.

    regular  정규 시간표(신규·적용일부터 변경·삭제)  — 앞으로 계속 그렇게 온다
    oneday   그날만                                — 그 날짜 하루만
    makeup   보강 만들기·취소
    absent   결석 처리
    """
    d = detail or ""
    if "보강 생성" in d or "보강 취소" in d or "보강 날짜" in d:
        return "makeup"
    if "결석 처리" in d or "예정으로 복원" in d:
        return "absent"
    if "부터 시간표 변경" in d or d.endswith("신규 등록") or "삭제" in d or "복원" in d:
        return "regular"
    if _TT_ONEDAY.match(d):
        return "oneday"
    return "regular"


def _next_open_day(d, branch_ids, limit=21):
    """d 다음으로 문을 여는 날. 휴무일과 '안 여는 요일'을 건너뛴다.

    안 여는 요일은 지점 근무 기준(WorkSchedule)의 지점 기본값에서 읽는다. 기본값이 없는
    지점은 요일로 거르지 못하므로 그 지점 이름을 함께 돌려주어 화면이 알리게 한다.
    (없는 지점을 조용히 '매일 연다'고 보면 쉬는 날을 짚어 준다.)"""
    if not branch_ids:
        return None, []
    # 학생이 없는 지점은 기준이 없어도 알릴 까닭이 없다(고양·파주처럼 아직 안 여는 곳)
    enrolled = set(StudentProfile.objects.filter(
        enrollment_status=EnrollmentStatus.ENROLLED).values_list("user_id", flat=True))
    live = set(AcademyProfile.objects.filter(
        is_deleted=False, role=AcademyRole.STUDENT,
        branch_id__in=branch_ids, user_id__in=enrolled).values_list("branch_id", flat=True))
    open_wd, no_ws = {}, []
    for b in Branch.objects.filter(id__in=branch_ids):
        ws = WorkSchedule.objects.filter(
            branch_id=b.id, staff__isnull=True, active_from__lte=d).filter(
            Q(active_until__isnull=True) | Q(active_until__gte=d)).order_by("-active_from", "-id").first()
        if ws:
            open_wd[b.id] = {int(c) for c in (ws.workdays or "") if c.isdigit()}
        else:
            open_wd[b.id] = None          # 모름 — 요일로 거르지 않는다
            if b.id in live:
                no_ws.append(b.name)
    day = d
    for _ in range(limit):
        day += timedelta(days=1)
        hol = set(Holiday.objects.filter(date=day, is_deleted=False).values_list("branch_id", flat=True))
        for bid_ in branch_ids:
            if bid_ in hol or None in hol:      # 그 지점 휴무 또는 전 지점 공통 휴무
                continue
            wds = open_wd.get(bid_)
            if wds is not None and day.weekday() not in wds:
                continue
            return day, no_ws                   # 한 지점이라도 열면 그날이 다음 수업일
    return None, no_ws


def _kst_to_utc(d, hm):
    """KST 날짜 d + 'HH:MM'을 저장용 UTC aware datetime으로."""
    from datetime import time as _t
    from django.utils import timezone as _tz
    hh, mm = hm.split(":")
    naive = datetime.combine(d, _t(int(hh), int(mm))) - timedelta(hours=9)
    return _tz.make_aware(naive, _tz.utc)


def _parse_kst_local_dt(s):
    """프론트 composeAt()이 보내는 'YYYY-MM-DDTHH:MM'(KST 벽시계, tz 표기 없음) 문자열을
    진짜 UTC aware datetime으로 변환. 사용자가 직접 고른 시각(상담 일시 등)을 저장할 때 항상 거쳐야
    한다 — 그대로 저장하면 Django가 TIME_ZONE=UTC로 오인해 9시간 어긋난다."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        day_s, time_s = s.replace(" ", "T").split("T")[:2]
        d = datetime.strptime(day_s, "%Y-%m-%d").date()
        return _kst_to_utc(d, time_s[:5])
    except (ValueError, AttributeError):
        return None


class AttendanceCheckAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """등원/하원 체크/수정. {student_id, kind:'in'|'out', date?, clear?, time?'HH:MM', reason?}"""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            d = datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else now().date()
        except (TypeError, ValueError):
            d = now().date()
        a, _ = DailyAttendance.objects.get_or_create(
            student=u, date=d, defaults={"branch": (prof.branch if prof and prof.branch_id else None)})
        kind = data.get("kind")
        if kind not in ("in", "out"):
            return self.error("kind 값이 올바르지 않습니다.")
        clear = bool(data.get("clear"))
        tm = (data.get("time") or "").strip()
        field = "check_in_at" if kind == "in" else "check_out_at"
        label = "등원" if kind == "in" else "하원"
        if kind == "out" and not clear and not a.check_in_at:
            return self.error("등원 기록이 없습니다. 등원을 먼저 넣어 주세요.")
        # 등원을 지우면 하원만 남아 버린다 — 들어온 적 없는데 나간 기록이라 뜻이 없고,
        # 화면에서 하원 칸이 눌리지 않아 지울 수도 없게 된다. 둘을 함께 지운다.
        cascade = bool(data.get("cascade"))
        if kind == "in" and clear and a.check_out_at and not cascade:
            return self.error("하원 %s 기록이 함께 있습니다. 둘 다 지울지 확인이 필요합니다."
                              % _hm_kst(a.check_out_at))
        # 지난 날짜에 '지금'을 찍으면 8월 4일 기록에 8월 14일 시각이 들어간다.
        # 그날이 아니면 시각을 반드시 받는다(화면이 수업 시각을 채워 준다).
        if not clear and not tm and d != kst_today_admin():
            return self.error("지난 날짜는 시각을 함께 넣어 주세요.")
        old = _hm_kst(getattr(a, field))
        old_out_c = _hm_kst(a.check_out_at)
        if clear:
            setattr(a, field, None)
            if kind == "in" and cascade:
                a.check_out_at = None
        elif tm:
            try:
                setattr(a, field, _kst_to_utc(d, tm))
            except (ValueError, AttributeError):
                return self.error("시간 형식이 올바르지 않습니다(HH:MM).")
        else:
            setattr(a, field, now())
        a.save()
        new = _hm_kst(getattr(a, field))
        # 발생 경로까지 포함해 모든 변경을 이력에 기록(키오스크 체크와 구분 — 누가 눌렀는지 추적용)
        if old != new:
            if clear:
                detail = "%s 체크 취소 (%s → -) (포털 수동)" % (label, old or "-")
                if kind == "in" and cascade and old_out_c:
                    detail += " · 하원 %s 도 함께 삭제" % old_out_c
            elif old:
                detail = "%s %s → %s (포털 수동)" % (label, old, new)
            else:
                detail = "%s 체크 %s (포털 수동)" % (label, new)
            AttendanceChange.objects.create(
                attendance=a, actor=request.user, detail=detail,
                reason=(data.get("reason") or "").strip())
        return self.success({"in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at)})


class AttendanceNoteAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """출결 비고(표시 태그 + 사유). {student_id, date?, note_tag, note}"""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            d = datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else now().date()
        except (TypeError, ValueError):
            d = now().date()
        a, _ = DailyAttendance.objects.get_or_create(
            student=u, date=d, defaults={"branch": (prof.branch if prof and prof.branch_id else None)})
        old_tag, old_note = a.note_tag, a.note
        a.note_tag = (data.get("note_tag") or "").strip()
        a.note = (data.get("note") or "").strip()
        a.save()
        if (old_tag, old_note) != (a.note_tag, a.note):
            old_label = _opt_label("attendance_note", old_tag) if old_tag else "없음"
            new_label = _opt_label("attendance_note", a.note_tag) if a.note_tag else "없음"
            detail = "비고 %s → %s" % (old_label, new_label)
            if a.note:
                detail += " (%s)" % a.note
            AttendanceChange.objects.create(attendance=a, actor=request.user, detail=detail, reason="")
        return self.success({"note_tag": a.note_tag, "note": a.note})

    @admin_role_required
    def get(self, request):
        """출결 변경 이력. student_id, date. 열람은 원장 이상만 가능."""
        if not _is_director_up(request.user):
            return self.error("권한이 없습니다.")
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        try:
            d = datetime.strptime(request.GET.get("date"), "%Y-%m-%d").date() if request.GET.get("date") else now().date()
        except (TypeError, ValueError):
            d = now().date()
        a = DailyAttendance.objects.filter(student=u, date=d).first()
        # 같은 분에 여러 건이 몰리면(두 사람이 거의 동시에 조작) 순서를 알 수 없어 초까지 표시한다.
        def _sec(dt):
            return str(dt + timedelta(hours=9))[:19] if dt else ""
        out = []
        if a:
            for c in a.changes.select_related("actor"):
                out.append({"detail": c.detail, "reason": c.reason,
                            "actor": _name_of(c.actor) if c.actor_id else "",
                            "time": _sec(c.create_time)})
        # 그 날짜 수업(정규 하루/보강) 시각·강사·과정 변경 및 보강 생성 이력도 같이 표시
        for c in TimetableChange.objects.filter(student=u, detail__startswith=str(d)).select_related("actor"):
            out.append({"detail": c.detail, "reason": c.reason,
                        "actor": _name_of(c.actor) if c.actor_id else "",
                        "time": _sec(c.create_time)})
        out.sort(key=lambda x: x["time"], reverse=True)  # 최신이 위로
        return self.success(out)


def _ensure_kiosk_token(branch):
    """지점별 출결 키오스크 접속 토큰(무로그인). 없으면 새로 발급."""
    if not branch.kiosk_token:
        branch.kiosk_token = secrets.token_urlsafe(18)[:32]
        branch.save(update_fields=["kiosk_token"])
    return branch.kiosk_token


class KioskUrlAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """출결 키오스크 화면 URL 조회(없으면 토큰 자동 발급). branch_id="""
        branch = Branch.objects.filter(id=request.GET.get("branch_id")).first()
        if not branch:
            return self.error("지점을 찾을 수 없습니다.")
        if not can_view_branch(request.user, branch.id):
            return self.error("권한이 없습니다.")
        token = _ensure_kiosk_token(branch)
        return self.success({"url": "/portal/kiosk.html?b=%d&t=%s" % (branch.id, token)})

    @admin_role_required
    def post(self, request):
        """출결 키오스크 토큰 재발급(유출 등 사유로 링크 무효화). {branch_id}"""
        branch = Branch.objects.filter(id=request.data.get("branch_id")).first()
        if not branch:
            return self.error("지점을 찾을 수 없습니다.")
        if not can_manage_branch(request.user, branch.id):
            return self.error("권한이 없습니다.")
        branch.kiosk_token = secrets.token_urlsafe(18)[:32]
        branch.save(update_fields=["kiosk_token"])
        return self.success({"url": "/portal/kiosk.html?b=%d&t=%s" % (branch.id, branch.kiosk_token)})


class KioskDeviceListAPI(APIView):
    @admin_role_required
    def get(self, request):
        """출결 키오스크 기기(브라우저) 목록. 원장 이상만. branch_id="""
        branch = Branch.objects.filter(id=request.GET.get("branch_id")).first()
        if not branch:
            return self.error("지점을 찾을 수 없습니다.")
        if not _is_director_up(request.user):
            return self.error("원장 이상만 관리할 수 있습니다.")
        if not can_manage_branch(request.user, branch.id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        rows = [{"id": d.id, "device_id": d.device_id[:8], "label": d.label, "user_agent": d.user_agent,
                "status": d.status, "requested_at": _kst_dt_str(d.requested_at),
                "approved_at": _kst_dt_str(d.approved_at) if d.approved_at else "",
                "approved_by": _name_of(d.approved_by) if d.approved_by_id else ""}
                for d in KioskDevice.objects.filter(branch=branch).select_related("approved_by")]
        return self.success(rows)


class KioskPinAdminAPI(APIView):
    """키오스크 진입 PIN(6자리) 조회/설정. 원장 이상 + 그 지점 관리자만.
    포털 첫 화면에서 이 PIN을 누르면 지점이 자동 선택돼 기기 등록 신청으로 이어진다."""
    @admin_role_required
    def get(self, request):
        branch = Branch.objects.filter(id=request.GET.get("branch_id")).first()
        if not branch:
            return self.error("지점을 찾을 수 없습니다.")
        if not _is_director_up(request.user) or not can_manage_branch(request.user, branch.id):
            return self.error("권한이 없습니다.")
        return self.success({"pin": branch.kiosk_pin})

    @admin_role_required
    def post(self, request):
        branch = Branch.objects.filter(id=request.data.get("branch_id")).first()
        if not branch:
            return self.error("지점을 찾을 수 없습니다.")
        if not _is_director_up(request.user) or not can_manage_branch(request.user, branch.id):
            return self.error("권한이 없습니다.")
        pin = "".join(ch for ch in (request.data.get("pin") or "") if ch.isdigit())
        if pin and len(pin) != 6:
            return self.error("PIN은 숫자 6자리로 입력하세요.")
        if pin and Branch.objects.filter(kiosk_pin=pin).exclude(id=branch.id).exists():
            return self.error("다른 지점이 쓰고 있는 PIN입니다. 다른 번호로 지정하세요.")
        branch.kiosk_pin = pin
        branch.save(update_fields=["kiosk_pin"])
        return self.success({"pin": branch.kiosk_pin})


class KioskDeviceActionAPI(APIView):
    @admin_role_required
    def post(self, request):
        """기기 승인/삭제(취소)/이름변경. 원장 이상만. {id, action:'approve'|'revoke'|'label', label?}"""
        d = KioskDevice.objects.filter(id=request.data.get("id")).first()
        if not d:
            return self.error("기기를 찾을 수 없습니다.")
        if not _is_director_up(request.user):
            return self.error("원장 이상만 관리할 수 있습니다.")
        if not can_manage_branch(request.user, d.branch_id):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        action = request.data.get("action")
        if action == "approve":
            d.status = KioskDeviceStatus.APPROVED
            d.approved_at = now()
            d.approved_by = request.user
            if request.data.get("label"):
                d.label = (request.data.get("label") or "").strip()
            d.save()
        elif action == "revoke":
            d.status = KioskDeviceStatus.REVOKED
            d.save(update_fields=["status"])
        elif action == "label":
            d.label = (request.data.get("label") or "").strip()
            d.save(update_fields=["label"])
        else:
            return self.error("action 값이 올바르지 않습니다.")
        return self.success({"status": d.status, "label": d.label})


def _student_weekday_map(student_ids, on):
    """학생마다 오늘 적용중인 시간표의 요일 묶음. 요일을 옮겨 끊긴 옛 줄은 빼야
    주1회가 주2회로 둔갑하지 않는다."""
    out = {}
    for t in StudentTimetable.objects.filter(student_id__in=student_ids).exclude(status="ENDED"):
        if not _slot_active_on(t, on):
            continue
        out.setdefault(t.student_id, set()).add(t.weekday)
    return {k: ", ".join(_WD[w] for w in sorted(v)) for k, v in out.items()}


class TimetableCalendarAPI(APIView):
    @admin_role_required
    def get(self, request):
        """월간/주간 달력용 일자별 수업 집계. month=YYYY-MM 또는 from/to.
        student_id 주면 해당 학생만(보강 추가 달력용). program/instructor_id/branch_id 필터.
        각 항목에 결석 상태(status)·occ_id를 함께 내려 달력에서 결석 지정 가능."""
        view = viewable_branch_ids(request.user)
        sid = request.GET.get("student_id")
        m = request.GET.get("month")
        if m:
            y, mo = m.split("-")
            d0 = date_cls(int(y), int(mo), 1)
            d1 = date_cls(int(y) + (mo == "12"), (int(mo) % 12) + 1, 1) - timedelta(days=1)
        else:
            try:
                d0 = datetime.strptime(request.GET.get("from"), "%Y-%m-%d").date()
                d1 = datetime.strptime(request.GET.get("to"), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                d0 = now().date().replace(day=1)
                d1 = d0 + timedelta(days=30)
        # 패턴 시간표를 기간 내 날짜로 펼침(생성 없이 계산만). 휴원/퇴원(PAUSED/ENDED)이라도 이미
        # 스냅샷(overlay)이 있는 과거 날짜는 그대로 보여줘야 하므로 상태로 미리 거르지 않고,
        # 실제 표시 여부(미래 가상 일정 투영 여부)는 아래 날짜 루프에서 판단.
        slots = StudentTimetable.objects.select_related(
            "branch", "student", "student__student_profile", "instructor").all()
        if sid:
            slots = slots.filter(student_id=sid)
        if view is not None:
            slots = slots.filter(branch_id__in=view)
        bid = request.GET.get("branch_id")
        if bid:
            slots = slots.filter(branch_id=bid)
        prog = request.GET.get("program")
        if prog:
            slots = slots.filter(program=prog)
        # 강사 다중 선택: instructor_ids=1,2,__none__ (기존 단일 instructor_id도 계속 지원)
        instr_ids = [v for v in (request.GET.get("instructor_ids") or "").split(",") if v]
        instr = request.GET.get("instructor_id")
        if instr:
            instr_ids = [instr]
        if instr_ids:
            q = Q()
            real_ids = [v for v in instr_ids if v != "__none__"]
            if real_ids:
                q |= Q(instructor_id__in=real_ids)
            if "__none__" in instr_ids:
                q |= Q(instructor__isnull=True)
            slots = slots.filter(q)
        slots = list(slots)
        # 기간 내 인스턴스 오버레이(결석/취소 상태·occ_id·비고 사유·당일 시각/강사/과정 변경분)
        occ_q = LessonOccurrence.objects.filter(date__gte=d0, date__lte=d1, source_timetable__isnull=False)
        if sid:
            occ_q = occ_q.filter(student_id=sid)
        overlay = {}
        for o in occ_q.values("source_timetable_id", "date", "status", "id", "note", "no_makeup", "no_makeup_kind",
                               "start_time", "duration_minutes", "instructor_id", "program", "subject", "student_id"):
            overlay[(o["source_timetable_id"], str(o["date"]))] = o
        ov_instr_ids = {o["instructor_id"] for o in overlay.values() if o["instructor_id"]}
        ov_instr_names = {u.id: _name_of(u) for u in User.objects.filter(id__in=ov_instr_ids)}
        # 등원/하원 출결(오늘 운영과 동일하게 달력에도 표시)
        att_q = DailyAttendance.objects.filter(date__gte=d0, date__lte=d1)
        if sid:
            att_q = att_q.filter(student_id=sid)
        att_map = {}
        for a in att_q:
            att_map[(a.student_id, str(a.date))] = {"in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at),
                                                     "note_tag": a.note_tag, "note": a.note}
        # 결석/조퇴예정 건에 연결된 보강의 진행상황(예정/완료/보강도 결석) — 표시 세분화용
        link_target_ids = [o["id"] for o in overlay.values()
                            if _link_target_kind(o["status"], att_map.get((o["student_id"], str(o["date"])), {}).get("note_tag", ""))]
        makeup_of = {m.makeup_for_id: m for m in LessonOccurrence.objects.filter(
            is_makeup=True, makeup_for_id__in=link_target_ids)} if link_target_ids else {}
        mk_keys = {(m.student_id, m.date) for m in makeup_of.values()}
        mk_att = {}
        if mk_keys:
            for a in DailyAttendance.objects.filter(
                    student_id__in={sid_ for sid_, _ in mk_keys}, date__in={dt for _, dt in mk_keys}):
                if (a.student_id, a.date) in mk_keys:
                    mk_att[(a.student_id, a.date)] = a

        def _linked_for(ov):
            if not ov:
                return None
            kind = _link_target_kind(ov["status"], att_map.get((ov["student_id"], str(ov["date"])), {}).get("note_tag", ""))
            if not kind:
                return None
            mk = makeup_of.get(ov["id"])
            if not mk:
                return None
            a = mk_att.get((mk.student_id, mk.date))
            done = bool(a and a.check_in_at and a.check_out_at)
            return {"kind": "makeup", "occ_id": mk.id, "date": str(mk.date), "start_time": str(mk.start_time)[:5], "status": mk.status, "done": done}
        # 진도(있으면) occ_id별
        prog_q = LessonProgress.objects.filter(
            occurrence__date__gte=d0, occurrence__date__lte=d1, is_hidden=False)
        if sid:
            prog_q = prog_q.filter(student_id=sid)
        prog_by_occ = {p["occurrence_id"]: {"content": p["content"], "homework": p["homework"], "feedback": p["feedback"], "memo": p["memo"]}
                       for p in prog_q.values("occurrence_id", "content", "homework", "feedback", "memo")}
        days = {}
        cur = d0
        while cur <= d1:
            wd = cur.weekday()
            items = []
            for s in slots:
                if s.weekday != wd:
                    continue
                ov = overlay.get((s.id, str(cur)))
                if ov is None:
                    # 실제 스냅샷(이미 있었던 기록)이 없는 가상 투영 일정은, 지금 활성 패턴이고
                    # 유효기간 내일 때만 보여줌(휴원/퇴원 이후 미래 날짜에 안 보이게)
                    if s.status != "ACTIVE" or not _slot_active_on(s, cur):
                        continue
                elif ov["status"] == OccurrenceStatus.CANCELLED:
                    continue
                ov_program = (ov["program"] if ov else "") or s.program
                sp = getattr(s.student, "student_profile", None)
                items.append({"timetable_id": s.id,
                              "start_time": (str(ov["start_time"])[:5] if ov else str(s.start_time)[:5]),
                              "date": str(cur),
                              "duration_minutes": (ov["duration_minutes"] if ov else s.duration_minutes),
                              "subject": ((ov["subject"] if ov else "") or s.subject or
                                          resolve_program_label(s.program) or "미지정"),
                              "weekday": s.weekday, "program": ov_program,
                              "student_id": s.student_id, "student_name": _name_of(s.student),
                              "school_type": (sp.school_type if sp else ""),
                              "school_name": (sp.school_name if sp else ""),
                              "grade": (sp.grade if sp else ""),
                              "legacy_url": (sp.legacy_url if sp else ""),
                              "instructor": (ov_instr_names.get(ov["instructor_id"], "미배정") if ov
                                             else (_name_of(s.instructor) if s.instructor_id else "미배정")),
                              "instructor_id": (ov["instructor_id"] if ov else s.instructor_id),
                              "branch": (s.branch.name if s.branch_id else ""), "branch_id": s.branch_id,
                              "frequency": s.frequency,
                              "status": (ov["status"] if ov else OccurrenceStatus.SCHEDULED),
                              "occ_id": (ov["id"] if ov else None),
                              "lesson_note": (ov["note"] if ov else ""),
                              "no_makeup": bool(ov["no_makeup"]) if ov else False,
                              "no_makeup_kind": (ov["no_makeup_kind"] if ov else ""),
                              "linked": _linked_for(ov),
                              "att": att_map.get((s.student_id, str(cur)), {"in": "", "out": "", "note_tag": "", "note": ""}),
                              "progress": (prog_by_occ.get(ov["id"]) if ov else None)})
            # 보강(makeup) 인스턴스 + 시간표가 삭제돼 소속이 없어진 수업(지난 등원 이력)도 포함.
            # 후자를 빼면 시간표를 지운 순간 그 학생의 지난 수업이 달력에서만 통째로 사라져
            # '오늘 운영'·출결기록과 안 맞는다.
            mk = LessonOccurrence.objects.select_related(
                "student", "student__student_profile", "instructor", "makeup_for").filter(
                date=cur).filter(Q(is_makeup=True) | Q(source_timetable__isnull=True))\
                .exclude(status=OccurrenceStatus.CANCELLED)
            if sid:
                mk = mk.filter(student_id=sid)
            if view is not None:
                mk = mk.filter(branch_id__in=view)
            if bid:
                mk = mk.filter(branch_id=bid)
            for o in mk:
                linked = None
                if o.makeup_for_id and o.makeup_for:
                    t = o.makeup_for
                    t_att = DailyAttendance.objects.filter(student_id=t.student_id, date=t.date).first()
                    t_kind = _link_target_kind(t.status, t_att.note_tag if t_att else "") or "absence"
                    linked = {"kind": t_kind, "occ_id": o.makeup_for_id, "date": str(t.date),
                              "start_time": str(t.start_time)[:5]}
                o_sp = getattr(o.student, "student_profile", None)
                items.append({"timetable_id": None, "start_time": str(o.start_time)[:5], "date": str(cur),
                              "duration_minutes": o.duration_minutes,
                              "subject": (o.subject or ("보강" if o.is_makeup else "미지정")),
                              "program": o.program or "", "makeup": o.is_makeup,
                              "student_id": o.student_id, "student_name": _name_of(o.student),
                              "school_type": (o_sp.school_type if o_sp else ""),
                              "school_name": (o_sp.school_name if o_sp else ""),
                              "grade": (o_sp.grade if o_sp else ""),
                              "legacy_url": (o_sp.legacy_url if o_sp else ""),
                              "instructor": _name_of(o.instructor) if o.instructor_id else "미배정",
                              "instructor_id": o.instructor_id,
                              "status": o.status, "occ_id": o.id, "lesson_note": o.note, "linked": linked,
                              "att": att_map.get((o.student_id, str(cur)), {"in": "", "out": "", "note_tag": "", "note": ""}),
                              "progress": prog_by_occ.get(o.id)})
            if items:
                items.sort(key=lambda x: x["start_time"])
                days[str(cur)] = {"count": len(items), "items": items}
            cur += timedelta(days=1)
        # 상담 예약(달력에 함께 표시) — 기간 내 ACTIVE 예약을 KST 날짜로 버킷
        resv = {}
        if not sid:  # 학생 단건(보강 달력)에서는 예약 제외
            rq = CounselReservation.objects.select_related("lead", "lead__branch").filter(
                status="ACTIVE",
                scheduled_at__gte=_kst_to_utc(d0, "00:00"),
                scheduled_at__lt=_kst_to_utc(d1 + timedelta(days=1), "00:00"))
            if view is not None:
                rq = rq.filter(lead__branch_id__in=view)
            if bid:
                rq = rq.filter(lead__branch_id=bid)
            for rv in rq.order_by("scheduled_at"):
                ds = str((rv.scheduled_at + timedelta(hours=9)).date())
                resv.setdefault(ds, []).append({
                    "id": rv.id, "time": _hm_kst(rv.scheduled_at),
                    "student_name": rv.lead.student_name, "note": rv.note})
        # 휴무일 — 그날 수업 목록과 별개로 날짜 자체에 표시한다("왜 비었지?" 방지)
        from ..models import Holiday
        hq = Holiday.objects.filter(is_deleted=False, date__gte=d0, date__lte=d1)
        if view is not None:
            hq = hq.filter(Q(branch_id=None) | Q(branch_id__in=view))
        if bid:
            hq = hq.filter(Q(branch_id=None) | Q(branch_id=bid))
        hol = {}
        for h in hq:
            hol.setdefault(str(h.date), []).append(h.name)
        # 그 학생이 오는 요일. 학교 밑에 적어 주면 주1회인지 주2회인지 그 자리에서 보인다.
        sids = {it["student_id"] for day in days.values() for it in day.get("items", [])
                if it.get("student_id")}
        wdmap = _student_weekday_map(sids, (now() + timedelta(hours=9)).date()) if sids else {}
        for day in days.values():
            for it in day.get("items", []):
                it["weekdays"] = wdmap.get(it.get("student_id"), "")
        return self.success({"from": str(d0), "to": str(d1), "days": days,
                             "reservations": resv, "holidays": hol})


class EnsureOccurrenceAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """달력에서 아직 생성 안 된 정규수업 인스턴스를 만들어 occ_id 반환(비고/결석/진도 편집 진입용).
        {timetable_id, date}"""
        data = request.data
        s = StudentTimetable.objects.filter(id=data.get("timetable_id")).first()
        if not s:
            return self.error("시간표를 찾을 수 없습니다.")
        if not can_manage_branch(request.user, s.branch_id):
            return self.error("권한이 없습니다.")
        try:
            d = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return self.error("날짜가 올바르지 않습니다.")
        occ, _ = LessonOccurrence.objects.get_or_create(
            source_timetable=s, date=d,
            defaults={"student_id": s.student_id, "branch_id": s.branch_id,
                     "start_time": s.start_time, "duration_minutes": s.duration_minutes,
                     "program": s.program, "subject": s.subject or resolve_program_label(s.program) or "미지정",
                     "instructor_id": s.instructor_id})
        return self.success({"occ_id": occ.id})


def _clear_attendance_for_absence(student, d, actor, note):
    """결석 처리된 날짜에 이미 등원/하원 기록이 있으면 지우고(소프트) 이력 남김.
    등원한 기록이 남아있는데 결석으로 표시되면 앞뒤가 안 맞아서 — 프론트에서 확인창을 띄운 뒤 호출한다.
    단, 그 날 다른 수업이 아직 남아 있으면(연속 2타임 중 뒤 타임만 결석 등) 등원/하원은 남은 수업의
    기록이므로 지우지 않는다. 등원/하원은 수업별이 아니라 학생·날짜 단위로 하나뿐이기 때문."""
    still = LessonOccurrence.objects.filter(student=student, date=d)\
        .exclude(status__in=(OccurrenceStatus.ABSENT, OccurrenceStatus.CANCELLED,
                             OccurrenceStatus.LEAVE, OccurrenceStatus.HOLIDAY))\
        .exists()
    if still:
        return
    a = DailyAttendance.objects.filter(student=student, date=d).first()
    if not a or (not a.check_in_at and not a.check_out_at):
        return
    old_in = _hm_kst(a.check_in_at) if a.check_in_at else "-"
    old_out = _hm_kst(a.check_out_at) if a.check_out_at else "-"
    a.check_in_at = None
    a.check_out_at = None
    a.save(update_fields=["check_in_at", "check_out_at"])
    detail = "결석 처리로 등원/하원 기록 삭제(등원 %s · 하원 %s)" % (old_in, old_out)
    AttendanceChange.objects.create(attendance=a, actor=actor, detail=detail, reason=note or "")


class LessonStatusAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """수업 인스턴스 상태 변경(결석/예정 복원/임시휴원/보강 취소). {occ_id, status:'ABSENT'|'SCHEDULED'|'LEAVE'|'CANCELLED', note?}
        결석으로 바뀌는 경우 그 날짜에 등원/하원 기록이 있으면 함께 지움(프론트에서 미리 확인받고 호출).
        CANCELLED는 보강(is_makeup) 건에 한해서만 허용(결석 취소 시 연결된 보강도 같이 취소하는 용도)."""
        data = request.data
        o = LessonOccurrence.objects.select_related("branch", "student").filter(id=data.get("occ_id")).first()
        if not o:
            return self.error("수업이 없습니다.")
        if not can_manage_branch(request.user, o.branch_id):
            return self.error("권한이 없습니다.")
        st = data.get("status")
        if st not in (OccurrenceStatus.SCHEDULED, OccurrenceStatus.ABSENT, OccurrenceStatus.LEAVE, OccurrenceStatus.CANCELLED):
            return self.error("상태 값이 올바르지 않습니다.")
        if st == OccurrenceStatus.CANCELLED and not o.is_makeup:
            return self.error("보강 건만 취소할 수 있습니다.")
        prev_status, prev_nm, prev_kind = o.status, o.no_makeup, o.no_makeup_kind
        # '예정'으로 되돌릴 때 그날이 학원 휴무일이면 예정이 아니라 휴무로 돌아가야 한다.
        # (안 그러면 휴무일에 그 학생만 수업이 있는 것처럼 남는다)
        if st == OccurrenceStatus.SCHEDULED and not o.is_makeup:
            from ..views.hr import holidays_on
            if holidays_on(o.date, o.branch_id).exists():
                st = OccurrenceStatus.HOLIDAY
        o.status = st
        if "note" in data:
            o.note = (data.get("note") or "").strip()
        if "no_makeup" in data:
            # 명시적으로 넘어온 값을 우선(조퇴예정 등 SCHEDULED 상태에서도 "보강 안 함" 지정 가능하도록)
            o.no_makeup = bool(data.get("no_makeup"))
            o.no_makeup_kind = (data.get("no_makeup_kind") or "") if o.no_makeup else ""
        elif st in (OccurrenceStatus.SCHEDULED, OccurrenceStatus.LEAVE, OccurrenceStatus.HOLIDAY):
            o.no_makeup = False
        if st == OccurrenceStatus.CANCELLED:
            o.makeup_for = None  # 연결된 결석이 있었다면 재연결 가능하도록 해제
        o.save()
        if st == OccurrenceStatus.ABSENT:
            _clear_attendance_for_absence(o.student, o.date, request.user, o.note)
        if st == OccurrenceStatus.CANCELLED:
            TimetableChange.objects.create(student=o.student, actor=request.user, action="DELETE",
                reason=(data.get("reason") or ""),
                detail=("%s 보강 취소: %s %s분 %s%s" % (
                    str(o.date), str(o.start_time)[:5], o.duration_minutes, o.subject or "",
                    (" · " + _name_of(o.instructor)) if o.instructor_id else ""))[:255])
        elif prev_status != st or (prev_nm, prev_kind) != (o.no_makeup, o.no_makeup_kind):
            # 결석·임시휴원·예정 복원과 보강 처리 변경도 누가 언제 했는지 남긴다
            # (예전엔 보강 취소만 기록돼 추적 불가였음)
            _lbl = {OccurrenceStatus.ABSENT: "결석 처리", OccurrenceStatus.LEAVE: "임시휴원 처리",
                    OccurrenceStatus.SCHEDULED: "예정으로 복원",
                    OccurrenceStatus.HOLIDAY: "휴무로 복원(학원 휴무일)"}.get(st, st)
            if prev_status == st:
                _lbl = "결석 보강 처리 변경" if st == OccurrenceStatus.ABSENT else "%s(수정)" % _lbl
            if st == OccurrenceStatus.ABSENT:
                _lbl += ("(보강없음/숙제대체)" if o.no_makeup_kind == "HOMEWORK"
                         else ("(보강없음/숙제없음)" if o.no_makeup else "(보강 예정·미정)"))
            TimetableChange.objects.create(student=o.student, actor=request.user, action="UPDATE",
                reason=(data.get("reason") or o.note or ""),
                detail=("%s %s: %s %s분 %s" % (str(o.date), _lbl, str(o.start_time)[:5],
                                               o.duration_minutes, o.subject or ""))[:255])
        return self.success({"status": o.status, "note": o.note, "no_makeup": o.no_makeup,
                             "no_makeup_kind": o.no_makeup_kind})


class LessonAbsenceAPI(APIView):
    @admin_role_required
    def post(self, request):
        """달력에서 특정 날짜 수업을 결석/예정 토글.
        {timetable_id, date'YYYY-MM-DD', status:'ABSENT'|'SCHEDULED', note?}.
        패턴 수업이면 해당일 인스턴스를 먼저 확정한 뒤 상태 변경."""
        data = request.data
        st = data.get("status")
        if st not in (OccurrenceStatus.SCHEDULED, OccurrenceStatus.ABSENT):
            return self.error("상태 값이 올바르지 않습니다.")
        try:
            d = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return self.error("날짜가 올바르지 않습니다.")
        slot = StudentTimetable.objects.filter(id=data.get("timetable_id")).first()
        if not slot:
            return self.error("수업이 없습니다.")
        if not can_manage_branch(request.user, slot.branch_id):
            return self.error("권한이 없습니다.")
        ensure_occurrences(d, [slot.branch_id] if slot.branch_id else None)
        o = LessonOccurrence.objects.filter(source_timetable=slot, date=d).first()
        if not o:
            return self.error("수업 인스턴스를 만들 수 없습니다(격주 비수업일일 수 있음).")
        o.status = st
        if "note" in data:
            o.note = (data.get("note") or "").strip()
        if st == OccurrenceStatus.SCHEDULED:
            o.no_makeup = False
        o.save()
        return self.success({"occ_id": o.id, "status": o.status})


class PendingMakeupAPI(APIView):
    @admin_role_required
    def get(self, request):
        """보강 현황: 결석(ABSENT) 또는 조퇴예정 표시된 수업 중 '보강 안 함'이 아닌 것.
        보강일이 잡힌 건은 makeup_date/makeup_time과 진행 상태(mk_state: none 미정 /
        planned 예정 / done 완료 / missed 미이수)를 함께 내려 화면에서 걸러 볼 수 있게 한다. branch_id·student_id로 좁힐 수 있고, 오래된 것부터."""
        view = viewable_branch_ids(request.user)
        bid = request.GET.get("branch_id")
        sid = request.GET.get("student_id")
        made = {}
        for m in LessonOccurrence.objects.filter(is_makeup=True, makeup_for__isnull=False):
            made[m.makeup_for_id] = m

        qs = LessonOccurrence.objects.select_related("student", "student__student_profile", "branch").filter(
            status=OccurrenceStatus.ABSENT, is_makeup=False, no_makeup=False)
        if view is not None:
            qs = qs.filter(branch_id__in=view)
        if bid:
            qs = qs.filter(branch_id=bid)
        if sid:
            qs = qs.filter(student_id=sid)

        # 조퇴예정 태그가 붙은 날짜의 수업도 후보에 포함
        el_pairs = list(DailyAttendance.objects.filter(note_tag=EARLY_LEAVE_TAG).values_list("student_id", "date"))
        el_qs = LessonOccurrence.objects.none()
        if el_pairs:
            q = Q()
            for sid, dt in el_pairs:
                q |= Q(student_id=sid, date=dt)
            el_qs = LessonOccurrence.objects.select_related("student", "student__student_profile", "branch").filter(
                q, status=OccurrenceStatus.SCHEDULED, is_makeup=False, no_makeup=False)
            if view is not None:
                el_qs = el_qs.filter(branch_id__in=view)
            if bid:
                el_qs = el_qs.filter(branch_id=bid)
            if sid:
                el_qs = el_qs.filter(student_id=sid)

        cands = [(o, "absence") for o in qs.order_by("date", "start_time")[:300]] + \
                [(o, "early_leave") for o in el_qs.order_by("date", "start_time")[:300]]

        # 보강 수업이 실제로 이루어졌는지는 그날 등원 기록으로 판정한다. 필요한 날짜만 모아 한 번에 조회.
        mk_list = [made[o.id] for o, _ in cands if o.id in made]
        checked_in = set()
        if mk_list:
            checked_in = set(DailyAttendance.objects.filter(
                student_id__in={m.student_id for m in mk_list},
                date__in={m.date for m in mk_list},
                check_in_at__isnull=False,
            ).values_list("student_id", "date"))
        today = (now() + timedelta(hours=9)).date()

        out = []
        for o, kind in cands:
            prof = getattr(o.student, "student_profile", None)
            mk = made.get(o.id)
            if not mk:
                state = "none"          # 보강일 미정
            elif mk.status == OccurrenceStatus.ABSENT:
                # 보강일에 또 결석. 그날 다른 수업으로 등원했을 수 있으므로 등원 기록보다 먼저 본다.
                state = "missed"
            elif (mk.student_id, mk.date) in checked_in:
                state = "done"          # 완료 — 보강일에 등원 기록이 있음
            elif mk.date < today:
                state = "missed"        # 미이수 — 날짜가 지났는데 등원 기록이 없음
            else:
                state = "planned"       # 예정 — 보강일이 아직 오지 않음
            out.append({"occ_id": o.id, "student_id": o.student_id, "student_name": _name_of(o.student),
                        "date": str(o.date), "start_time": str(o.start_time)[:5],
                        "duration_minutes": o.duration_minutes, "kind": kind,
                        "subject": o.subject or "미지정", "branch": (o.branch.name if o.branch_id else ""),
                        "makeup_occ_id": (mk.id if mk else None),
                        "makeup_date": (str(mk.date) if mk else ""),
                        "makeup_time": (str(mk.start_time)[:5] if mk else ""),
                        "mk_state": state,
                        "parent_phone": (prof.parent_phone if prof else ""),
                        "student_phone": (prof.student_phone if prof else "")})
        out.sort(key=lambda r: (r["date"], r["start_time"]))
        return self.success(out)


class LessonEditAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """수업 인스턴스(그 날짜 하나만) 시각·길이·강사·과정 변경 + 사유(이력).
        정규수업은 패턴(StudentTimetable)은 그대로 두고 이 날짜 인스턴스만 바뀜(달력에도 반영).
        보강은 애초에 패턴이 없어 이 값이 유일한 소스.
        추가 수업도 패턴이 없어 마찬가지다 — 날짜와 사유를 여기서 고친다.
        {occ_id, start_time?'HH:MM', duration?, instructor_id?, program?, extra_reason?, reason?}"""
        data = request.data
        o = LessonOccurrence.objects.select_related("branch", "instructor", "student").filter(
            id=data.get("occ_id")).first()
        if not o:
            return self.error("수업이 없습니다.")
        if not can_manage_branch(request.user, o.branch_id):
            return self.error("권한이 없습니다.")
        changes = []
        # 추가 수업 ↔ 보강 바꾸기. 미리 해 준 수업을 나중 결석의 보강으로 돌리는 일이
        # 실제로 있다(반대도 있다). 다만 값을 조용히 뒤집으면 나중에 무슨 일이 있었는지
        # 알 수 없으므로 반드시 이력에 남긴다.
        if "kind" in data:
            newk = (data.get("kind") or "").upper()
            if newk not in ("MAKEUP", "EXTRA"):
                return self.error("종류가 올바르지 않습니다.")
            if not (o.is_makeup or o.is_extra):
                return self.error("정규 수업은 보강·추가 수업으로 바꿀 수 없습니다.")
            cur = "MAKEUP" if o.is_makeup else "EXTRA"
            if newk != cur:
                if newk == "MAKEUP":
                    if not data.get("makeup_for"):
                        return self.error("보강으로 바꾸려면 메울 결석을 골라야 합니다.")
                    o.is_makeup, o.is_extra, o.extra_reason = True, False, ""
                    changes.append("추가 수업 → 보강으로 바꿈")
                else:
                    o.is_makeup, o.is_extra = False, True
                    if o.makeup_for_id:
                        changes.append("결석 연결 해제(%s)" % str(o.makeup_for.date))
                        o.makeup_for = None
                    changes.append("보강 → 추가 수업으로 바꿈")
        what = "추가 수업" if o.is_extra else "보강"
        if "date" in data:
            if not (o.is_makeup or o.is_extra):
                return self.error("정규수업은 날짜를 옮길 수 없습니다(요일 반복 시간표 자체를 바꾸려면 시간표 탭에서 수정하세요).")
            try:
                new_date = datetime.strptime(data.get("date"), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return self.error("날짜 형식이 올바르지 않습니다.")
            if new_date != o.date:
                old_date = o.date
                changes.append("%s 날짜 %s → %s" % (what, str(old_date), str(new_date)))
                o.date = new_date
                o.time_change_reason = (data.get("reason") or "").strip() or (what + " 일정 변경")
                # 옛 날짜에 이 보강 말고 남은 수업이 없다면(등원/하원 기록이 이 보강 때문에 생긴 것으로 보고)
                # 새 날짜로 함께 옮겨준다 — 안 옮기면 옛 날짜에 '수업외'로 고아 데이터가 남음
                still_others = LessonOccurrence.objects.filter(student_id=o.student_id, date=old_date)\
                    .exclude(id=o.id).exclude(status=OccurrenceStatus.CANCELLED).exists()
                if not still_others:
                    old_att = DailyAttendance.objects.filter(student_id=o.student_id, date=old_date).first()
                    if old_att and (old_att.check_in_at or old_att.check_out_at):
                        new_att, _ = DailyAttendance.objects.get_or_create(
                            student_id=o.student_id, date=new_date, defaults={"branch_id": o.branch_id})
                        if not new_att.check_in_at and not new_att.check_out_at:
                            new_att.check_in_at = old_att.check_in_at
                            new_att.check_out_at = old_att.check_out_at
                            new_att.note_tag = old_att.note_tag
                            new_att.note = old_att.note
                            new_att.save()
                            AttendanceChange.objects.create(attendance=new_att, actor=request.user,
                                detail="%s 날짜 변경으로 등원/하원 기록 이동(%s → %s)" % (what, str(old_date), str(new_date)),
                                reason=(data.get("reason") or "").strip())
                            old_att.check_in_at = None
                            old_att.check_out_at = None
                            old_att.save(update_fields=["check_in_at", "check_out_at"])
        if "makeup_for" in data:
            if not o.is_makeup:
                return self.error("보강 건만 결석·조퇴예정과 연결할 수 있습니다.")
            target_id = data.get("makeup_for")
            if target_id:
                target = LessonOccurrence.objects.filter(id=target_id, student_id=o.student_id).first()
                if not target:
                    return self.error("연결할 결석·조퇴예정을 찾을 수 없습니다.")
                target_att = DailyAttendance.objects.filter(student_id=target.student_id, date=target.date).first()
                target_kind = _link_target_kind(target.status, target_att.note_tag if target_att else "")
                if not target_kind:
                    return self.error("결석 또는 조퇴예정 상태인 수업만 연결할 수 있습니다.")
                if LessonOccurrence.objects.filter(is_makeup=True, makeup_for_id=target.id).exclude(id=o.id).exists():
                    return self.error("이미 다른 보강과 연결된 건입니다.")
                if o.makeup_for_id != target.id:
                    label = "결석" if target_kind == "absence" else "조퇴예정"
                    changes.append("%s 연결: %s %s" % (label, str(target.date), str(target.start_time)[:5]))
                    o.makeup_for = target
            elif o.makeup_for_id:
                changes.append("연결 해제")
                o.makeup_for = None
        # 추가 수업의 사유. 보강에는 메울 결석이 있어 까닭이 저절로 서지만, 추가 수업은
        # 왜 불렀는지가 기록에 남지 않으면 나중에 무슨 수업이었는지 알 수 없다.
        if "extra_reason" in data:
            if not o.is_extra:
                return self.error("추가 수업만 사유를 정할 수 있습니다.")
            newr = (data.get("extra_reason") or "")[:32]
            if newr != (o.extra_reason or ""):
                lbl = {x.value: x.label for x in OptionItem.objects.filter(category="extra_reason")}
                changes.append("추가 수업 사유 %s → %s" % (lbl.get(o.extra_reason, o.extra_reason or "없음"),
                                                          lbl.get(newr, newr or "없음")))
                o.extra_reason = newr
        # 넣을 때 적은 메모. 여기 말고는 고칠 곳이 없다.
        if "lesson_memo" in data:
            newm = (data.get("lesson_memo") or "").strip()
            if newm != (o.note or ""):
                changes.append("메모 고침")
                o.note = newm
        if "start_time" in data:
            tm = (data.get("start_time") or "").strip()
            if not tm:
                return self.error("시각을 입력하세요.")
            from datetime import time as _t
            try:
                hh, mm = tm.split(":")
                new_time = _t(int(hh), int(mm))
            except (ValueError, AttributeError):
                return self.error("시각 형식이 올바르지 않습니다(HH:MM).")
            old_tm = str(o.start_time)[:5]
            if old_tm != tm:
                changes.append("시각 %s → %s" % (old_tm, tm))
                o.start_time = new_time
                o.time_change_reason = (data.get("reason") or "").strip() or "수업 정보 수정"
        if data.get("duration"):
            newd = int(data.get("duration"))
            if newd != o.duration_minutes:
                changes.append("수업시간 %s분 → %s분" % (o.duration_minutes, newd))
                o.duration_minutes = newd
        if "instructor_id" in data:
            newi = data.get("instructor_id") or None
            if newi != o.instructor_id:
                old_name = _name_of(o.instructor) if o.instructor_id else "미배정"
                new_u = User.objects.filter(id=newi).first() if newi else None
                new_name = _name_of(new_u) if new_u else "미배정"
                changes.append("강사 %s → %s" % (old_name, new_name))
                o.instructor_id = newi
        if "program" in data:
            newp = data.get("program") or ""
            new_subj = (data.get("subject") or "").strip() or resolve_program_label(newp) or "미지정"
            if newp != (o.program or "") or new_subj != (o.subject or ""):
                old_subj = o.subject or "미지정"
                changes.append("과정 %s → %s" % (old_subj, new_subj))
                o.program = newp
                o.subject = new_subj
        # 바뀐 값 기준으로 그 날 다른 수업과 겹치는지 확인(자기 자신은 제외)
        conf = _find_day_conflict(o.student_id, o.date, o.start_time, o.duration_minutes, exclude_occ_id=o.id)
        if conf:
            return self.error(_conflict_msg(_name_of(o.student), str(o.date), conf[0], conf[1], conf[2]))
        instr_u = User.objects.filter(id=o.instructor_id).first() if o.instructor_id else None
        instr_name = _name_of(instr_u) if instr_u else "미배정"
        if not changes:
            return self.success({"changed": False, "date": str(o.date), "start_time": str(o.start_time)[:5],
                                 "duration_minutes": o.duration_minutes, "instructor_id": o.instructor_id,
                                 "instructor": instr_name, "program": o.program, "subject": o.subject})
        reason = (data.get("reason") or "").strip()
        o.save()
        TimetableChange.objects.create(
            student=o.student, actor=request.user, action="UPDATE",
            reason=reason or "수업 정보 수정",
            detail=("%s: %s" % (str(o.date), "; ".join(changes)))[:255])
        return self.success({"changed": True, "date": str(o.date), "start_time": str(o.start_time)[:5],
                             "duration_minutes": o.duration_minutes, "instructor_id": o.instructor_id,
                             "instructor": instr_name, "program": o.program, "subject": o.subject})


class LessonProgressAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """진도 조회. ?occ_id= (해당 수업 진도 1건) 또는 ?student_id= (진도표 목록)."""
        occ_id = request.GET.get("occ_id")
        if occ_id:
            p = LessonProgress.objects.filter(occurrence_id=occ_id, is_hidden=False).first()
            if not p:
                return self.success(None)
            return self.success({"id": p.id, "date": str(p.date), "content": p.content,
                                 "homework": p.homework, "feedback": p.feedback, "memo": p.memo,
                                 "author": _name_of(p.author) if p.author_id else "",
                                 "time": _kst_dt_str(p.update_time)})
        sid = request.GET.get("student_id")
        u = User.objects.filter(id=sid).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_view_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        out = []
        for p in LessonProgress.objects.select_related("author", "occurrence").filter(
                student=u, is_hidden=False).order_by("-date", "-id")[:200]:
            out.append({"id": p.id, "date": str(p.date), "content": p.content, "homework": p.homework,
                        "feedback": p.feedback, "memo": p.memo,
                        "subject": (p.occurrence.subject if p.occurrence_id else ""),
                        "author": _name_of(p.author) if p.author_id else "",
                        "time": _kst_dt_str(p.update_time)})
        return self.success(out)

    @admin_role_required
    def post(self, request):
        """진도 저장(업서트). {occ_id?, student_id, date?, content, homework}.
        occ_id 있으면 그 수업의 진도 1건을 갱신/생성, 없으면 자유 기록 생성."""
        data = request.data
        content = (data.get("content") or "").strip()
        homework = (data.get("homework") or "").strip()
        feedback = (data.get("feedback") or "").strip()
        memo = (data.get("memo") or "").strip()
        occ_id = data.get("occ_id")
        if occ_id:
            o = LessonOccurrence.objects.select_related("branch").filter(id=occ_id).first()
            if not o:
                return self.error("수업이 없습니다.")
            if not can_manage_branch(request.user, o.branch_id):
                return self.error("권한이 없습니다.")
            p, _ = LessonProgress.objects.get_or_create(
                occurrence=o, defaults={"student_id": o.student_id, "date": o.date})
            p.student_id = o.student_id
            p.date = o.date
            p.is_hidden = False
        else:
            u = User.objects.filter(id=data.get("student_id")).first()
            if not u:
                return self.error("학생이 없습니다.")
            prof = getattr(u, "academy_profile", None)
            if prof and not can_manage_branch(request.user, prof.branch_id):
                return self.error("권한이 없습니다.")
            try:
                d = datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else now().date()
            except (TypeError, ValueError):
                d = now().date()
            if data.get("id"):
                p = LessonProgress.objects.filter(id=data.get("id"), student=u).first()
                if not p:
                    return self.error("진도 기록이 없습니다.")
                p.date = d
            else:
                p = LessonProgress(student=u, date=d)
        p.content = content
        p.homework = homework
        p.feedback = feedback
        p.memo = memo
        p.author = request.user
        p.save()
        return self.success({"id": p.id})

    @admin_role_required
    def delete(self, request):
        """진도 소프트삭제."""
        p = LessonProgress.objects.select_related("student").filter(id=request.GET.get("id")).first()
        if not p:
            return self.error("진도 기록이 없습니다.")
        prof = getattr(p.student, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        p.is_hidden = True
        p.save(update_fields=["is_hidden"])
        return self.success(True)


class MakeupAddAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """수업 추가. {student_id, date, start_time'HH:MM', duration?, program?, instructor_id?,
        source_timetable_id?(정규수업), makeup_for?(결석 occ_id), note?,
        is_extra?(추가 수업), extra_reason?}

        보강은 빠진 것을 메우는 것이고, 추가 수업은 더 얹는 것이다 — 대회 전날 하루 더
        나오라고 불러 시키는 특강 같은 것. 성질이 반대라 섞으면 '결석 없는 보강'이 쌓여
        미보강 결석 수가 어긋난다.
        """
        data = request.data
        is_extra = bool(data.get("is_extra"))
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            d = datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else now().date()
        except (TypeError, ValueError):
            d = now().date()
        tm = (data.get("start_time") or "").strip()
        if not tm:
            return self.error("보강 시각을 입력하세요.")
        try:
            from datetime import time as _t
            hh, mm = tm.split(":")
            st_time = _t(int(hh), int(mm))
        except (ValueError, AttributeError):
            return self.error("시각 형식이 올바르지 않습니다(HH:MM).")
        note = (data.get("note") or "").strip()
        target = None
        # 추가 수업은 메울 결석이 없다. 화면에서 잘못 딸려 와도 여기서 끊는다.
        makeup_for_id = None if is_extra else data.get("makeup_for")
        if makeup_for_id:
            target = LessonOccurrence.objects.filter(id=makeup_for_id, student_id=u.id).first()
            if not target:
                return self.error("연결할 수업을 찾을 수 없습니다.")
            # 같은 결석에 이미 보강이 잡혀 있으면 막는다. 두 사람이 거의 동시에 각자 보강을 잡아
            # 같은 날 중복 수업이 생기는 사고를 방지(수정 경로에는 있던 검사가 생성 경로엔 없었음).
            dup = LessonOccurrence.objects.filter(is_makeup=True, makeup_for_id=target.id)\
                .exclude(status=OccurrenceStatus.CANCELLED).first()
            if dup:
                return self.error(
                    "이미 이 건에 연결된 보강이 있습니다(%s %s). 다른 선생님이 먼저 잡았을 수 있으니 "
                    "화면을 새로고침해 확인하세요. 바꾸려면 기존 보강을 취소한 뒤 다시 잡아주세요."
                    % (str(dup.date), str(dup.start_time)[:5]))
        src = StudentTimetable.objects.filter(id=data.get("source_timetable_id")).first()
        # 추가 수업은 걸어 둘 결석도, 고른 정규 수업도 없다. 그대로 두면 과목이 '추가 수업'
        # 으로만 남아 무슨 수업이었는지 나중에 알 수 없다. 그 학생이 지금 듣는 줄을 따른다.
        if not src and is_extra:
            src = StudentTimetable.objects.filter(student_id=u.id, status="ACTIVE").order_by("id").last()
        # 과목·수업시간·담당강사: 명시 입력 > 정규수업(source_timetable_id) > 연결 대상 수업(target) > 기본값
        dur = data.get("duration") or (src.duration_minutes if src else (target.duration_minutes if target else 60))
        # 그 날 이미 있는 수업과 시간이 겹치면 막는다(같은 학생이 동시에 두 수업에 있을 수 없음)
        conf = _find_day_conflict(u.id, d, st_time, dur)
        if conf:
            return self.error(_conflict_msg(_name_of(u), str(d), conf[0], conf[1], conf[2]))
        prog = data.get("program") or (src.program if src else (target.program if target else ""))
        # 과목: 명시 입력 > 정규수업(src)/연결 대상(target)의 실제 과목(언어 등 세부 포함) > 과정 코드의 일반 라벨.
        # 순서를 뒤집으면(과정라벨을 먼저 쓰면) LANG 과정이 항상 '프로그래밍언어'로만 나오고
        # 실제 저장된 세부 과목(예: Python 약어 'Py')을 덮어써버리므로 주의.
        subj = (data.get("subject") or "").strip() or \
            (src.subject if src else "") or (target.subject if target else "") or \
            resolve_program_label(prog) or ("추가 수업" if is_extra else "보강")
        instr = data.get("instructor_id")
        if instr is None:
            instr = src.instructor_id if src else (target.instructor_id if target else None)
        with transaction.atomic():
            # 아직 결석·조퇴예정 처리 전(그냥 예정 수업)인 대상을 고른 경우에만 결석으로 자동 처리.
            # 이미 조퇴예정으로 표시된 대상은 그대로 두고(학생은 출석했으므로) 결석 처리하지 않음.
            if target:
                target_att = DailyAttendance.objects.filter(student_id=target.student_id, date=target.date).first()
                target_kind = _link_target_kind(target.status, target_att.note_tag if target_att else "")
                if not target_kind:
                    target.status = OccurrenceStatus.ABSENT
                    if note:
                        target.note = note
                    target.no_makeup = False
                    target.save()
            occ = LessonOccurrence.objects.create(
                student=u, branch_id=(prof.branch_id if prof else (src.branch_id if src else None)),
                source_timetable=None, date=d, start_time=st_time, duration_minutes=dur,
                program=prog, subject=subj, instructor_id=instr,
                status=OccurrenceStatus.SCHEDULED, is_makeup=(not is_extra),
                is_extra=is_extra, extra_reason=((data.get("extra_reason") or "")[:32] if is_extra else ""),
                makeup_for_id=makeup_for_id, note=note)
            instr_u = User.objects.filter(id=instr).first() if instr else None
            what = "추가 수업" if is_extra else "보강"
            TimetableChange.objects.create(
                student=u, actor=request.user, action="CREATE", reason=note or (what + " 생성"),
                detail=("%s %s 생성: %s %s분 %s%s" % (
                    str(d), what, tm, dur, subj or what,
                    (" · " + _name_of(instr_u)) if instr_u else ""))[:255])
        return self.success({"occ_id": occ.id})


class ActivityLogAPI(APIView):
    """포털 사용 이력 — '누가 무엇을 했는지'를 한 화면에서 본다.
    기록을 새로 쌓는 게 아니라, 이미 각 기능이 남기고 있는 이력들(시간표/보강, 출결,
    등록상태, 직원관리, 인사정보, 상담기록 수정)을 행위자 기준으로 합쳐서 보여준다.
    GET ?user_id=(원장 이상만 타인 조회)&from=YYYY-MM-DD&to=YYYY-MM-DD&q=검색어"""
    @admin_role_required
    def get(self, request):
        target_id = request.GET.get("user_id")
        me = request.user
        # 지점 직원 전체를 한 번에 보는 길. 한 명씩 골라 보려면 누가 언제 뭘 했는지
        # 찾을 때마다 사람을 바꿔 가며 훑어야 한다.
        actors = None           # None 이면 한 사람, 목록이면 여럿
        if target_id == "ALL":
            if not _is_director_up(me):
                return self.error("지점 전체 이력은 원장 이상만 볼 수 있습니다.")
            view = viewable_branch_ids(me)
            aq = AcademyProfile.objects.filter(is_deleted=False, role__in=STAFF_ROLES)
            if view is not None:
                aq = aq.filter(branch_id__in=view)
            actors = list(aq.values_list("user_id", flat=True))
            target = me
        elif target_id and str(target_id) != str(me.id):
            if not _is_director_up(me):
                return self.error("다른 사람의 사용 이력은 원장 이상만 볼 수 있습니다.")
            target = User.objects.filter(id=target_id).first()
            if not target:
                return self.error("사용자를 찾을 수 없습니다.")
            tp = getattr(target, "academy_profile", None)
            if tp and not can_view_branch(me, tp.branch_id):
                return self.error("이 지점 인원이 아닙니다.")
        else:
            target = me
        today = (now() + timedelta(hours=9)).date()
        try:
            d0 = datetime.strptime(request.GET.get("from"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            d0 = today - timedelta(days=30)
        try:
            d1 = datetime.strptime(request.GET.get("to"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            d1 = today
        lo = _kst_to_utc(d0, "00:00")
        hi = _kst_to_utc(d1 + timedelta(days=1), "00:00")
        q = (request.GET.get("q") or "").strip()

        rows = []

        # 지점 전체를 볼 때는 누가 한 일인지도 있어야 한다(한 사람만 볼 때는 뻔하므로 비운다)
        who = {}
        if actors is not None:
            who = {u.id: _name_of(u) for u in User.objects.filter(id__in=actors)
                                                          .select_related("userprofile")}

        def add(dt, kind, target_name, detail, reason="", actor_id=None):
            rows.append({"time": str(dt + timedelta(hours=9))[:19], "kind": kind,
                         "target": target_name or "", "detail": detail or "", "reason": reason or "",
                         "actor": who.get(actor_id, "") if actors is not None else ""})


        base = ({"actor_id__in": actors} if actors is not None else {"actor": target})
        base.update(create_time__gte=lo, create_time__lt=hi)
        for c in TimetableChange.objects.filter(**base).select_related("student", "student__userprofile")[:1000]:
            add(c.create_time, "시간표·보강", _name_of(c.student), c.detail, c.reason, actor_id=c.actor_id)
        for c in AttendanceChange.objects.filter(**base).select_related(
                "attendance", "attendance__student", "attendance__student__userprofile")[:1000]:
            a = c.attendance
            add(c.create_time, "출결", "%s %s" % (_name_of(a.student) if a else "", str(a.date) if a else ""),
                c.detail, c.reason, actor_id=c.actor_id)
        for c in StudentStatusChange.objects.filter(**base).select_related("student", "student__userprofile")[:1000]:
            add(c.create_time, "등록상태", _name_of(c.student),
                "%s → %s%s" % (c.from_status, c.to_status,
                               (" (적용 %s)" % c.effective_date) if c.effective_date else ""), c.reason, actor_id=c.actor_id)
        for c in StaffChangeLog.objects.filter(**base).select_related("staff", "staff__userprofile")[:1000]:
            add(c.create_time, "직원관리", _name_of(c.staff), "%s %s" % (c.change_type, c.detail), c.reason, actor_id=c.actor_id)
        for c in StaffProfileHistory.objects.filter(**base).select_related("user", "user__userprofile")[:1000]:
            ov = staff_value_text(c.field, c.old_value)
            nv = staff_value_text(c.field, c.new_value)
            if ov == nv:
                continue        # 빈 값에서 빈 값으로 — 사람 눈에는 바뀐 게 없다
            add(c.create_time, "인사정보", _name_of(c.user),
                "%s: %s → %s" % (staff_field_label(c.field), ov, nv), c.reason, actor_id=c.actor_id)
        # 학생 등록 — 일괄은 한 번을 한 줄로 묶고 이름만 늘어놓는다.
        # 상세를 적어 봐야 뒤에 고치면 그건 각자의 이력에 남으므로 두 번 적는 셈이다.
        regs = list(StudentRegisterLog.objects.filter(**base)
                    .select_related("student", "student__userprofile", "branch")[:1000])
        batches = {}
        for c in regs:
            if c.source == "BULK" and c.batch:
                batches.setdefault(c.batch, []).append(c)
                continue
            label = "상담 전환" if c.source == "LEAD" else "직접 등록"
            add(c.create_time, "학생등록", _name_of(c.student),
                "%s%s" % (label, (" · %s" % c.branch.name) if c.branch_id else ""), actor_id=c.actor_id)
        for items in batches.values():
            names = [_name_of(x.student) for x in items]
            bn = items[0].branch.name if items[0].branch_id else ""
            add(max(x.create_time for x in items), "학생등록", "%d명" % len(names),
                "일괄 등록%s — %s" % ((" · %s" % bn) if bn else "", ", ".join(names)),
                actor_id=items[0].actor_id)

        for c in CounselingLogEdit.objects.filter(**base).select_related("log")[:1000]:
            add(c.create_time, "상담기록", "", "상담 기록 수정(이전 내용: %s)" % (c.old_summary or "")[:80], actor_id=c.actor_id)

        if q:
            ql = q.lower()
            rows = [r for r in rows
                    if ql in r["target"].lower() or ql in r["detail"].lower()
                    or ql in r["reason"].lower() or ql in r["kind"].lower()
                    or ql in (r.get("actor") or "").lower()]
        rows.sort(key=lambda r: r["time"], reverse=True)
        truncated = len(rows) > 500
        rows = rows[:500]

        # 원장 이상은 지점 인원을 골라서 볼 수 있게 목록을 함께 내려준다
        people = []
        if _is_director_up(me):
            view = viewable_branch_ids(me)
            qs = AcademyProfile.objects.filter(role__in=STAFF_ROLES, is_deleted=False)\
                .select_related("user", "user__userprofile", "branch")
            if view is not None:
                qs = qs.filter(branch_id__in=view)
            for p in qs:
                people.append({"id": p.user_id, "name": _name_of(p.user),
                               "role": p.role, "branch": p.branch.name if p.branch_id else ""})
            people.sort(key=lambda x: (x["branch"], x["name"] or ""))
        return self.success({"rows": rows, "truncated": truncated, "people": people,
                             "user_id": ("ALL" if actors is not None else target.id),
                             "user_name": ("지점 직원 전체 (%d명)" % len(actors)) if actors is not None else _name_of(target),
                             "from": str(d0), "to": str(d1)})


class StudentLegacyUrlAPI(APIView):
    """학생별 '이전 기록 링크'(기존 관리 스프레드시트 등) 저장/삭제.
    포털 전환 기간에 예전 시트를 같이 보느라 왔다갔다 하는 수고를 줄이기 위한 바로가기."""
    @admin_role_required
    def post(self, request):
        u = User.objects.filter(id=request.data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        sp = getattr(u, "student_profile", None)
        if not sp:
            return self.error("등록 정보가 없습니다.")
        url = (request.data.get("url") or "").strip()
        # 링크로 그대로 열리는 값이라 javascript: 같은 스킴이 들어가지 않도록 http(s)만 허용
        if url and not (url.startswith("http://") or url.startswith("https://")):
            return self.error("주소는 http:// 또는 https:// 로 시작해야 합니다.")
        sp.legacy_url = url[:500]
        sp.save(update_fields=["legacy_url"])
        return self.success({"legacy_url": sp.legacy_url})


class StudentLessonCandidatesAPI(APIView):
    @admin_role_required
    def get(self, request):
        """'수업외 등원'을 특정 정규수업의 보강으로 연결할 대상 후보(그 학생의 수업, 취소·보강·
        이미 다른 보강이 연결된 것·보강 안 함으로 표시된 것 제외 — 중복 보강 방지). 오늘 기준 -30*back_months일 ~
        +30*fwd_months일 범위(기본 back_months=3, fwd_months=1). 모달의 '-1개월'/'+1개월'
        버튼으로 각 방향을 독립적으로 넓힐 수 있음. 이미 결석인 것과 앞으로 예정된 것 모두
        보여주고, 예정된 걸 고르면 그 자리에서 결석으로 전환한다(AdhocMakeupLinkAPI)."""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            back_months = max(1, min(24, int(request.GET.get("back_months") or 3)))
        except (TypeError, ValueError):
            back_months = 3
        try:
            fwd_months = max(1, min(24, int(request.GET.get("fwd_months") or 1)))
        except (TypeError, ValueError):
            fwd_months = 1
        d = (now() + timedelta(hours=9)).date()
        d0 = d - timedelta(days=30 * back_months)
        d1 = d + timedelta(days=30 * fwd_months)
        # 정규 수업 인스턴스는 그 날짜를 실제로 조회(오늘 운영 등)할 때 생성되므로,
        # 아직 아무도 안 본 미래·과거 날짜는 비어 있을 수 있다 — 조회 전 범위 전체를 만들어둔다.
        branch_ids = [prof.branch_id] if (prof and prof.branch_id) else None
        cur = d0
        while cur <= d1:
            ensure_occurrences(cur, branch_ids)
            cur += timedelta(days=1)
        # 이미 다른 보강이 연결된 결석/수업은 후보에서 제외(중복 보강 방지)
        already_linked = LessonOccurrence.objects.filter(
            is_makeup=True, makeup_for__isnull=False).values_list("makeup_for_id", flat=True)
        occ = LessonOccurrence.objects.filter(
            student_id=u.id, date__gte=d0, date__lte=d1,
            is_makeup=False, no_makeup=False).exclude(status=OccurrenceStatus.CANCELLED)\
            .exclude(id__in=already_linked).order_by("date", "start_time")
        rows = [{"occ_id": o.id, "date": str(o.date), "start_time": str(o.start_time)[:5],
                "subject": o.subject or resolve_program_label(o.program) or "미지정",
                "status": o.status, "note": o.note} for o in occ]
        return self.success({"from": str(d0), "to": str(d1), "rows": rows})


class StudentAttendanceHistoryAPI(APIView):
    @admin_role_required
    def get(self, request):
        """학생 상세의 출결기록 탭. 수업시작일(없으면 등록일)~오늘 기본, fwd_days로
        오늘 이후 범위를 늘릴 수 있음(결석 예정·시간표 변경 등 미리 안내받은 내용 확인용).
        최신순(날짜 내림차순)으로 반환 — 프론트에서 월 단위로 묶어 표시."""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        sp = getattr(u, "student_profile", None)
        today = (now() + timedelta(hours=9)).date()
        d0 = (sp.lesson_start_date if sp else None) or (sp.enrollment_date if sp else None) or today
        # 조회 범위는 일 단위(fwd_days). 예전 fwd_months 호출도 그대로 받아준다.
        try:
            fwd_days = int(request.GET.get("fwd_days") or 0)
        except (TypeError, ValueError):
            fwd_days = 0
        if not fwd_days:
            try:
                fwd_days = 30 * int(request.GET.get("fwd_months") or 0)
            except (TypeError, ValueError):
                fwd_days = 0
        fwd_days = max(0, min(730, fwd_days))
        d1 = today + timedelta(days=fwd_days)
        if d1 < d0:
            d1 = d0
        # 학생 시간표 패턴 기준으로만 인스턴스를 만든다(지점 전체 스캔 없이 이 학생만 — 기간이
        # 길 수 있어 ensure_occurrences(지점 전체 스캔)를 그대로 쓰면 비용이 큼).
        tts = list(StudentTimetable.objects.filter(student_id=u.id, status="ACTIVE"))
        if tts:
            existing = set(LessonOccurrence.objects.filter(
                student_id=u.id, date__gte=d0, date__lte=d1, source_timetable__isnull=False
            ).values_list("source_timetable_id", "date"))
            # 시간표를 바꾸면 새 줄에서 같은 날·같은 시각 수업이 하나 더 생긴다(옛 줄로 만든 것이
            # 이미 있는데도). ensure_occurrences 에는 이 검사가 있었지만 여기엔 없어 백건우
            # 8/13 17:00 이 두 개가 됐다.
            taken = set(LessonOccurrence.objects.filter(
                student_id=u.id, date__gte=d0, date__lte=d1, is_makeup=False
            ).exclude(status=OccurrenceStatus.CANCELLED).values_list("date", "start_time"))
            creates = []
            cur = d0
            while cur <= d1:
                wd = cur.weekday()
                for s in tts:
                    if s.weekday != wd:
                        continue
                    if not _slot_active_on(s, cur):
                        continue
                    if (s.id, cur) in existing:
                        continue
                    if (cur, s.start_time) in taken:
                        continue
                    taken.add((cur, s.start_time))
                    creates.append(LessonOccurrence(
                        student_id=s.student_id, branch_id=s.branch_id, source_timetable_id=s.id,
                        date=cur, start_time=s.start_time, duration_minutes=s.duration_minutes,
                        program=s.program, subject=s.subject or resolve_program_label(s.program) or "미지정",
                        instructor_id=s.instructor_id))
                cur += timedelta(days=1)
            if creates:
                LessonOccurrence.objects.bulk_create(creates, ignore_conflicts=True)
        occ = list(LessonOccurrence.objects.filter(student_id=u.id, date__gte=d0, date__lte=d1)
                   .exclude(status=OccurrenceStatus.CANCELLED)
                   .select_related("makeup_for", "instructor", "source_timetable").order_by("-date", "-start_time"))
        att_map = {a.date: a for a in DailyAttendance.objects.filter(student_id=u.id, date__gte=d0, date__lte=d1)}
        occ_ids = [o.id for o in occ]
        makeup_of = {m.makeup_for_id: m for m in
                    LessonOccurrence.objects.filter(is_makeup=True, makeup_for_id__in=occ_ids)}
        prog_by_occ = {p["occurrence_id"]: {"content": p["content"], "homework": p["homework"], "feedback": p["feedback"], "memo": p["memo"]}
                      for p in LessonProgress.objects.filter(
                          occurrence_id__in=occ_ids, is_hidden=False).values("occurrence_id", "content", "homework", "feedback", "memo")}
        rows = []
        for o in occ:
            a = att_map.get(o.date)
            time_changed = bool(o.source_timetable and o.source_timetable.start_time
                                 and str(o.start_time)[:5] != str(o.source_timetable.start_time)[:5])
            row = {"occ_id": o.id, "date": str(o.date), "start_time": str(o.start_time)[:5],
                  "subject": o.subject or resolve_program_label(o.program) or "미지정",
                  "program": o.program or "", "duration_minutes": o.duration_minutes,
                  "instructor": _name_of(o.instructor) if o.instructor_id else "미배정",
                  "instructor_id": o.instructor_id,
                  "status": o.status, "is_makeup": o.is_makeup, "no_makeup": o.no_makeup,
                  "is_extra": o.is_extra, "extra_reason": o.extra_reason,
                  "no_makeup_kind": o.no_makeup_kind, "lesson_note": o.note,
                  "time_changed": time_changed,
                  "orig_time": (str(o.source_timetable.start_time)[:5] if (time_changed and o.source_timetable) else ""),
                  "time_change_reason": (o.time_change_reason if time_changed else ""),
                  "att": {"in": _hm_kst(a.check_in_at) if a else "", "out": _hm_kst(a.check_out_at) if a else "",
                          "note_tag": a.note_tag if a else "", "note": a.note if a else ""},
                  "progress": prog_by_occ.get(o.id), "linked": None}
            if _link_target_kind(o.status, a.note_tag if a else ""):
                mk = makeup_of.get(o.id)
                if mk:
                    mk_att = att_map.get(mk.date)
                    mk_done = bool(mk_att and mk_att.check_in_at and mk_att.check_out_at)
                    row["linked"] = {"kind": "makeup", "occ_id": mk.id, "date": str(mk.date), "start_time": str(mk.start_time)[:5],
                                     "status": mk.status, "done": mk_done}
            elif o.is_makeup and o.makeup_for_id and o.makeup_for:
                t = o.makeup_for
                t_att = att_map.get(t.date)
                t_kind = _link_target_kind(t.status, t_att.note_tag if t_att else "") or "absence"
                row["linked"] = {"kind": t_kind, "occ_id": t.id, "date": str(t.date), "start_time": str(t.start_time)[:5]}
            rows.append(row)
        return self.success({"from": str(d0), "to": str(d1), "fwd_days": fwd_days, "rows": rows})


class StudentTempLeaveAPI(APIView):
    @admin_role_required
    def post(self, request):
        """기간을 지정해 임시휴원 일괄 처리. 결석과 달리 애초에 수업 대상이 아니었다는 의미로,
        등록상태(재원/휴원/퇴원)는 건드리지 않고 그 기간의 정규 수업 인스턴스만 LEAVE로 표시한다.
        기간이 끝나면 그 다음 정규 수업일부터 자동으로 원래대로 예정(SCHEDULED) 처리됨(별도 복귀 로직 불필요).
        {student_id, from'YYYY-MM-DD', to'YYYY-MM-DD', note?}"""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            d0 = datetime.strptime(data.get("from"), "%Y-%m-%d").date()
            d1 = datetime.strptime(data.get("to"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return self.error("기간이 올바르지 않습니다.")
        if d1 < d0:
            return self.error("종료일이 시작일보다 빠릅니다.")
        if (d1 - d0).days > 366:
            return self.error("기간은 최대 1년까지 지정할 수 있습니다.")
        note = (data.get("note") or "").strip()
        tts = list(StudentTimetable.objects.filter(student_id=u.id, status="ACTIVE"))
        if not tts:
            return self.error("이 학생의 정규 시간표가 없습니다.")
        existing = {(o.source_timetable_id, o.date): o for o in LessonOccurrence.objects.filter(
            student_id=u.id, date__gte=d0, date__lte=d1, source_timetable__isnull=False)}
        creates = []
        touched = []
        cur = d0
        while cur <= d1:
            wd = cur.weekday()
            for s in tts:
                if s.weekday != wd:
                    continue
                if not _slot_active_on(s, cur):
                    continue
                existing_occ = existing.get((s.id, cur))
                if existing_occ:
                    touched.append(existing_occ)
                else:
                    creates.append(LessonOccurrence(
                        student_id=s.student_id, branch_id=s.branch_id, source_timetable_id=s.id,
                        date=cur, start_time=s.start_time, duration_minutes=s.duration_minutes,
                        program=s.program, subject=s.subject or resolve_program_label(s.program) or "미지정",
                        instructor_id=s.instructor_id, status=OccurrenceStatus.LEAVE, note=note))
            cur += timedelta(days=1)
        if creates:
            LessonOccurrence.objects.bulk_create(creates)
        for o in touched:
            o.status = OccurrenceStatus.LEAVE
            o.note = note
            o.no_makeup = False
            o.save(update_fields=["status", "note", "no_makeup"])
        count = len(creates) + len(touched)
        if count:
            TimetableChange.objects.create(
                student=u, actor=request.user, action="UPDATE",
                reason=note or "임시휴원 처리",
                detail=("임시휴원 처리 — %s ~ %s (%d건)" % (d0, d1, count))[:255])
        return self.success({"count": count, "from": str(d0), "to": str(d1)})


class AdhocMakeupLinkAPI(APIView):
    @admin_role_required
    def post(self, request):
        """'수업외 등원'(오늘 수업 인스턴스 없이 체크만 된 등원)을 학생의 다른 특정 수업의
        보강으로 연결. 대상이 아직 결석 처리 전이면 이 요청으로 결석 처리(사유 포함)까지 함께 한다.
        {student_id, target_occ_id, date?(수업외 등원일, 기본 오늘), note?}"""
        data = request.data
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        prof = getattr(u, "academy_profile", None)
        if prof and not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        target = LessonOccurrence.objects.filter(id=data.get("target_occ_id"), student_id=u.id).first()
        if not target:
            return self.error("연결할 수업을 찾을 수 없습니다.")
        try:
            d = datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else \
                (now() + timedelta(hours=9)).date()
        except (TypeError, ValueError):
            d = (now() + timedelta(hours=9)).date()
        att = DailyAttendance.objects.filter(student_id=u.id, date=d).first()
        if not att or not att.check_in_at:
            return self.error("등원 기록을 찾을 수 없습니다.")
        if LessonOccurrence.objects.filter(date=d, student_id=u.id).exclude(status=OccurrenceStatus.CANCELLED).exists():
            return self.error("이미 그 날짜에 수업 인스턴스가 있습니다.")
        note = (data.get("note") or "").strip()
        with transaction.atomic():
            target_att = DailyAttendance.objects.filter(student_id=target.student_id, date=target.date).first()
            target_kind = _link_target_kind(target.status, target_att.note_tag if target_att else "")
            if not target_kind:
                target.status = OccurrenceStatus.ABSENT
                if note:
                    target.note = note
                target.no_makeup = False
                target.save()
            tt = StudentTimetable.objects.filter(student_id=u.id, status="ACTIVE")\
                .select_related("instructor").order_by("weekday").first()
            kst_dt = att.check_in_at + timedelta(hours=9)
            new_occ = LessonOccurrence.objects.create(
                student=u, branch_id=(prof.branch_id if prof else target.branch_id),
                source_timetable=None, date=d, start_time=kst_dt.time().replace(second=0, microsecond=0),
                duration_minutes=(tt.duration_minutes if tt else target.duration_minutes),
                program=(tt.program if tt else target.program),
                subject=((tt.subject or resolve_program_label(tt.program)) if tt else target.subject) or "보강",
                instructor_id=(tt.instructor_id if tt else target.instructor_id),
                status=OccurrenceStatus.SCHEDULED, is_makeup=True, makeup_for=target, note=note)
        return self.success({"occ_id": new_occ.id})
