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
from ..models import (AcademyProfile, AcademyRole, ALL_BRANCH_ROLES, Branch,
                      SignupRequest, SignupStatus, CourseClass, ClassEnrollment,
                      TimetableSlot, ClassSession, SessionStatus, AttendanceRecord,
                      Lead, LeadStatus, CounselingLog, StudentProfile, EnrollmentStatus)
from ..serializers import (SignupRequestSerializer, SignupApproveSerializer,
                           SignupRejectSerializer, AssignRoleSerializer,
                           CourseClassSerializer, CreateClassSerializer,
                           EditClassSerializer, EnrollSerializer,
                           EnrollmentSerializer, SetTimetableSlotSerializer,
                           ClassSessionSerializer, CreateSessionSerializer,
                           GenerateSessionsSerializer, AttendanceRecordSerializer,
                           MarkAttendanceSerializer, _student_brief,
                           LeadSerializer, AddCounselingNoteSerializer,
                           ConvertLeadSerializer, CloseLeadSerializer)
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
                address=data.get("address", "") or "",
                student_phone=data.get("student_phone", "") or "",
                parent_name=lead.parent_name,
                parent_phone=lead.parent_phone,
                school_type=lead.school_type,
                school_name=lead.school_name,
                grade=lead.grade,
                enrollment_date=now().date(),
                enrollment_status=EnrollmentStatus.ENROLLED,
            )
            lead.status = LeadStatus.CONVERTED
            lead.converted_user = user
            lead.save()
        return self.success(LeadSerializer(lead).data)


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
