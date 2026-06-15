from django.db import transaction
from django.db.models import Q

from utils.api import APIView, validate_serializer

from account.models import User, UserProfile
from ..models import (AcademyProfile, AcademyRole, Branch, SignupRequest, CourseClass,
                      AttendanceRecord, Lead, OptionItem)
from ..models import StudentTimetable, GuardianStudent
from ..serializers import (AcademySignupSerializer, BranchSerializer,
                           SignupRequestSerializer, CourseClassSerializer,
                           ClassSessionSerializer, LeadCreateSerializer,
                           StudentTimetableSerializer)
from account.decorators import login_required


class BranchListAPI(APIView):
    def get(self, request):
        """가입 폼 등에서 사용하는 활성 지점 목록(공개)."""
        branches = Branch.objects.filter(is_active=True)
        return self.success(BranchSerializer(branches, many=True).data)


class OptionListAPI(APIView):
    def get(self, request):
        """포털 폼에서 쓰는 선택 목록(활성). 카테고리별로 묶어서 반환(공개).
        상담 신청서는 비로그인 작성이라 공개 조회를 허용한다."""
        result = {}
        for opt in OptionItem.objects.filter(is_active=True).order_by("category", "order", "id"):
            result.setdefault(opt.category, []).append(
                {"value": opt.value, "label": opt.label, "allow_custom": opt.allow_custom})
        return self.success(result)


class AcademySignupAPI(APIView):
    @validate_serializer(AcademySignupSerializer)
    def post(self, request):
        """학생/학부모 가입 신청. 비활성 계정을 생성하고 승인 대기 상태로 둔다."""
        data = request.data
        username = data["username"].lower()
        email = (data.get("email") or "").lower() or None

        if User.objects.filter(username=username).exists():
            return self.error("Username already exists")
        if email and User.objects.filter(email=email).exists():
            return self.error("Email already exists")

        branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
        if not branch:
            return self.error("Invalid branch")

        with transaction.atomic():
            user = User.objects.create(username=username, email=email, is_disabled=True)
            user.set_password(data["password"])
            user.save()
            UserProfile.objects.create(user=user, real_name=data["real_name"])
            AcademyProfile.objects.create(user=user, role=data["role"], branch=branch)
            SignupRequest.objects.create(
                user=user,
                requested_role=data["role"],
                requested_branch=branch,
                applicant_name=data["real_name"],
                contact=data.get("contact", "") or "",
                memo=data.get("memo", "") or "",
            )
        return self.success("Signup request submitted")


class LeadCreateAPI(APIView):
    @validate_serializer(LeadCreateSerializer)
    def post(self, request):
        """상담 신청서 접수(무로그인). 계정은 생성하지 않는다(등록 전환 시 생성)."""
        data = request.data
        branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
        if not branch:
            return self.error("Invalid branch")
        Lead.objects.create(
            branch=branch,
            parent_name=data["parent_name"],
            parent_phone=data["parent_phone"],
            student_name=data["student_name"],
            school_type=data.get("school_type", "") or "",
            school_name=data.get("school_name", "") or "",
            grade=data.get("grade", "") or "",
            interest=data.get("interest", "") or "",
            purpose=data.get("purpose", "") or "",
            purpose_detail=data.get("purpose_detail", "") or "",
        )
        return self.success("상담 신청이 접수되었습니다.")


class MyAcademyProfileAPI(APIView):
    @login_required
    def get(self, request):
        """로그인 사용자의 학원 역할/소속 지점. 직원 화면 기본값(지점 prefill) 등에 사용."""
        from ..models import ACADEMY_ROLE_CHOICES
        profile = AcademyProfile.objects.select_related("branch").filter(user=request.user).first()
        if not profile:
            return self.success(None)
        role_label = dict(ACADEMY_ROLE_CHOICES).get(profile.role, profile.role)
        branch = None
        if profile.branch_id and profile.branch:
            branch = {"id": profile.branch.id, "code": profile.branch.code, "name": profile.branch.name}
        return self.success({
            "role": profile.role,
            "role_label": role_label,
            "branch": branch,
            "is_all_branch": profile.is_all_branch(),
            "is_staff_role": profile.is_staff_role(),
        })


