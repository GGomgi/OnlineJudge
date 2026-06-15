from datetime import timedelta, datetime, date as date_cls

from django.utils.timezone import now


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
                      Lead, LeadStatus, CounselingLog, StudentProfile, EnrollmentStatus,
                      OptionItem, StudentTimetable, LessonType, GuardianStudent)
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
from ..services import apply_role, staff_scope, can_manage_branch


class SignupRequestAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """가입 신청 목록. 전지점 역할은 전체, 지점 역할은 자기 지점만 조회."""
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch and branch_id is None:
            return self.error("No branch scope assigned")

        qs = SignupRequest.objects.select_related("user", "requested_branch", "reviewed_by")
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        if not all_branch:
            qs = qs.filter(requested_branch_id=branch_id)

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
        data = request.data
        target = User.objects.filter(id=data["user_id"]).first()
        if not target:
            return self.error("User does not exist")

        role = data["role"]
        actor_all, actor_branch, actor_role = staff_scope(request.user)
        if role in ALL_BRANCH_ROLES and not actor_all:
            return self.error("Only HQ admin can grant this role")

        branch = None
        if role not in ALL_BRANCH_ROLES:
            if not data.get("branch_id"):
                return self.error("Branch is required for this role")
            branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
            if not branch:
                return self.error("Invalid branch")
            if not can_manage_branch(request.user, branch.id):
                return self.error("No permission for this branch")

        apply_role(target, role, branch)
        return self.success({"user_id": target.id, "role": role,
                             "branch_id": branch.id if branch else None})


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
    return {
        "user_id": u.id, "username": u.username, "real_name": real_name,
        "staff_no": profile.staff_no or u.username,
        "role": profile.role, "role_label": dict(ACADEMY_ROLE_CHOICES).get(profile.role, profile.role),
        "branch": branch, "is_active": not u.is_disabled,
    }


class StaffAdminAPI(APIView):
    @admin_role_required
    def get(self, request):
        """직원(교직원 역할) 계정 목록. 전지점 역할은 전체, 지점 역할은 자기 지점만."""
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch and branch_id is None:
            return self.error("No branch scope assigned")
        qs = AcademyProfile.objects.select_related("user", "branch").filter(role__in=STAFF_ROLES)
        if not all_branch:
            qs = qs.filter(branch_id=branch_id)
        qs = qs.order_by("branch_id", "role", "user__username")
        return self.success([_staff_brief(p) for p in qs])

    @validate_serializer(CreateStaffSerializer)
    @admin_role_required
    def post(self, request):
        """직원 계정 생성(활성). 역할/지점 부여 + admin_type 동기화."""
        data = request.data
        role = data["role"]
        if role not in STAFF_ROLES:
            return self.error("Invalid staff role")

        actor_all, actor_branch, actor_role = staff_scope(request.user)
        if role in ALL_BRANCH_ROLES and not actor_all:
            return self.error("Only HQ admin can grant this role")

        branch = None
        if role not in ALL_BRANCH_ROLES:
            if not data.get("branch_id"):
                return self.error("Branch is required for this role")
            branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
            if not branch:
                return self.error("Invalid branch")
            if not can_manage_branch(request.user, branch.id):
                return self.error("No permission for this branch")

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
        profile = AcademyProfile.objects.select_related("user", "branch").get(pk=profile.pk)
        return self.success(_staff_brief(profile))


