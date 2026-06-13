from django.db import transaction

from utils.api import APIView, validate_serializer

from account.models import User, UserProfile
from ..models import (AcademyProfile, Branch, SignupRequest, CourseClass,
                      AttendanceRecord)
from ..serializers import (AcademySignupSerializer, BranchSerializer,
                           SignupRequestSerializer, CourseClassSerializer,
                           ClassSessionSerializer)
from account.decorators import login_required


class BranchListAPI(APIView):
    def get(self, request):
        """가입 폼 등에서 사용하는 활성 지점 목록(공개)."""
        branches = Branch.objects.filter(is_active=True)
        return self.success(BranchSerializer(branches, many=True).data)


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
        """내 시간표: 학생은 수강 중인 반, 강사는 담당 반의 반·시간표를 반환."""
        user = request.user
        # 담당 강사로 배정된 반 + 수강 중인 반(중복 제거)
        teaching = CourseClass.objects.filter(instructor=user, is_active=True)
        enrolled = CourseClass.objects.filter(
            enrollments__student=user, enrollments__is_active=True, is_active=True)
        classes = (teaching | enrolled).distinct().select_related("branch", "instructor")
        return self.success(CourseClassSerializer(classes, many=True).data)


class MyAttendanceAPI(APIView):
    @login_required
    def get(self, request):
        """본인 출결 내역(최근순). class_id 로 특정 반 필터 가능."""
        qs = AttendanceRecord.objects.filter(student=request.user).select_related(
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