def _resolve_target(request):
    """조회 대상 사용자: 기본은 본인. 학부모가 student_id 로 자녀를 지정하면
    보호자 연결을 확인하고 그 자녀를 반환. 권한 없으면 None."""
    user = request.user
    student_id = request.GET.get("student_id")
    if not student_id:
        return user
    if str(student_id) == str(user.id):
        return user
    link = GuardianStudent.objects.filter(parent=user, student_id=student_id).select_related("student").first()
    if link:
        return link.student
    return None


class MyChildrenAPI(APIView):
    @login_required
    def get(self, request):
        """학부모 로그인 시 연결된 자녀 목록(11 §9 자녀 스위처용)."""
        links = GuardianStudent.objects.filter(parent=request.user).select_related("student")
        children = []
        for l in links:
            s = l.student
            real_name = ""
            try:
                real_name = s.userprofile.real_name or ""
            except Exception:
                real_name = ""
            children.append({"id": s.id, "username": s.username, "real_name": real_name,
                             "relation": l.relation})
        return self.success(children)


class GuardianMeAPI(APIView):
    @login_required
    def get(self, request):
        """학부모 본인 기본정보(상담 신청서 자동 채움용). 학부모가 아니면 null."""
        profile = AcademyProfile.objects.select_related("branch").filter(user=request.user).first()
        if not profile or profile.role != AcademyRole.PARENT:
            return self.success(None)
        real_name = ""
        try:
            real_name = request.user.userprofile.real_name or ""
        except Exception:
            real_name = ""
        branch = None
        if profile.branch_id and profile.branch:
            branch = {"id": profile.branch.id, "code": profile.branch.code, "name": profile.branch.name}
        return self.success({"parent_name": real_name, "parent_phone": profile.phone, "branch": branch})


class MySignupStatusAPI(APIView):
    @login_required
    def get(self, request):
        """본인 가입 신청 상태 조회."""
        req = SignupRequest.objects.filter(user=request.user).first()
        if not req:
            return self.success(None)
        return self.success(SignupRequestSerializer(req).data)


class MyTimetableAPI(APIView):
    @login_required
    def get(self, request):
        """내 시간표: 개별 수업 시간표 + 그룹(특강) 반.
        학생=본인 개별 슬롯/수강 반, 강사=담당 개별 슬롯/담당 반.
        학부모는 student_id 로 자녀 시간표 조회."""
        user = _resolve_target(request)
        if user is None:
            return self.error("권한이 없습니다.")
        # 개별 수업 시간표(학생 본인 또는 담당 강사)
        individual = StudentTimetable.objects.select_related(
            "student", "branch", "instructor").exclude(status="ENDED").filter(
            Q(student=user) | Q(instructor=user))
        # 그룹/특강 반: 담당 강사 + 수강 중(중복 제거)
        teaching = CourseClass.objects.filter(instructor=user, is_active=True)
        enrolled = CourseClass.objects.filter(
            enrollments__student=user, enrollments__is_active=True, is_active=True)
        classes = (teaching | enrolled).distinct().select_related("branch", "instructor")
        return self.success({
            "individual": StudentTimetableSerializer(individual, many=True).data,
            "groups": CourseClassSerializer(classes, many=True).data,
        })


class MyAttendanceAPI(APIView):
    @login_required
    def get(self, request):
        """본인(또는 학부모의 자녀) 출결 내역(최근순). class_id 로 특정 반 필터 가능."""
        target = _resolve_target(request)
        if target is None:
            return self.error("권한이 없습니다.")
        qs = AttendanceRecord.objects.filter(student=target).select_related(
            "session", "session__course_class", "session__course_class__branch")
        class_id = request.GET.get("class_id")
        if class_id:
            qs = qs.filter(session__course_class_id=class_id)
        qs = qs.order_by("-session__date")
        results = []
        for r in qs[:200]:
            results.append({
                "session": ClassSessionSerializer(r.session).data,
                "status": r.status,
                "memo": r.memo,
            })
        return self.success(results)
