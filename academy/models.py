from django.conf import settings
from django.db import models


class Branch(models.Model):
    """학원 지점. 코드/표시명은 04 네이밍 정책 기준."""
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_branch"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class AcademyRole(object):
    HQ_ADMIN = "HQ_ADMIN"
    HR_ADMIN = "HR_ADMIN"
    BRANCH_MANAGER = "BRANCH_MANAGER"
    INSTRUCTOR = "INSTRUCTOR"
    TA = "TA"
    STUDENT = "STUDENT"
    PARENT = "PARENT"
    EXTERNAL_INSTRUCTOR_ADMIN = "EXTERNAL_INSTRUCTOR_ADMIN"


ACADEMY_ROLE_CHOICES = [
    (AcademyRole.HQ_ADMIN, "본부 관리자"),
    (AcademyRole.HR_ADMIN, "인사 관리자"),
    (AcademyRole.BRANCH_MANAGER, "지점장/원장"),
    (AcademyRole.INSTRUCTOR, "강사"),
    (AcademyRole.TA, "조교"),
    (AcademyRole.STUDENT, "학생"),
    (AcademyRole.PARENT, "학부모"),
    (AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN, "외부 강사 관리자"),
]

# 전(全) 지점 범위 역할 (단일 지점에 묶이지 않음 → branch null 허용)
ALL_BRANCH_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN}

# 교직원(관리자측) 역할
STAFF_ROLES = {
    AcademyRole.HQ_ADMIN,
    AcademyRole.HR_ADMIN,
    AcademyRole.BRANCH_MANAGER,
    AcademyRole.INSTRUCTOR,
    AcademyRole.TA,
    AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN,
}

# 포털(피교육자측) 역할
PORTAL_ROLES = {AcademyRole.STUDENT, AcademyRole.PARENT}


class AcademyProfile(models.Model):
    """기존 account.User 를 건드리지 않고 학원 역할/지점 스코프를 1:1로 확장."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="academy_profile")
    role = models.CharField(max_length=32, default=AcademyRole.STUDENT)
    # 주(主) 소속 지점. 전지점 역할(HQ/HR) 또는 미배정은 null.
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="members")
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_profile"

    def __str__(self):
        return f"{self.user_id}:{self.role}"

    def is_all_branch(self):
        return self.role in ALL_BRANCH_ROLES

    def is_staff_role(self):
        return self.role in STAFF_ROLES

    def is_student(self):
        return self.role == AcademyRole.STUDENT

    def is_parent(self):
        return self.role == AcademyRole.PARENT


class SignupStatus(object):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# 본인 가입(self-signup)으로 신청 가능한 역할 (교직원은 관리자가 생성/부여)
SELF_SIGNUP_ROLES = {AcademyRole.STUDENT, AcademyRole.PARENT}


class SignupRequest(models.Model):
    """가입 신청. 신청 시 비활성(User.is_disabled=True) 계정을 함께 생성하고,
    관리자가 지점/역할을 확정하며 승인하면 활성화한다."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="signup_request")
    requested_role = models.CharField(max_length=32, default=AcademyRole.STUDENT)
    requested_branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="signup_requests")
    # 검토 편의를 위한 신청자 정보 사본
    applicant_name = models.CharField(max_length=64)
    contact = models.CharField(max_length=32, blank=True, default="")
    memo = models.TextField(blank=True, default="")

    status = models.CharField(max_length=16, default=SignupStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="reviewed_signups")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.TextField(blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_signup_request"
        ordering = ["-create_time"]
