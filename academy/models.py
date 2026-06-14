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


class CourseClass(models.Model):
    """반(class group). 지점 소속, 담당 강사, 트랙/레벨(커리큘럼 09)."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField(max_length=128)
    # 커리큘럼 09: track LANG/ALGO/BLOCK/SQL/CERT, level L1~L4 (자유 문자열로 보관)
    track = models.CharField(max_length=16, blank=True, default="")
    level = models.CharField(max_length=8, blank=True, default="")
    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="teaching_classes")
    is_active = models.BooleanField(default=True)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_class"
        ordering = ["branch_id", "name"]

    def __str__(self):
        return f"{self.branch_id}:{self.name}"


class ClassEnrollment(models.Model):
    """학생 ↔ 반 수강 관계."""
    course_class = models.ForeignKey(CourseClass, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="class_enrollments")
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_class_enrollment"
        unique_together = ("course_class", "student")


class TimetableSlot(models.Model):
    """반의 정규 주간 시간표 슬롯(요일+시작/종료)."""
    course_class = models.ForeignKey(CourseClass, on_delete=models.CASCADE, related_name="timetable_slots")
    # 0=월 ... 6=일 (Python date.weekday() 기준)
    day_of_week = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "academy_timetable_slot"
        ordering = ["day_of_week", "start_time"]


class SessionStatus(object):
    SCHEDULED = "SCHEDULED"
    DONE = "DONE"
    CANCELED = "CANCELED"


class ClassSession(models.Model):
    """반의 개별 수업 회차(날짜 단위). 출결·숙제의 기준."""
    course_class = models.ForeignKey(CourseClass, on_delete=models.CASCADE, related_name="sessions")
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default=SessionStatus.SCHEDULED)
    topic = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_class_session"
        ordering = ["-date", "start_time"]
        unique_together = ("course_class", "date", "start_time")


class AttendanceStatus(object):
    PRESENT = "PRESENT"        # 출석
    LATE = "LATE"              # 지각
    ABSENT = "ABSENT"          # 결석
    EARLY_LEAVE = "EARLY_LEAVE"  # 조퇴
    EXCUSED = "EXCUSED"        # 사유결석(인정)


ATTENDANCE_STATUS_VALUES = [
    AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ABSENT,
    AttendanceStatus.EARLY_LEAVE, AttendanceStatus.EXCUSED,
]


class AttendanceRecord(models.Model):
    """회차별 학생 출결 기록."""
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="attendances")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="attendance_records")
    status = models.CharField(max_length=16, default=AttendanceStatus.PRESENT)
    memo = models.CharField(max_length=255, blank=True, default="")
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="marked_attendances")
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_attendance_record"
        unique_together = ("session", "student")


# ── 상담 신청(리드) → 등록 전환 흐름 (80) ──

class LeadStatus(object):
    NEW = "NEW"              # 신규 접수
    COUNSELING = "COUNSELING"  # 상담 진행 중
    CONVERTED = "CONVERTED"  # 등록 전환(계정 생성)
    CLOSED = "CLOSED"        # 종결(미등록)


CONTACT_PREFERENCES = ["PHONE_OK", "MESSAGE_PREFERRED", "MESSAGE_ONLY", "KAKAO_PREFERRED"]
SCHOOL_TYPES = ["ELEMENTARY", "MIDDLE", "HIGH", "UNIVERSITY", "ETC"]

# 등록 과정(입회원 신청서). LANG 선택 시 program_language 에 세부 언어 저장.
PROGRAM_TYPES = ["LANG", "WEB", "PROJECT", "COMPETITION", "ETC"]
PROGRAM_LANGUAGES = ["Python", "C", "C++", "Java", "C#"]

# 상담 목적(필수). ETC 선택 시 purpose_detail 에 직접 입력값 저장.
COUNSELING_PURPOSES = ["SELF_DEV", "ADMISSION", "COMPETITION", "CAREER", "ETC"]


class Lead(models.Model):
    """상담 신청(리드). 계정 없이 방문 상담 시 직접 작성하는 신청서.
    등록(결제) 시점에 학생 계정으로 전환된다."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="leads")
    parent_name = models.CharField(max_length=64)
    parent_phone = models.CharField(max_length=32)
    student_name = models.CharField(max_length=64)
    school_type = models.CharField(max_length=16, blank=True, default="")
    school_name = models.CharField(max_length=64, blank=True, default="")
    grade = models.CharField(max_length=16, blank=True, default="")
    interest = models.TextField(blank=True, default="")
    contact_preference = models.CharField(max_length=24, blank=True, default="PHONE_OK")
    # 상담 목적(필수). ETC 면 purpose_detail 에 직접 입력값.
    purpose = models.CharField(max_length=24, blank=True, default="")
    purpose_detail = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, default=LeadStatus.NEW)
    converted_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="converted_from_lead")
    close_reason = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_lead"
        ordering = ["-create_time"]


class CounselingLog(models.Model):
    """리드(또는 향후 학생)에 누적되는 상담 기록 (18 타임라인 1차형)."""
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="logs")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="counseling_logs")
    channel = models.CharField(max_length=16, blank=True, default="VISIT")
    summary = models.TextField()
    next_contact_at = models.DateField(null=True, blank=True)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_counseling_log"
        ordering = ["-create_time"]


class EnrollmentStatus(object):
    ENROLLED = "ENROLLED"
    ON_LEAVE = "ON_LEAVE"
    WITHDRAWN = "WITHDRAWN"


class StudentProfile(models.Model):
    """학생 등록 정보(56 필드의 1차 구현형). 등록 전환(입회원 신청) 시 생성."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="student_profile")
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=8, blank=True, default="")  # M / F
    zipcode = models.CharField(max_length=16, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    address_detail = models.CharField(max_length=255, blank=True, default="")
    student_phone = models.CharField(max_length=32, blank=True, default="")
    parent_name = models.CharField(max_length=64, blank=True, default="")
    parent_phone = models.CharField(max_length=32, blank=True, default="")
    school_type = models.CharField(max_length=16, blank=True, default="")
    school_name = models.CharField(max_length=64, blank=True, default="")
    grade = models.CharField(max_length=16, blank=True, default="")
    enrollment_date = models.DateField(null=True, blank=True)
    enrollment_status = models.CharField(max_length=16, default=EnrollmentStatus.ENROLLED)
    # 등록 과정·교육 일정(입회원 신청서)
    program = models.CharField(max_length=16, blank=True, default="")
    program_language = models.CharField(max_length=16, blank=True, default="")
    weekly_sessions = models.PositiveSmallIntegerField(null=True, blank=True)
    # 교육 요일·시간 (회수만큼). JSON 문자열 [{"day":0,"time":"16:00"}, ...]
    class_schedule = models.TextField(blank=True, default="")
    memo = models.TextField(blank=True, default="")
    # 개인정보 수집·이용·제공 동의(법정대리인 동의서)
    consent_privacy = models.BooleanField(default=False)
    consent_guardian_name = models.CharField(max_length=64, blank=True, default="")
    consent_signature = models.TextField(blank=True, default="")  # data URL(PNG base64)
    consent_date = models.DateField(null=True, blank=True)
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_student_profile"
