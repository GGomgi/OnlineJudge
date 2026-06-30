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
    VICE_PRINCIPAL = "VICE_PRINCIPAL"        # 부원장(원장 대리: 운영 권한 동일, 인사·재무 제외)
    REGIONAL_MANAGER = "REGIONAL_MANAGER"    # 지부장(여러 지점 관리: managed_branches)
    INSTRUCTOR = "INSTRUCTOR"
    TA = "TA"
    STUDENT = "STUDENT"
    PARENT = "PARENT"
    EXTERNAL_INSTRUCTOR_ADMIN = "EXTERNAL_INSTRUCTOR_ADMIN"


ACADEMY_ROLE_CHOICES = [
    (AcademyRole.HQ_ADMIN, "본부 관리자"),
    (AcademyRole.HR_ADMIN, "인사 관리자"),
    (AcademyRole.REGIONAL_MANAGER, "지부장"),
    (AcademyRole.BRANCH_MANAGER, "원장"),
    (AcademyRole.VICE_PRINCIPAL, "부원장"),
    (AcademyRole.INSTRUCTOR, "강사"),
    (AcademyRole.TA, "조교"),
    (AcademyRole.STUDENT, "학생"),
    (AcademyRole.PARENT, "학부모"),
    (AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN, "외부 강사"),
]

# 전(全) 지점 범위 역할 (단일 지점에 묶이지 않음 → branch null 허용)
# 외부 강사는 특정 지점 소속이 아니라 본부 소속으로 둔다.
ALL_BRANCH_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN,
                    AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN}

# 교직원(관리자측) 역할
STAFF_ROLES = {
    AcademyRole.HQ_ADMIN,
    AcademyRole.HR_ADMIN,
    AcademyRole.REGIONAL_MANAGER,
    AcademyRole.BRANCH_MANAGER,
    AcademyRole.VICE_PRINCIPAL,
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
    # 지부장(REGIONAL_MANAGER) 전용: 관리 대상 지점(여러 지점). 다른 역할은 비워둠.
    managed_branches = models.ManyToManyField(Branch, blank=True, related_name="regional_managers")
    # 직원 사번(지점2+일련3, 04 명명). 직원 계정의 로그인 아이디로도 사용. 학생/학부모는 빈 값.
    staff_no = models.CharField(max_length=16, blank=True, default="")
    # 연락처(학부모 계정 매칭용: 동일 전화번호=동일 학부모, 11 §9 다자녀). 숫자만 정규화 저장.
    phone = models.CharField(max_length=32, blank=True, default="")
    prefs = models.TextField(blank=True, default="")  # 사용자 UI 설정(JSON): 삭제표시 토글 등
    is_deleted = models.BooleanField(default=False)  # 직원 소프트삭제(숨김). 데이터는 보존.
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
    is_hidden = models.BooleanField(default=False)  # 소프트 삭제(숨김, 본부만 조회)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    deleted_at = models.DateTimeField(null=True, blank=True)
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
    channel = models.CharField(max_length=16, blank=True, default="VISIT")  # 상담방법 VISIT/CALL/ETC
    summary = models.TextField()
    counsel_at = models.DateTimeField(null=True, blank=True)  # 실제 상담 일시
    next_contact_at = models.DateField(null=True, blank=True)
    is_hidden = models.BooleanField(default=False)  # 소프트 삭제(숨김)
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    edited_at = models.DateTimeField(null=True, blank=True)
    prev_summary = models.TextField(blank=True, default="")  # 직전 내용(수정 이력)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_counseling_log"
        ordering = ["-create_time"]


class CounselingLogEdit(models.Model):
    """상담기록 수정 이력(매 수정마다 직전 내용 보존). 여러 번 수정해도 전체 추적."""
    log = models.ForeignKey(CounselingLog, on_delete=models.CASCADE, related_name="edits")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    old_summary = models.TextField(blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_counseling_log_edit"
        ordering = ["-create_time"]


class CounselReservation(models.Model):
    """상담 예약(여러 건). 등록 후에도 계속 받을 수 있으며, 미래 예약이 있으면
    화면에서 '상담예약중'으로 자동 표시(예약 일시가 지나면 다시 '상담')."""
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="reservations")
    scheduled_at = models.DateTimeField()
    note = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, default=ACTIVE)  # ACTIVE / CANCELLED
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_counsel_reservation"
        ordering = ["scheduled_at"]


class EnrollmentStatus(object):
    ENROLLED = "ENROLLED"
    ON_LEAVE = "ON_LEAVE"
    WITHDRAWN = "WITHDRAWN"