class StaffStatusAPI(APIView):
    @validate_serializer(StaffStatusSerializer)
    @admin_role_required
    def post(self, request):
        """직원 계정 활성/비활성 전환."""
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
            if not can_manage_branch(request.user, obj.branch_id):
                return self.error("No permission for this branch")
            return self.success(CourseClassSerializer(obj).data)

        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch:
            if branch_id is None:
                return self.error("No branch scope assigned")
            qs = qs.filter(branch_id=branch_id)
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
        if not can_manage_branch(request.user, course_class.branch_id):
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
        if not can_manage_branch(request.user, course_class.branch_id):
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
        if not can_manage_branch(request.user, session.course_class.branch_id):
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
        """리드(상담 신청) 목록. 전지점 역할은 전체, 지점 역할은 자기 지점만."""
        all_branch, branch_id, role = staff_scope(request.user)
        if not all_branch and branch_id is None:
            return self.error("No branch scope assigned")
        qs = Lead.objects.select_related("branch", "converted_user").prefetch_related("logs__author")
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        if not all_branch:
            qs = qs.filter(branch_id=branch_id)
        return self.success(self.paginate_data(request, qs, LeadSerializer))


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
            next_contact_at=data.get("next_contact_at"))
        if lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.COUNSELING
            lead.save()
        return self.success(LeadSerializer(lead).data)


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
                consent_privacy=bool(data.get("consent_privacy")),
                consent_guardian_name=data.get("consent_guardian_name", "") or "",
                consent_signature=data.get("consent_signature", "") or "",
                consent_date=data.get("consent_date") or now().date(),
            )
            # 입회원 신청서의 요일/시간(class_schedule)으로 개별 시간표 자동 생성(12)
            schedule_raw = data.get("class_schedule") or ""
            try:
                schedule = _json.loads(schedule_raw) if schedule_raw else []
            except (ValueError, TypeError):
                schedule = []
            for row in schedule:
                try:
                    wd = int(row.get("day"))
                    tm = (row.get("time") or "").strip()
                except (AttributeError, TypeError, ValueError):
                    continue
                if not (0 <= wd <= 6) or not tm:
                    continue
                StudentTimetable.objects.create(
                    student=user, branch=lead.branch, class_type=LessonType.PRIVATE,
                    weekday=wd, start_time=tm, duration_minutes=60)
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
            if branch_id is None:
                return self.error("No branch scope assigned")
            qs = qs.filter(branch_id=branch_id)
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
        # 동일 학생 같은 요일/시각 중복 방지
        if StudentTimetable.objects.filter(student=student, weekday=data["weekday"],
                                           start_time=data["start_time"]).exclude(status="ENDED").exists():
            return self.error("같은 요일·시각에 이미 수업이 있습니다.")
        slot = StudentTimetable.objects.create(
            student=student, branch=branch,
            class_type=data.get("class_type") or LessonType.PRIVATE,
            weekday=data["weekday"], start_time=data["start_time"],
            duration_minutes=data.get("duration_minutes") or 60,
            instructor=instructor, subject=data.get("subject", "") or "",
            room=data.get("room", "") or "")
        slot = StudentTimetable.objects.select_related("student", "branch", "instructor").get(pk=slot.pk)
        return self.success(StudentTimetableSerializer(slot).data)

    @validate_serializer(EditStudentTimetableSerializer)
    @admin_role_required
    def put(self, request):
        data = request.data
        slot = StudentTimetable.objects.select_related("branch").filter(id=data["id"]).first()
        if not slot:
            return self.error("Timetable does not exist")
        if not self._branch_ok(request, slot.branch_id):
            return self.error("No permission for this branch")
        for f in ("weekday", "start_time", "duration_minutes", "subject", "room", "status"):
            if f in data:
                setattr(slot, f, data[f])
        if "instructor_id" in data:
            slot.instructor = User.objects.filter(id=data["instructor_id"]).first() if data["instructor_id"] else None
        slot.save()
        slot = StudentTimetable.objects.select_related("student", "branch", "instructor").get(pk=slot.pk)
        return self.success(StudentTimetableSerializer(slot).data)

    @admin_role_required
    def delete(self, request):
        slot = StudentTimetable.objects.select_related("branch").filter(id=request.GET.get("id")).first()
        if not slot:
            return self.error("Timetable does not exist")
        if not self._branch_ok(request, slot.branch_id):
            return self.error("No permission for this branch")
        slot.delete()
        return self.success("Deleted")
