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
                      Lead, LeadStatus, CounselingLog, CounselingLogEdit, CounselReservation, StudentProfile, EnrollmentStatus,
                      OptionItem, StudentTimetable, LessonType, GuardianStudent,
                      StaffProfile, HRNotice, StaffDocument, StaffProfileHistory,
                      TimetableChange, StudentStatusChange, StudentCredential, StaffChangeLog, DailyAttendance,
                      AttendanceChange, LessonOccurrence, OccurrenceStatus, LessonProgress,
                      MsgTemplateGroup, MsgTemplate)
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
                        "actor": an, "time": str(c.create_time)[:16]})
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
        return self.success({"token": lead.enroll_token,
                             "path": "/portal/?enroll=" + lead.enroll_token,
                             "expires": str(lead.enroll_token_expires)[:16]})


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
        fields = [("parent_name", "학부모 이름"), ("parent_phone", "학부모 연락처"),
                  ("student_name", "자녀 이름"), ("school_type", "학교 구분"),
                  ("school_name", "학교 이름"), ("grade", "학년"),
                  ("purpose", "학생의 목표"), ("purpose_detail", "목표 상세"), ("interest", "문의")]
        changed = []
        for f, label in fields:
            if f in data:
                newv = (data.get(f) or "").strip()
                if getattr(lead, f) != newv:
                    setattr(lead, f, newv)
                    changed.append(label)
        bid = data.get("branch_id")
        if bid and bid != lead.branch_id:
            b = Branch.objects.filter(id=bid).first()
            if b and can_manage_branch(request.user, b.id):
                lead.branch = b
                changed.append("지점")
        if changed:
            try:
                log = _json.loads(lead.edit_log) if lead.edit_log else []
            except (ValueError, TypeError):
                log = []
            log.append({"time": str(now())[:16], "by": _name_of(request.user),
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
            counsel_at=data.get("counsel_at"),
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
            created_by=request.user)
        return self.success(LeadSerializer(lead, context={"show_hidden": _is_manager(request.user)}).data)

    @admin_role_required
    def put(self, request):
        """상담 예약 수정(일시·메모) + 변경 이력. {reservation_id, at 또는 date+time, note?}"""
        data = request.data
        r = CounselReservation.objects.select_related("lead").filter(id=data.get("reservation_id")).first()
        if not r:
            return self.error("예약이 없습니다.")
        if not can_manage_branch(request.user, r.lead.branch_id):
            return self.error("No permission for this branch")
        at = (data.get("at") or "").strip()
        d = (data.get("date") or "").strip()
        t = (data.get("time") or "").strip()
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
        # 변경 이력(이전 값 보존)
        new_note = (data.get("note") or "").strip()
        if sched != r.scheduled_at or new_note != r.note:
            try:
                log = _json.loads(r.edit_log) if r.edit_log else []
            except (ValueError, TypeError):
                log = []
            log.append({"time": str(now())[:16], "by": _name_of(request.user),
                        "old_at": _hm_kst(r.scheduled_at) and (str((r.scheduled_at + timedelta(hours=9)).date()) + " " + _hm_kst(r.scheduled_at)),
                        "old_note": r.note})
            r.edit_log = _json.dumps(log, ensure_ascii=False)
        r.scheduled_at = sched
        r.note = new_note
        r.save()
        return self.success(LeadSerializer(r.lead, context={"show_hidden": _is_manager(request.user)}).data)

    @admin_role_required
    def delete(self, request):
        """상담 예약 취소(ACTIVE→CANCELLED)."""
        r = CounselReservation.objects.select_related("lead").filter(id=request.GET.get("reservation_id")).first()
        if not r:
            return self.error("예약이 없습니다.")
        if not can_manage_branch(request.user, r.lead.branch_id):
            return self.error("No permission for this branch")
        r.status = CounselReservation.CANCELLED
        r.save(update_fields=["status"])
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
                    # 격주 번갈아 짝 슬롯(week_offset=1)은 시작일을 1주 밀어 반대 주차에 수업
                    af = data.get("lesson_start_date")
                    if freq == "BIWEEKLY" and row.get("week_offset") and af:
                        try:
                            af = (datetime.strptime(af, "%Y-%m-%d").date() + timedelta(days=7)).isoformat()
                        except (ValueError, TypeError):
                            pass
                    StudentTimetable.objects.create(
                        student=user, branch=lead.branch, class_type=LessonType.PRIVATE,
                        weekday=wd, start_time=tm, duration_minutes=dur,
                        program=prog, subject=subj, frequency=freq,
                        active_from=af)
            # 학부모(보호자) 계정 생성/연결 — 자녀 기록 열람용(11 §9)
            parent_user = get_or_create_guardian(
                user, lead, lead.branch,
                login_id=data.get("parent_login_id", ""),
                password=data.get("parent_password", ""))
            lead.status = LeadStatus.CONVERTED
            lead.converted_user = user
            lead.is_hidden = True  # 등록 전환 완료 시 상담 목록에서 자동 숨김
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
        # 수강 중(미종료) 과목명 집계
        subj_map = {}
        for sid, subj in StudentTimetable.objects.exclude(status="ENDED").values_list("student_id", "subject"):
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
                        "enrollment_status": (sp.enrollment_status if sp else EnrollmentStatus.ENROLLED),
                        "weekly_sessions": (sp.weekly_sessions if sp else None),
                        "guardian_count": gcounts.get(u.id, 0),
                        "subjects": subj_map.get(u.id, []),
                        "slot_count": counts.get(u.id, 0),
                        "status_history": []})
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
    """선택목록 라벨 또는 값(코드)을 value(코드)로 해석. 매칭 없으면 ''."""
    t = (text or "").strip()
    if not t:
        return ""
    for o in OptionItem.objects.filter(category=category):
        if t == o.value or t == o.label:
            return o.value
    return ""