class StudentProfile(models.Model):
    """학생 등록 정보(56 필드의 1차 구현형). 등록 전환(입회원 신청) 시 생성."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="student_profile")
    enroll_no = models.CharField(max_length=16, blank=True, default="")  # 원번(지점2+일련4, 등록 전환 시 자동)
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
    lesson_start_date = models.DateField(null=True, blank=True)  # 수업 시작일(시간표 표시 기준)
    # 등록 과정·교육 일정(입회원 신청서). 단일 과정(legacy) + 다중 과정(programs JSON).
    program = models.CharField(max_length=16, blank=True, default="")
    program_language = models.CharField(max_length=16, blank=True, default="")
    program_custom = models.CharField(max_length=255, blank=True, default="")  # 개인맞춤(allow_custom) 자유 입력
    programs = models.TextField(blank=True, default="")  # JSON [{"value","language","custom"}] 다중 과정
    weekly_sessions = models.PositiveSmallIntegerField(null=True, blank=True)
    # 교육 요일·시간 (회수만큼). JSON 문자열 [{"day":0,"time":"16:00"}, ...]
    class_schedule = models.TextField(blank=True, default="")
    # 기존 학원 스케줄 미정 → 교육 일정 추후 안내(요일/시간 미입력, 개별 시간표 미생성)
    schedule_pending = models.BooleanField(default=False)
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


class StaffProfile(models.Model):
    """직원 인사 정보(자체 등록). 본사/지점장이 계정만 간략 생성하고, 직원이 첫 로그인 후
    직접 작성·업로드한다(22 인적사항, 58 문서 정책의 1차 구현형)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="staff_profile")
    zipcode = models.CharField(max_length=16, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")          # 주소(필수)
    address_detail = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")             # 연락처(필수)
    resident_copy = models.CharField(max_length=255, blank=True, default="")     # 등본
    bankbook_copy = models.CharField(max_length=255, blank=True, default="")     # 통장사본
    graduation_cert = models.CharField(max_length=255, blank=True, default="")   # 졸업증명서
    transcript = models.CharField(max_length=255, blank=True, default="")        # 성적증명서
    family_relation_cert = models.CharField(max_length=255, blank=True, default="")  # 가족관계증명서(피부양자 공통 1장)
    # 4대보험 피부양자: 등록 여부 확정 + 목록 [{"name","relation","rrn"}]
    dependents_decided = models.BooleanField(default=False)
    dependents = models.TextField(blank=True, default="")
    # 비상연락망 [{"name","relation","phone"}]
    emergency_contacts = models.TextField(blank=True, default="")
    # 성범죄조회 동의서(추후 양식·출력). 우선 동의/서명만 수집.
    sex_offense_consent = models.BooleanField(default=False)
    sex_offense_signature = models.TextField(blank=True, default="")
    sex_offense_date = models.DateField(null=True, blank=True)
    # 고정 서류 필드별 업로드 시각 {field: "YYYY-MM-DD HH:MM"}
    file_uploaded_at = models.TextField(blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_staff_profile"

    def is_complete(self):
        import json as _j
        try:
            deps = _j.loads(self.dependents) if self.dependents else []
        except (ValueError, TypeError):
            deps = []
        try:
            emer = _j.loads(self.emergency_contacts) if self.emergency_contacts else []
        except (ValueError, TypeError):
            emer = []
        # 피부양자가 있으면 가족관계증명서(공통 1장) 필요. 없으면 확인 체크만.
        deps_ok = self.dependents_decided and all(d.get("name") for d in deps) \
            and (not deps or bool(self.family_relation_cert))
        return bool(self.address and self.phone and self.resident_copy and self.bankbook_copy
                    and self.graduation_cert and self.transcript and self.sex_offense_consent
                    and self.sex_offense_signature and deps_ok and len(emer) >= 1)


class StaffDocument(models.Model):
    """직원 계약서·서류(근로/연봉/근로서약서 등). 관리자가 업로드·관리. 서류함(group)으로 묶고
    visible_to_staff 면 본인에게도 노출(기본 관리자만)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="staff_documents")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="uploaded_staff_documents")
    group = models.CharField(max_length=64, blank=True, default="")   # 서류함
    title = models.CharField(max_length=128, blank=True, default="")  # 설명/문서명
    url = models.CharField(max_length=255)
    doc_date = models.DateField(null=True, blank=True)                 # 작성일
    order = models.PositiveSmallIntegerField(default=0)
    visible_to_staff = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)             # 업로드일

    class Meta:
        db_table = "academy_staff_document"
        ordering = ["group", "order", "id"]


class StaffProfileHistory(models.Model):
    """직원 인사 정보 변경 이력(누가·항목·전→후)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="staff_history")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    field = models.CharField(max_length=64)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_staff_history"
        ordering = ["-create_time"]


class HRNotice(models.Model):
    """인사 변경 통보(관리자 쪽지). 직원이 4대보험 피부양자 등 민감 항목을 수정하면
    소속 지점 관리자(및 본사)에게 통보된다."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="hr_notices")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="hr_notices")
    kind = models.CharField(max_length=32, default="DEPENDENTS")
    message = models.CharField(max_length=255, blank=True, default="")
    is_read = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_hr_notice"
        ordering = ["-create_time"]


class GuardianStudent(models.Model):
    """학부모(보호자) 계정 ↔ 학생 계정 1:N 매핑(11 §9). 동일 전화번호의 학부모는
    하나의 계정으로 다자녀를 연결한다."""
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="children_links")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="guardian_links")
    relation = models.CharField(max_length=16, blank=True, default="학부모")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_guardian_student"
        unique_together = ("parent", "student")


class DevRequest(models.Model):
    """개발 요청 게시판 글. 모든 로그인 사용자 작성 가능, 상태는 관리자만 변경."""
    NONE = "NONE"            # 접수
    REVIEWING = "REVIEWING"  # 검토중
    IN_PROGRESS = "IN_PROGRESS"  # 개발중
    CONFIRMED = "CONFIRMED"  # 확인함
    DONE = "DONE"            # 해결
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="dev_requests")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")  # 마크다운
    status = models.CharField(max_length=16, default=NONE)
    is_hidden = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_dev_request"
        ordering = ["-create_time"]


class DevRequestComment(models.Model):
    """개발 요청 글의 덧글."""
    request = models.ForeignKey(DevRequest, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    is_hidden = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_dev_request_comment"
        ordering = ["create_time"]


class Notification(models.Model):
    """개인 알림(헤더 종). 내 개발요청 글에 덧글/상태변동 등이 생기면 적립."""
    COMMENT = "COMMENT"      # 내 글에 덧글
    STATUS = "STATUS"        # 내 글 상태 변동
    MESSAGE = "MESSAGE"      # 쪽지 도착
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=16)
    text = models.CharField(max_length=255, blank=True, default="")
    link_type = models.CharField(max_length=16, blank=True, default="")  # 'dev' / 'message'
    link_id = models.IntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_notification"
        ordering = ["-create_time"]


class StudentCredential(models.Model):
    """학생 사이트 계정(스크래치 등). 어린 학생이 자주 잊어 학원에서 관리.
    사이트/아이디/비밀번호를 줄 단위로 저장(별도 목록화 없이 자유 입력)."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="site_credentials")
    site = models.CharField(max_length=64, blank=True, default="")
    login_id = models.CharField(max_length=128, blank=True, default="")
    password = models.CharField(max_length=128, blank=True, default="")
    order = models.PositiveSmallIntegerField(default=0)
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_student_credential"
        ordering = ["order", "id"]


class StudentStatusChange(models.Model):
    """학생 등록상태 변경 이력(재원↔휴원↔퇴원↔재등록). 휴원/퇴원 모아보기·재등록 관리·
    안내문자 연계의 근거 자료로 영구 보존한다."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="status_changes")
    from_status = models.CharField(max_length=16, blank=True, default="")
    to_status = models.CharField(max_length=16)
    reason = models.CharField(max_length=255, blank=True, default="")
    effective_date = models.DateField(null=True, blank=True)  # 휴원/퇴원/재등록 적용일
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_student_status_change"
        ordering = ["-create_time"]


class OccurrenceStatus(object):
    SCHEDULED = "SCHEDULED"   # 예정(정규 또는 보강)
    ABSENT = "ABSENT"         # 결석
    CANCELLED = "CANCELLED"   # 취소


class LessonOccurrence(models.Model):
    """일자별 수업 인스턴스. 정규 시간표(패턴)에서 날짜마다 생성되거나, 보강으로 직접 추가.
    수업 상태(예정/결석/보강)를 이 인스턴스에 기록. 등원/하원 출결은 일자별(DailyAttendance)."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="lesson_occurrences")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source_timetable = models.ForeignKey("StudentTimetable", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="occurrences")
    date = models.DateField()
    start_time = models.TimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    program = models.CharField(max_length=32, blank=True, default="")
    subject = models.CharField(max_length=64, blank=True, default="")
    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=16, default=OccurrenceStatus.SCHEDULED)
    is_makeup = models.BooleanField(default=False)          # 보강 수업 여부
    makeup_for = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="makeups")   # 어떤 결석에 대한 보강인지
    no_makeup = models.BooleanField(default=False)          # 결석이지만 보강 안 함(학부모 미희망)
    note = models.CharField(max_length=255, blank=True, default="")  # 결석/보강 사유
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_lesson_occurrence"
        unique_together = ("source_timetable", "date")
        ordering = ["date", "start_time"]


