from django.utils.timezone import now

from utils.api import APIView, validate_serializer

from account.decorators import admin_role_required
from account.models import User
from ..models import (AcademyProfile, AcademyRole, ALL_BRANCH_ROLES, Branch,
                      SignupRequest, SignupStatus, CourseClass, ClassEnrollment,
                      TimetableSlot)
from ..serializers import (SignupRequestSerializer, SignupApproveSerializer,
                           SignupRejectSerializer, AssignRoleSerializer,
                           CourseClassSerializer, CreateClassSerializer,
                           EditClassSerializer, EnrollSerializer,
                           EnrollmentSerializer, SetTimetableSlotSerializer)
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