def _parse_bulk_timetable(text):
    """'월 16:00 웹, 수 17:00 블록코딩' → ([{weekday,start_time,program,subject}], warnings[]).
    토큰: 요일 시각 [과정명]. 격주는 v1 미지원(등록 후 [시간표]에서)."""
    from datetime import time as _t
    items, warns = [], []
    text = (text or "").replace("，", ",").strip()
    if not text:
        return items, warns
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        parts = tok.split()
        if len(parts) < 2:
            warns.append("시간표 형식 오류: '%s' (요일 시각 [과정])" % tok)
            continue
        wd = _WD.index(parts[0]) if parts[0] in _WD else -1
        if wd < 0:
            warns.append("요일 인식 불가: '%s'" % parts[0])
            continue
        tm = parts[1].strip()
        try:
            hh, mm = tm.split(":")
            _t(int(hh), int(mm))
            tm = "%02d:%02d" % (int(hh), int(mm))
        except (ValueError, AttributeError):
            warns.append("시각 형식 오류: '%s' (HH:MM)" % parts[1])
            continue
        prog_text = " ".join(parts[2:]).strip()
        prog = _resolve_opt_value("program", prog_text)
        subj = resolve_program_label(prog) or prog_text or "수업"
        items.append({"weekday": wd, "start_time": tm, "program": prog, "subject": subj})
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
    tt_items, tt_warns = _parse_bulk_timetable(r.get("timetable"))
    res["warnings"] = tt_warns
    res["timetable_preview"] = ["%s %s %s" % (_WD[it["weekday"]], it["start_time"], it["subject"]) for it in tt_items]
    if ws is None and tt_items:
        ws = len(tt_items)
    progs = []
    for tok in r.get("programs", "").replace("，", ",").split(","):
        tok = tok.strip()
        if tok:
            val = _resolve_opt_value("program", tok)
            progs.append({"value": val, "language": "", "custom": ("" if val else tok)})
    resolved = {
        "name": name, "branch": branch, "login_id": login_id, "password": pw,
        "birth_date": bd, "gender": {"남": "M", "여": "F", "M": "M", "F": "F"}.get(r.get("gender", ""), ""),
        "zipcode": r.get("zipcode", ""), "address": r.get("address", ""),
        "address_detail": r.get("address_detail", ""), "student_phone": r.get("student_phone", ""),
        "parent_name": r.get("parent_name", ""), "parent_phone": r.get("parent_phone", ""),
        "school_type": _resolve_opt_value("school_type", r.get("school_type")),
        "school_name": r.get("school_name", ""), "grade": r.get("grade", ""),
        "programs": progs, "weekly_sessions": ws,
        "enrollment_date": _bulk_parse_date(r.get("enrollment_date")) or now().date(),
        "lesson_start_date": _bulk_parse_date(r.get("lesson_start_date")),
        "timetable": tt_items,
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
            StudentProfile.objects.create(
                user=user, enroll_no=gen_enroll_no(d["branch"]),
                birth_date=d["birth_date"], gender=d["gender"],
                zipcode=d["zipcode"], address=d["address"], address_detail=d["address_detail"],
                student_phone=d["student_phone"],
                parent_name=d["parent_name"], parent_phone=d["parent_phone"],
                school_type=d["school_type"], school_name=d["school_name"], grade=d["grade"],
                enrollment_date=d["enrollment_date"], enrollment_status=EnrollmentStatus.ENROLLED,
                program=(d["programs"][0]["value"] if d["programs"] else ""),
                programs=_json.dumps(d["programs"], ensure_ascii=False),
                weekly_sessions=d["weekly_sessions"], lesson_start_date=d["lesson_start_date"])
            dur = lesson_duration(d["school_type"], d["weekly_sessions"])
            for it in d["timetable"]:
                StudentTimetable.objects.create(
                    student=user, branch=d["branch"], class_type=LessonType.PRIVATE,
                    weekday=it["weekday"], start_time=it["start_time"], duration_minutes=dur,
                    program=it["program"], subject=it["subject"], frequency="WEEKLY",
                    active_from=d["lesson_start_date"])


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
        WD = _WD
        tt_map = {}
        for s in StudentTimetable.objects.exclude(status="ENDED").values(
                "student_id", "weekday", "start_time", "subject"):
            tt_map.setdefault(s["student_id"], []).append(
                "%s %s %s" % (WD[s["weekday"]] if 0 <= s["weekday"] <= 6 else "?",
                              str(s["start_time"])[:5], s["subject"] or ""))
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
                        v = pr.get("value")
                        progs.append(pg_label.get(v, v) if v else (pr.get("custom") or ""))
                except (ValueError, TypeError):
                    pass
            out.append({
                "원번": (sp.enroll_no if sp else ""), "지점": (p.branch.name if p.branch_id else ""),
                "학생이름": rn, "생년월일": (str(sp.birth_date) if (sp and sp.birth_date) else ""),
                "성별": {"M": "남", "F": "여"}.get((sp.gender if sp else ""), ""),
                "학교구분": sl_label.get((sp.school_type if sp else ""), ""),
                "학교이름": (sp.school_name if sp else ""), "학년": (sp.grade if sp else ""),
                "보호자이름": (sp.parent_name if sp else ""), "보호자연락처": (sp.parent_phone if sp else ""),
                "학생연락처": (sp.student_phone if sp else ""), "우편번호": (sp.zipcode if sp else ""),
                "주소": (sp.address if sp else ""), "상세주소": (sp.address_detail if sp else ""),
                "아이디": u.username, "비밀번호": "",
                "등록과정": ", ".join([x for x in progs if x]),
                "주횟수": (sp.weekly_sessions if (sp and sp.weekly_sessions) else ""),
                "시간표": ", ".join(tt_map.get(u.id, [])),
                "등록일": (str(sp.enrollment_date) if (sp and sp.enrollment_date) else ""),
                "수업시작일": (str(sp.lesson_start_date) if (sp and sp.lesson_start_date) else ""),
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


class StudentTimetableAdminAPI(APIView):
    """학생별 개별 수업 시간표(12) 관리. 지점 스코프."""

    def _branch_ok(self, request, branch_id):
        return can_manage_branch(request.user, branch_id)

    @admin_role_required
    def get(self, request):
        """student_id 로 특정 학생의 시간표, 또는 branch/weekday 로 지점 전체 조회.
        종료(ENDED, 퇴원 등)는 기본 숨김. show_ended=1 이면 포함(삭제 보기)."""
        qs = StudentTimetable.objects.select_related("student", "branch", "instructor")
        if request.GET.get("show_ended") != "1":
            qs = qs.exclude(status="ENDED")
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
        "enroll_no": sp.enroll_no or "",
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
        "programs": sp.programs or "",
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
        # 개별 시간표(종료 제외)
        timetables = []
        for s in StudentTimetable.objects.select_related("instructor", "branch").filter(
                student=u).exclude(status="ENDED").order_by("weekday", "start_time"):
            timetables.append({"id": s.id, "weekday": s.weekday, "start_time": str(s.start_time)[:5],
                               "duration_minutes": s.duration_minutes, "program": s.program or "",
                               "subject": s.subject or resolve_program_label(s.program) or "미지정",
                               "instructor": ({"id": s.instructor_id, "name": _name_of(s.instructor)} if s.instructor_id else None),
                               "frequency": s.frequency, "branch": ({"id": s.branch_id, "name": s.branch.name} if s.branch_id else None),
                               "status": s.status})
        return self.success({
            "id": u.id, "username": u.username, "real_name": _name_of(u),
            "branch": (prof.branch.name if prof and prof.branch_id else ""),
            "branch_id": prof.branch_id if prof else None,
            "enrollment_status": sp.enrollment_status if sp else EnrollmentStatus.ENROLLED,
            "profile": pdict, "guardians": guardians, "status_history": history,
            "timetables": timetables,
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
        reason = (data.get("reason") or "").strip()
        StudentStatusChange.objects.create(
            student=u, from_status=from_status, to_status=to_status,
            reason=reason, effective_date=data.get("effective_date") or None, actor=request.user)

        # 등록상태에 따라 개별 시간표 자동 처리(+이력)
        tt_msg = self._sync_timetables(u, to_status, request.user, reason)
        return self.success({"timetable": tt_msg})

    @staticmethod
    def _sync_timetables(student, to_status, actor, reason):
        """휴원→일시중지(PAUSED), 재원→복원(ACTIVE), 퇴원→종료(ENDED). 변경 이력 기록."""
        from ..models import TimetableStatus
        slots = StudentTimetable.objects.filter(student=student)
        changed = 0
        if to_status == EnrollmentStatus.ON_LEAVE:
            qs = slots.filter(status=TimetableStatus.ACTIVE)
            changed = qs.count()
            qs.update(status=TimetableStatus.PAUSED)
            action, label = "UPDATE", "휴원 처리 — 시간표 일시중지"
        elif to_status == EnrollmentStatus.ENROLLED:
            qs = slots.filter(status=TimetableStatus.PAUSED)
            changed = qs.count()
            qs.update(status=TimetableStatus.ACTIVE)
            action, label = "UPDATE", "재등록 — 시간표 복원"
        elif to_status == EnrollmentStatus.WITHDRAWN:
            qs = slots.exclude(status=TimetableStatus.ENDED)
            changed = qs.count()
            qs.update(status=TimetableStatus.ENDED)
            action, label = "DELETE", "퇴원 처리 — 시간표 종료"
        else:
            return ""
        if changed:
            TimetableChange.objects.create(
                student=student, actor=actor, action=action,
                reason=reason or "등록상태 변경 자동 처리",
                detail=("%s (%d건)" % (label, changed))[:255])
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
            summary=summary, counsel_at=data.get("counsel_at") or None)
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


class MsgTemplateAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """템플릿 목록. ?group_id= (없으면 전체)."""
        qs = MsgTemplate.objects.filter(is_hidden=False)
        gid = request.GET.get("group_id")
        if gid:
            qs = qs.filter(group_id=gid)
        qs = qs.order_by("order", "id")
        return self.success([{"id": t.id, "group_id": t.group_id, "title": t.title,
                              "body": t.body} for t in qs])

    @admin_role_required
    def post(self, request):
        """템플릿 추가/수정. {id?, group_id, title, body}"""
        data = request.data
        title = (data.get("title") or "").strip()
        if not title:
            return self.error("제목을 입력하세요.")
        if data.get("id"):
            t = MsgTemplate.objects.filter(id=data.get("id")).first()
            if not t:
                return self.error("템플릿이 없습니다.")
        else:
            last = MsgTemplate.objects.order_by("-order").first()
            t = MsgTemplate(order=((last.order + 1) if last else 0))
        t.group_id = data.get("group_id") or None
        t.title = title
        t.body = data.get("body") or ""
        t.save()
        return self.success({"id": t.id})

    @admin_role_required
    def delete(self, request):
        """템플릿 소프트삭제."""
        t = MsgTemplate.objects.filter(id=request.GET.get("id")).first()
        if not t:
            return self.error("템플릿이 없습니다.")
        t.is_hidden = True
        t.save(update_fields=["is_hidden"])
        return self.success(True)


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


# ── 일일 운영 대시보드(오늘 수업 + 등원/하원 출결) ──

def _hm_kst(dt):
    """저장된 UTC datetime을 KST(+9) HH:MM 문자열로."""
    if not dt:
        return ""
    return (dt + timedelta(hours=9)).strftime("%H:%M")


def ensure_occurrences(d, branch_ids=None):
    """지정일 d의 정규 수업 인스턴스를 시간표 패턴에서 생성(없는 것만). branch_ids=None이면 전체."""
    wd = d.weekday()
    slots = StudentTimetable.objects.select_related("instructor", "branch").filter(
        weekday=wd, status="ACTIVE")
    if branch_ids is not None:
        slots = slots.filter(branch_id__in=branch_ids)
    existing = set(LessonOccurrence.objects.filter(date=d, source_timetable__isnull=False)
                   .values_list("source_timetable_id", flat=True))
    for s in slots:
        # 격주: 수업 시작일 기준 짝수 주만 인스턴스 생성
        if s.frequency == "BIWEEKLY" and s.active_from:
            if ((d - s.active_from).days // 7) % 2 != 0:
                continue
        if s.id in existing:
            continue
        LessonOccurrence.objects.get_or_create(
            source_timetable=s, date=d,
            defaults={"student_id": s.student_id, "branch_id": s.branch_id,
                      "start_time": s.start_time, "duration_minutes": s.duration_minutes,
                      "program": s.program, "subject": s.subject or resolve_program_label(s.program) or "미지정",
                      "instructor_id": s.instructor_id})


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
        ensure_occurrences(d, view)
        occ = LessonOccurrence.objects.select_related("student", "instructor", "branch", "source_timetable").filter(
            date=d).exclude(status=OccurrenceStatus.CANCELLED)
        if view is not None:
            occ = occ.filter(branch_id__in=view)
        bid = request.GET.get("branch_id")
        if bid:
            occ = occ.filter(branch_id=bid)
        occ = occ.order_by("start_time")

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
                "subject": o.subject or "미지정",
                "instructor": _name_of(o.instructor) if o.instructor_id else "미배정",
                "branch": (o.branch.name if o.branch_id else ""),
                "biweekly": biweekly, "is_makeup": o.is_makeup,
                "status": o.status, "lesson_note": o.note, "no_makeup": o.no_makeup,
                "school_type": (sp.school_type if sp else ""),
                "school_name": (sp.school_name if sp else ""),
                "grade": (sp.grade if sp else ""),
                "parent_phone": (sp.parent_phone if sp else ""),
                "student_phone": (sp.student_phone if sp else ""),
            })
        att = {}
        for a in DailyAttendance.objects.filter(date=d, student_id__in=sids):
            att[a.student_id] = {"in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at),
                                 "note_tag": a.note_tag, "note": a.note}
        # 수업별 진도(있으면)
        prog = {}
        occ_ids = [l["occ_id"] for l in lessons]
        for p in LessonProgress.objects.filter(occurrence_id__in=occ_ids, is_hidden=False):
            prog[p.occurrence_id] = {"content": p.content, "homework": p.homework}
        for l in lessons:
            l["att"] = att.get(l["student_id"], {"in": "", "out": "", "note_tag": "", "note": ""})
            l["progress"] = prog.get(l["occ_id"])
        # 그날 상담 예약(KST 하루) — 위쪽 상담 일정 섹션용
        day_lo = _kst_to_utc(d, "00:00")
        day_hi = _kst_to_utc(d + timedelta(days=1), "00:00")
        rq = CounselReservation.objects.select_related("lead", "lead__branch").prefetch_related(
            "lead__logs__author").filter(
            status="ACTIVE", scheduled_at__gte=day_lo, scheduled_at__lt=day_hi)
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
                     "channel": c.channel, "summary": c.summary, "time": str(c.create_time)[:16]}
                    for c in lg.logs.all() if not c.is_hidden][:6]
            reservations.append({
                "id": rv.id, "lead_id": rv.lead_id, "time": _hm_kst(rv.scheduled_at),
                "student_name": lg.student_name, "parent_name": lg.parent_name,
                "branch": (lg.branch.name if lg.branch_id else ""), "note": rv.note,
                "school_type": lg.school_type, "school_name": lg.school_name, "grade": lg.grade,
                "parent_phone": lg.parent_phone, "logs": logs, "edits": edits})
        WD = ["월", "화", "수", "목", "금", "토", "일"]
        return self.success({"date": str(d), "weekday": WD[wd], "lessons": lessons,
                             "total": len(lessons), "present": len(att), "reservations": reservations})


def _kst_to_utc(d, hm):
    """KST 날짜 d + 'HH:MM'을 저장용 UTC aware datetime으로."""
    from datetime import time as _t
    from django.utils import timezone as _tz
    hh, mm = hm.split(":")
    naive = datetime.combine(d, _t(int(hh), int(mm))) - timedelta(hours=9)
    return _tz.make_aware(naive, _tz.utc)


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
        old = _hm_kst(getattr(a, field))
        if clear:
            setattr(a, field, None)
        elif tm:
            try:
                setattr(a, field, _kst_to_utc(d, tm))
            except (ValueError, AttributeError):
                return self.error("시간 형식이 올바르지 않습니다(HH:MM).")
        else:
            setattr(a, field, now())
        a.save()
        new = _hm_kst(getattr(a, field))
        # 시각 수정(기존값 있고 명시적 time/clear)은 이력 기록
        if (tm or clear) and old:
            AttendanceChange.objects.create(
                attendance=a, actor=request.user,
                detail="%s %s → %s" % (label, old or "-", new or "-"),
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
            AttendanceChange.objects.create(
                attendance=a, actor=request.user,
                detail="비고 변경: [%s] %s" % (a.note_tag or "-", a.note or ""),
                reason="")
        return self.success({"note_tag": a.note_tag, "note": a.note})

    @admin_role_required
    def get(self, request):
        """출결 변경 이력. student_id, date."""
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        try:
            d = datetime.strptime(request.GET.get("date"), "%Y-%m-%d").date() if request.GET.get("date") else now().date()
        except (TypeError, ValueError):
            d = now().date()
        a = DailyAttendance.objects.filter(student=u, date=d).first()
        out = []
        if a:
            for c in a.changes.select_related("actor"):
                out.append({"detail": c.detail, "reason": c.reason,
                            "actor": _name_of(c.actor) if c.actor_id else "",
                            "time": str(c.create_time)[:16]})
        return self.success(out)


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
        # 패턴 시간표를 기간 내 날짜로 펼침(생성 없이 계산만)
        slots = StudentTimetable.objects.select_related("branch", "student", "instructor").filter(status="ACTIVE")
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
        instr = request.GET.get("instructor_id")
        if instr == "__none__":
            slots = slots.filter(instructor__isnull=True)
        elif instr:
            slots = slots.filter(instructor_id=instr)
        slots = list(slots)
        # 기간 내 인스턴스 오버레이(결석/취소 상태·occ_id)
        occ_q = LessonOccurrence.objects.filter(date__gte=d0, date__lte=d1, source_timetable__isnull=False)
        if sid:
            occ_q = occ_q.filter(student_id=sid)
        overlay = {}
        for o in occ_q.values("source_timetable_id", "date", "status", "id"):
            overlay[(o["source_timetable_id"], str(o["date"]))] = o
        # 진도(있으면) occ_id별
        prog_q = LessonProgress.objects.filter(
            occurrence__date__gte=d0, occurrence__date__lte=d1, is_hidden=False)
        if sid:
            prog_q = prog_q.filter(student_id=sid)
        prog_by_occ = {p["occurrence_id"]: {"content": p["content"], "homework": p["homework"]}
                       for p in prog_q.values("occurrence_id", "content", "homework")}
        days = {}
        cur = d0
        while cur <= d1:
            wd = cur.weekday()
            items = []
            for s in slots:
                if s.weekday != wd:
                    continue
                if s.frequency == "BIWEEKLY" and s.active_from and ((cur - s.active_from).days // 7) % 2 != 0:
                    continue
                ov = overlay.get((s.id, str(cur)))
                items.append({"timetable_id": s.id, "start_time": str(s.start_time)[:5],
                              "subject": s.subject or resolve_program_label(s.program) or "미지정",
                              "weekday": s.weekday, "program": s.program,
                              "student_id": s.student_id, "student_name": _name_of(s.student),
                              "instructor": _name_of(s.instructor) if s.instructor_id else "미배정",
                              "instructor_id": s.instructor_id,
                              "branch": (s.branch.name if s.branch_id else ""), "branch_id": s.branch_id,
                              "frequency": s.frequency,
                              "status": (ov["status"] if ov else OccurrenceStatus.SCHEDULED),
                              "occ_id": (ov["id"] if ov else None),
                              "progress": (prog_by_occ.get(ov["id"]) if ov else None)})
            # 보강(makeup) 인스턴스도 포함
            mk = LessonOccurrence.objects.select_related("student").filter(date=cur, is_makeup=True)
            if sid:
                mk = mk.filter(student_id=sid)
            if view is not None:
                mk = mk.filter(branch_id__in=view)
            if bid:
                mk = mk.filter(branch_id=bid)
            for o in mk:
                items.append({"timetable_id": None, "start_time": str(o.start_time)[:5],
                              "subject": (o.subject or "보강"), "makeup": True,
                              "student_id": o.student_id, "student_name": _name_of(o.student),
                              "status": o.status, "occ_id": o.id,
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
        return self.success({"from": str(d0), "to": str(d1), "days": days, "reservations": resv})


class LessonStatusAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """수업 인스턴스 상태 변경(결석/예정 복원). {occ_id, status:'ABSENT'|'SCHEDULED', note?}"""
        data = request.data
        o = LessonOccurrence.objects.select_related("branch").filter(id=data.get("occ_id")).first()
        if not o:
            return self.error("수업이 없습니다.")
        if not can_manage_branch(request.user, o.branch_id):
            return self.error("권한이 없습니다.")
        st = data.get("status")
        if st not in (OccurrenceStatus.SCHEDULED, OccurrenceStatus.ABSENT):
            return self.error("상태 값이 올바르지 않습니다.")
        o.status = st
        if "note" in data:
            o.note = (data.get("note") or "").strip()
        if "no_makeup" in data:
            o.no_makeup = bool(data.get("no_makeup"))
        if st == OccurrenceStatus.SCHEDULED:
            o.no_makeup = False
        o.save()
        return self.success({"status": o.status, "note": o.note, "no_makeup": o.no_makeup})


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
        """보강 필요 리스트: 결석(ABSENT)인데 보강 미배정·보강 안 함 아닌 수업."""
        view = viewable_branch_ids(request.user)
        qs = LessonOccurrence.objects.select_related("student", "branch").filter(
            status=OccurrenceStatus.ABSENT, is_makeup=False, no_makeup=False)
        if view is not None:
            qs = qs.filter(branch_id__in=view)
        made = set(LessonOccurrence.objects.filter(is_makeup=True, makeup_for__isnull=False)
                   .values_list("makeup_for_id", flat=True))
        out = []
        for o in qs.order_by("-date", "start_time")[:300]:
            if o.id in made:
                continue
            out.append({"occ_id": o.id, "student_id": o.student_id, "student_name": _name_of(o.student),
                        "date": str(o.date), "start_time": str(o.start_time)[:5],
                        "subject": o.subject or "미지정", "branch": (o.branch.name if o.branch_id else "")})
        return self.success(out)


class LessonEditAdminAPI(APIView):
    @admin_role_required
    def post(self, request):
        """오늘 하루 수업 인스턴스 시각/길이 변경 + 사유(이력). {occ_id, start_time'HH:MM', duration?, reason?}"""
        data = request.data
        o = LessonOccurrence.objects.select_related("branch").filter(id=data.get("occ_id")).first()
        if not o:
            return self.error("수업이 없습니다.")
        if not can_manage_branch(request.user, o.branch_id):
            return self.error("권한이 없습니다.")
        tm = (data.get("start_time") or "").strip()
        if not tm:
            return self.error("시각을 입력하세요.")
        from datetime import time as _t
        try:
            hh, mm = tm.split(":")
            new_time = _t(int(hh), int(mm))
        except (ValueError, AttributeError):
            return self.error("시각 형식이 올바르지 않습니다(HH:MM).")
        old = str(o.start_time)[:5]
        o.start_time = new_time
        if data.get("duration"):
            o.duration_minutes = data.get("duration")
        reason = (data.get("reason") or "").strip()
        if old != tm:
            o.time_change_reason = reason or "오늘 수업 시각 변경"
        o.save()
        if old != tm:
            TimetableChange.objects.create(
                student=o.student, actor=request.user, action="UPDATE",
                reason=reason or "오늘 수업 시각 변경",
                detail=("%s 수업 시각 %s → %s (오늘 하루)" % (str(o.date), old, tm))[:255])
        return self.success({"start_time": str(o.start_time)[:5]})


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
                                 "homework": p.homework, "author": _name_of(p.author) if p.author_id else "",
                                 "time": str(p.update_time)[:16]})
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
                        "subject": (p.occurrence.subject if p.occurrence_id else ""),
                        "author": _name_of(p.author) if p.author_id else "",
                        "time": str(p.update_time)[:16]})
        return self.success(out)

    @admin_role_required
    def post(self, request):
        """진도 저장(업서트). {occ_id?, student_id, date?, content, homework}.
        occ_id 있으면 그 수업의 진도 1건을 갱신/생성, 없으면 자유 기록 생성."""
        data = request.data
        content = (data.get("content") or "").strip()
        homework = (data.get("homework") or "").strip()
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
        """보강 추가. {student_id, date, start_time'HH:MM', duration?, program?, instructor_id?,
        source_timetable_id?(정규수업), makeup_for?(결석 occ_id), note?}"""
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
        tm = (data.get("start_time") or "").strip()
        if not tm:
            return self.error("보강 시각을 입력하세요.")
        try:
            from datetime import time as _t
            hh, mm = tm.split(":")
            st_time = _t(int(hh), int(mm))
        except (ValueError, AttributeError):
            return self.error("시각 형식이 올바르지 않습니다(HH:MM).")
        src = StudentTimetable.objects.filter(id=data.get("source_timetable_id")).first()
        dur = data.get("duration") or (src.duration_minutes if src else 60)
        prog = data.get("program") or (src.program if src else "")
        subj = resolve_program_label(prog) or (src.subject if src else "") or "보강"
        instr = data.get("instructor_id")
        if instr is None and src:
            instr = src.instructor_id
        occ = LessonOccurrence.objects.create(
            student=u, branch_id=(prof.branch_id if prof else (src.branch_id if src else None)),
            source_timetable=None, date=d, start_time=st_time, duration_minutes=dur,
            program=prog, subject=subj, instructor_id=instr,
            status=OccurrenceStatus.SCHEDULED, is_makeup=True,
            makeup_for_id=data.get("makeup_for"), note=(data.get("note") or "").strip())
        return self.success({"occ_id": occ.id})