class DailyAttendance(models.Model):
    """일일 등원/하원 출결(개별 수업 운영용). 학생·날짜 1건."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="daily_attendances")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    date = models.DateField()
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    note_tag = models.CharField(max_length=32, blank=True, default="")  # 출결 비고 표시(선택목록 value)
    note = models.CharField(max_length=255, blank=True, default="")     # 긴 사유
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_daily_attendance"
        unique_together = ("student", "date")


class AttendanceChange(models.Model):
    """출결(등원/하원 시각·비고) 변경 이력."""
    attendance = models.ForeignKey(DailyAttendance, on_delete=models.CASCADE, related_name="changes")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    detail = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_attendance_change"
        ordering = ["-create_time"]


class StaffChangeLog(models.Model):
    """직원 변경 이력 통합(역할/지점/활성·비활성/사번 재발급). 사유 포함."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="staff_changes")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    change_type = models.CharField(max_length=16)   # ROLE / BRANCH / ACTIVE / SABUN
    detail = models.CharField(max_length=255, blank=True, default="")  # 기존 → 변경
    reason = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_staff_change_log"
        ordering = ["-create_time"]


# ── 관리자 편집 가능 선택 목록(옵션 마스터) ──

class OptionCategory(object):
    """포털 드롭다운에 쓰이는 선택 목록 카테고리 코드."""
    PROGRAM = "program"                    # 입회원: 등록 과정
    PROGRAM_LANGUAGE = "program_language"  # 입회원: 언어
    SCHOOL_TYPE = "school_type"            # 상담: 학교 구분
    COUNSELING_PURPOSE = "counseling_purpose"  # 상담: 상담 목적
    ATTENDANCE_NOTE = "attendance_note"    # 출결: 비고 표시(색상 태그)


OPTION_CATEGORIES = [
    (OptionCategory.PROGRAM, "등록 과정"),
    (OptionCategory.PROGRAM_LANGUAGE, "언어"),
    (OptionCategory.SCHOOL_TYPE, "학교 구분"),
    (OptionCategory.COUNSELING_PURPOSE, "상담 목적"),
    (OptionCategory.ATTENDANCE_NOTE, "출결 비고"),
]
OPTION_CATEGORY_VALUES = [c[0] for c in OPTION_CATEGORIES]


# ── 개별 수업 시간표 (12) ──

class LessonType(object):
    PRIVATE = "PRIVATE"  # 개별 수업(기본)
    GROUP = "GROUP"      # 그룹/특강


class TimetableStatus(object):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class TimetableFrequency(object):
    WEEKLY = "WEEKLY"      # 매주
    BIWEEKLY = "BIWEEKLY"  # 격주(2주에 1회, 과정 번갈아 수강 등)


class StudentTimetable(models.Model):
    """학생별 개별 수업 시간표 슬롯(12). 학원 기본 운영이 개별 수업이므로
    반(CourseClass)과 별개로 학생마다 요일/시작시간/수업길이/담당강사를 둔다.
    그룹/특강은 기존 반(CourseClass)으로 운영."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="timetables")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="student_timetables")
    class_type = models.CharField(max_length=16, default=LessonType.PRIVATE)
    # 0=월 ... 6=일 (date.weekday() 기준)
    weekday = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="instructing_timetables")
    program = models.CharField(max_length=32, blank=True, default="")  # 등록 과정 코드(과목)
    subject = models.CharField(max_length=64, blank=True, default="")  # 표시용 과정명(라벨)
    frequency = models.CharField(max_length=16, default=TimetableFrequency.WEEKLY)  # 매주/격주
    active_from = models.DateField(null=True, blank=True)  # 수업 시작일
    room = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, default=TimetableStatus.ACTIVE)
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_student_timetable"
        ordering = ["weekday", "start_time"]


class TimetableChange(models.Model):
    """개별 시간표 변경 이력(언제·누가·무슨 이유). 생성/수정/삭제 기록."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="timetable_changes")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=16)  # CREATE/UPDATE/DELETE
    reason = models.CharField(max_length=255, blank=True, default="")
    detail = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_timetable_change"
        ordering = ["-create_time"]


class OptionItem(models.Model):
    """포털 선택 목록(드롭다운) 항목. 관리자가 수정·추가·삭제한다.
    `value` 는 레코드에 저장되는 코드, `label` 은 화면 표시명.
    `allow_custom` 이면 해당 항목 선택 시 자유 입력란을 노출한다(예: 개인맞춤·직접 입력)."""
    category = models.CharField(max_length=32)
    value = models.CharField(max_length=32)
    label = models.CharField(max_length=64)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    allow_custom = models.BooleanField(default=False)
    color = models.CharField(max_length=16, blank=True, default="")  # 태그 색(예: #f59e0b), 출결 비고 등
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_option_item"
        unique_together = ("category", "value")
        ordering = ["category", "order", "id"]

    def __str__(self):
        return f"{self.category}:{self.value}"
