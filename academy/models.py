from django.conf import settings
from django.db import models


class Branch(models.Model):
    """학원 지점. 코드/표시명은 04 네이밍 정책 기준."""
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    kiosk_token = models.CharField(max_length=32, blank=True, default="")  # 출결 키오스크 접속 토큰(무로그인)
    kiosk_pin = models.CharField(max_length=6, blank=True, default="")      # 키오스크 진입 PIN(6자리, 포털 첫 화면에서 지점 자동 선택)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_branch"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class KioskDeviceStatus(object):
    PENDING = "PENDING"    # 승인 대기(기기가 처음 접속해서 등록 요청)
    APPROVED = "APPROVED"  # 승인됨(사용 가능)
    REVOKED = "REVOKED"    # 승인 취소/삭제


class KioskDevice(models.Model):
    """출결 키오스크 신뢰 기기(브라우저) 등록. 지점 토큰만으로는 아무 기기나 접속 가능해서,
    승인된 기기(브라우저)에서만 실제 조회/체크가 되도록 하는 추가 잠금."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="kiosk_devices")
    device_id = models.CharField(max_length=64)  # 클라이언트(브라우저 localStorage)가 생성한 무작위 식별자
    label = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, default=KioskDeviceStatus.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "academy_kiosk_device"
        unique_together = ("branch", "device_id")
        ordering = ["-requested_at"]


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
    # 근무 형태. 불규칙(아르바이트)은 정해진 요일·시각이 없어 지각·결근을 따지지 않는다.
    work_type = models.CharField(max_length=16, default="FIXED")
    hourly_wage = models.PositiveIntegerField(null=True, blank=True)   # 시급(원) — 불규칙 근무자
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
    memo = models.TextField(blank=True, default="")
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
    # 등록 링크(학부모 원격 작성). 토큰=그 리드 신청서 1건 작성 권한.
    enroll_token = models.CharField(max_length=48, blank=True, default="", db_index=True)
    enroll_token_expires = models.DateTimeField(null=True, blank=True)
    enroll_status = models.CharField(max_length=16, blank=True, default="")  # ''(없음)/SENT/SUBMITTED
    enroll_data = models.TextField(blank=True, default="")  # 학부모 제출 인적사항·동의(JSON)
    enroll_submitted_at = models.DateTimeField(null=True, blank=True)
    enroll_edit_log = models.TextField(blank=True, default="")  # 학부모 재작성(수정) 이력 JSON [{time,changes}]
    enroll_edited = models.BooleanField(default=False)  # 제출 후 학부모가 수정함(직원 미확인) 플래그
    edit_log = models.TextField(blank=True, default="")  # 기본정보 수정 이력 JSON [{time,by,changes}]
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
    화면에서 '상담예약중'으로 자동 표시(예약 일시가 지나면 다시 '상담').
    상담 후에는 기록작성(→DONE, 상담기록 연결)/변경(사유 기록)/취소(사유 기록) 중 하나로 처리한다."""
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    DONE = "DONE"
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="reservations")
    scheduled_at = models.DateTimeField()
    note = models.TextField(blank=True, default="")
    channel = models.CharField(max_length=16, blank=True, default="VISIT")  # 방문/전화/기타(CounselingLog와 동일 체계)
    status = models.CharField(max_length=16, default=ACTIVE)  # ACTIVE / CANCELLED / DONE
    cancel_reason = models.CharField(max_length=255, blank=True, default="")
    completed_log = models.ForeignKey(CounselingLog, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    edit_log = models.TextField(blank=True, default="")  # 수정 이력 JSON [{time,by,old_at,old_note,reason}]
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
    parent_relation = models.CharField(max_length=32, blank=True, default="")   # 보호자 관계(어머니/아버지/기타 직접입력)
    notify_optin = models.BooleanField(default=False)                            # 등하원 알림톡 수신 여부
    guardian2_phone = models.CharField(max_length=32, blank=True, default="")    # 기타 보호자 휴대폰
    guardian2_relation = models.CharField(max_length=32, blank=True, default="") # 기타 보호자 관계
    school_type = models.CharField(max_length=16, blank=True, default="")
    school_name = models.CharField(max_length=64, blank=True, default="")
    grade = models.CharField(max_length=16, blank=True, default="")
    legacy_url = models.CharField(max_length=500, blank=True, default="")  # 기존 관리 시트 등 이전 기록 링크
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
    edit_log = models.TextField(blank=True, default="")  # 인적사항 수정 이력 JSON [{time,by,items:[{label,old,new}]}]
    # 개인정보 수집·이용·제공 동의(법정대리인 동의서)
    consent_privacy = models.BooleanField(default=False)
    consent_guardian_name = models.CharField(max_length=64, blank=True, default="")
    consent_signature = models.TextField(blank=True, default="")  # data URL(PNG base64)
    consent_date = models.DateField(null=True, blank=True)
    # 포털 전에 종이로 받아 둔 동의. 서명 그림은 없지만 동의는 실제로 존재한다.
    # (온라인 서명이 없다고 '안 받음'으로 두면 정보 미완료가 영영 안 지워진다)
    consent_paper = models.BooleanField(default=False)
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
    # 원장이 '해당사항 없음'으로 표시한 서류 목록(JSON). 조교·아르바이트는 성적증명서
    # 같은 것이 필요 없을 수 있다. 면제한 사람과 때는 인사 이력에 남는다.
    waived_docs = models.TextField(blank=True, default="")
    sex_offense_date = models.DateField(null=True, blank=True)
    # 고정 서류 필드별 업로드 시각 {field: "YYYY-MM-DD HH:MM"}
    file_uploaded_at = models.TextField(blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_staff_profile"

    def waived_set(self):
        """원장이 '해당사항 없음'으로 표시한 서류. 조교·아르바이트는 성적증명서 같은 것이
        필요 없을 수 있어 서류마다 면제할 수 있게 한다."""
        import json as _j
        try:
            v = _j.loads(self.waived_docs) if self.waived_docs else []
        except (ValueError, TypeError):
            v = []
        return set(v if isinstance(v, list) else [])

    def missing_items(self):
        """무엇이 안 됐는지 목록. 미완료라고만 하면 어디를 봐야 할지 알 수 없다."""
        import json as _j
        try:
            deps = _j.loads(self.dependents) if self.dependents else []
        except (ValueError, TypeError):
            deps = []
        try:
            emer = _j.loads(self.emergency_contacts) if self.emergency_contacts else []
        except (ValueError, TypeError):
            emer = []
        w = self.waived_set()
        out = []
        for field, ok, label in (
            ("address", self.address, "주소"), ("phone", self.phone, "연락처"),
            ("resident_copy", self.resident_copy, "주민등록등본"),
            ("bankbook_copy", self.bankbook_copy, "통장 사본"),
            ("graduation_cert", self.graduation_cert, "졸업증명서"),
            ("transcript", self.transcript, "성적증명서"),
            ("sex_offense_consent", self.sex_offense_consent, "성범죄 조회 동의"),
            ("sex_offense_signature", self.sex_offense_signature, "성범죄 조회 서명"),
            ("dependents_decided", self.dependents_decided, "피부양자 확인"),
            ("dependents", all(d.get("name") for d in deps), "피부양자 이름"),
            ("family_relation_cert", (not deps) or self.family_relation_cert, "가족관계증명서"),
            ("emergency_contacts", len(emer) >= 1, "비상 연락처"),
        ):
            if field in w:
                continue                # 원장이 해당사항 없음으로 둔 것
            if not ok:
                out.append(label)
        return out

    def is_complete(self):
        return not self.missing_items()

    def _is_complete_old(self):
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


# 사용 이력에 변수명이 그대로 나오면 읽을 수가 없다. 사람이 읽는 이름과 값으로 옮긴다.
STAFF_FIELD_LABELS = {
    "zipcode": "우편번호", "address": "주소", "address_detail": "상세주소",
    "phone": "연락처", "resident_copy": "주민등록등본", "bankbook_copy": "통장 사본",
    "graduation_cert": "졸업증명서", "transcript": "성적증명서",
    "family_relation_cert": "가족관계증명서",
    "dependents": "피부양자", "dependents_decided": "피부양자 확인",
    "emergency_contacts": "비상연락망",
    "sex_offense_consent": "성범죄 조회 동의", "sex_offense_signature": "성범죄 조회 서명",
    "sex_offense_date": "성범죄 조회 동의일", "waived_docs": "서류 해당사항 없음",
    "bank_name": "은행", "bank_account": "계좌번호", "bank_holder": "예금주",
    "work_type": "근무 형태", "hourly_wage": "시급",
}
# 파일은 경로를 보여 봐야 소용이 없다. 올렸는지 지웠는지만 알면 된다.
_STAFF_FILE_FIELDS = {"resident_copy", "bankbook_copy", "graduation_cert", "transcript",
                      "family_relation_cert", "sex_offense_signature"}


def staff_field_label(field):
    return STAFF_FIELD_LABELS.get(field, field)


def staff_value_text(field, raw):
    """이력에 저장된 값을 사람이 읽는 말로. 못 알아보면 원래 값을 그대로 둔다."""
    import json as _j
    v = "" if raw is None else str(raw).strip()
    if v in ("", "-"):
        return "(없음)"
    if field in _STAFF_FILE_FIELDS:
        return "올림"
    if v in ("True", "False"):
        return "예" if v == "True" else "아니오"
    if v.startswith("[") or v.startswith("{"):
        try:
            data = _j.loads(v)
        except (ValueError, TypeError):
            return v
        if isinstance(data, list) and not data:
            return "(없음)"
        if field == "waived_docs" and isinstance(data, list):
            return ", ".join(staff_field_label(x) for x in data) + " 해당사항 없음"
        if isinstance(data, list):
            out = []
            for it in data:
                if not isinstance(it, dict):
                    out.append(str(it))
                    continue
                nm = (it.get("name") or "").strip()
                rel = (it.get("relation") or "").strip()
                ph = (it.get("phone") or "").strip()
                s = nm + ("(%s)" % rel if rel else "")
                if ph:
                    s += " " + ph
                out.append(s.strip() or "-")
            return ", ".join(out)
        return v
    return v


class StaffProfileHistory(models.Model):
    """직원 인사 정보 변경 이력(누가·항목·전→후)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="staff_history")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    field = models.CharField(max_length=64)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    # 남의 정보를 고칠 때 왜 고쳤는지. 직원이 이력에서 이 문장을 보고 납득한다.
    reason = models.TextField(blank=True, default="")
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


class MsgTemplateGroup(models.Model):
    """문자 템플릿 폴더(그룹). 예: 방학안내·특강안내·원비안내."""
    name = models.CharField(max_length=64)
    order = models.PositiveSmallIntegerField(default=0)
    is_hidden = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_msg_template_group"
        ordering = ["order", "id"]


class MsgTemplate(models.Model):
    """자주 쓰는 문자 템플릿. 본문에 {변수} 토큰 사용(학생 연결 시 자동 채움)."""
    group = models.ForeignKey(MsgTemplateGroup, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="templates")
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True, default="")
    order = models.PositiveSmallIntegerField(default=0)
    is_hidden = models.BooleanField(default=False)
    edit_log = models.TextField(blank=True, default="")  # 수정 이력 JSON(원장+ 열람)
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_msg_template"
        ordering = ["order", "id"]


class FixedTemplate(models.Model):
    """사이트 전용 고정 문자 템플릿(용도별, 지점별 내용). 제목(용도)은 코드로 고정,
    내용만 지점 원장이 자기 지점 것을 수정(본부는 전 지점). 예: 등록 링크 안내."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="fixed_templates")
    key = models.CharField(max_length=32)   # 용도 키(enroll_link 등)
    body = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_fixed_template"
        unique_together = ("branch", "key")


class Message(models.Model):
    """직원 간 1:1 쪽지. 보낸/받은 각 측에서 소프트삭제(상대에겐 영향 없음)."""
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="sent_messages")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="received_messages")
    body = models.TextField()
    is_read = models.BooleanField(default=False)
    sender_hidden = models.BooleanField(default=False)
    recipient_hidden = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_message"
        ordering = ["-create_time"]


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
    reason = models.TextField(blank=True, default="")
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
    LEAVE = "LEAVE"           # 임시휴원(기간/날짜 지정 — 결석과 달리 애초에 수업 대상이 아니었음)
    HOLIDAY = "HOLIDAY"       # 학원 휴무일(공휴일·방학 등 — 학원이 쉰 날이라 결석률에서 제외)


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
    no_makeup = models.BooleanField(default=False)
    # 보강 안 함일 때의 구분: HOMEWORK=숙제로 대체 / NONE=숙제도 없음 (빈값=옛 데이터, 미구분)
    no_makeup_kind = models.CharField(max_length=16, blank=True, default="")          # 결석이지만 보강 안 함(학부모 미희망)
    note = models.TextField(blank=True, default="")  # 결석/보강 사유
    time_change_reason = models.CharField(max_length=255, blank=True, default="")  # 오늘 하루 시각 변경 사유
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_lesson_occurrence"
        unique_together = ("source_timetable", "date")
        ordering = ["date", "start_time"]


class LessonProgress(models.Model):
    """개별 진도 기록. 수업 1회(인스턴스)당 1건이 기본이며, 학생 상세에서 직접
    추가한 자유 기록은 occurrence 없이 날짜로 남긴다. 수업내용 + 숙제 + 피드백 + 비고."""
    occurrence = models.OneToOneField(LessonOccurrence, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="progress")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="lesson_progress")
    date = models.DateField()
    content = models.TextField(blank=True, default="")    # 수업 내용
    homework = models.TextField(blank=True, default="")   # 숙제
    feedback = models.TextField(blank=True, default="")   # 피드백(학부모에게 그대로 전달되는 내용)
    memo = models.TextField(blank=True, default="")       # 비고(내부 참고용 — 학부모에게 전달하지 않음)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    is_hidden = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_lesson_progress"
        ordering = ["-date", "-id"]


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
    note = models.TextField(blank=True, default="")     # 긴 사유
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
    reason = models.TextField(blank=True, default="")
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
    reason = models.TextField(blank=True, default="")
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
    active_from = models.DateField(null=True, blank=True)  # 수업 시작일(이 날짜부터 적용)
    active_until = models.DateField(null=True, blank=True)  # 마지막 적용일(이 날짜까지, 없으면 무기한)
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
    reason = models.TextField(blank=True, default="")
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


# ── 학원 휴무일 / 직원 근태 ──

class HolidayKind(object):
    PUBLIC = "PUBLIC"            # 공휴일
    SUBSTITUTE = "SUBSTITUTE"    # 대체공휴일
    VACATION = "VACATION"        # 학원 방학
    FOUNDATION = "FOUNDATION"    # 개원기념일
    TEMP = "TEMP"                # 임시휴무


HOLIDAY_KIND_CHOICES = [
    (HolidayKind.PUBLIC, "공휴일"),
    (HolidayKind.SUBSTITUTE, "대체공휴일"),
    (HolidayKind.VACATION, "학원 방학"),
    (HolidayKind.FOUNDATION, "개원기념일"),
    (HolidayKind.TEMP, "임시휴무"),
]


class Holiday(models.Model):
    """학원 휴무일. 등록하면 그날 수업이 자동으로 '휴무' 상태가 되고, 삭제하면 되돌린다.
    branch 가 비어 있으면 전 지점 공통(공휴일 등), 지정하면 그 지점만 쉰다."""
    date = models.DateField()
    name = models.CharField(max_length=64)                       # 광복절, 여름방학 등
    kind = models.CharField(max_length=16, default=HolidayKind.PUBLIC)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE,
                               related_name="holidays")
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    # 소프트삭제(모든 삭제는 기록을 남긴다)
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    deleted_at = models.DateTimeField(null=True, blank=True)
    delete_reason = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_holiday"
        ordering = ["date", "id"]


class PromotionBatch(models.Model):
    """학년 올리기 묶음. 한 번의 진급을 통째로 기록해 되돌릴 수 있게 한다.

    같은 해에 두 번 하는 것이 정상일 수 있다(봄에 못 한 학생을 늦게, 국제학교는 가을).
    그래서 막지 않고, 겹치는 학생을 알려주고 사람이 고르게 한다."""
    SPRING = "SPRING"
    FALL = "FALL"
    season = models.CharField(max_length=8, default=SPRING)     # 봄 / 가을
    school_year = models.PositiveSmallIntegerField()            # 학년도(2027)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="promotions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    note = models.TextField(blank=True, default="")
    undone_at = models.DateTimeField(null=True, blank=True)     # 되돌린 때
    undone_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_promotion_batch"
        ordering = ["-id"]


class PromotionItem(models.Model):
    """진급으로 바뀐 학생 하나. 되돌리려면 이전 값이 있어야 한다."""
    batch = models.ForeignKey(PromotionBatch, on_delete=models.CASCADE, related_name="items")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="promotions")
    old_school_type = models.CharField(max_length=16, blank=True, default="")
    old_school_name = models.CharField(max_length=64, blank=True, default="")
    old_grade = models.CharField(max_length=8, blank=True, default="")
    new_school_type = models.CharField(max_length=16, blank=True, default="")
    new_school_name = models.CharField(max_length=64, blank=True, default="")
    new_grade = models.CharField(max_length=8, blank=True, default="")
    # 고3처럼 학년이 아니라 진로를 정한 경우(대학 진학·수료)
    action = models.CharField(max_length=16, blank=True, default="")   # GRADE / UNIV / DONE

    class Meta:
        db_table = "academy_promotion_item"


class SavedSearch(models.Model):
    """자주 쓰는 검색어. 사람에게 딸린 것이라 브라우저가 아니라 서버에 둔다
    (집 컴퓨터에서 만든 검색어를 학원 컴퓨터에서도 그대로 써야 한다)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="saved_searches")
    scope = models.CharField(max_length=32, default="phone")   # 어느 화면의 검색인지
    query = models.CharField(max_length=200)
    is_favorite = models.BooleanField(default=False)
    use_count = models.PositiveIntegerField(default=1)
    last_used_at = models.DateTimeField(auto_now=True)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_saved_search"
        unique_together = ("user", "scope", "query")
        ordering = ["-is_favorite", "-last_used_at"]


class HolidayOptOut(models.Model):
    """전 지점 공통 휴무일을 그 지점만 쉬지 않기.

    공휴일이라도 지점 사정에 따라 수업을 하는 날이 있다. 그렇다고 원장이 전 지점
    휴무일을 지워 버리면 다른 지점까지 영향을 받으므로, 지우는 대신 '우리 지점만
    사용 안 함' 으로 둔다. 언제든 다시 사용할 수 있다."""
    holiday = models.ForeignKey(Holiday, on_delete=models.CASCADE, related_name="opt_outs")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="holiday_opt_outs")
    reason = models.TextField(blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_holiday_optout"
        unique_together = ("holiday", "branch")


class WorkType(object):
    FIXED = "FIXED"            # 매주 같은 요일·시각
    IRREGULAR = "IRREGULAR"    # 아르바이트·외부 강사 — 그때그때 조율


WORK_TYPE_CHOICES = ((WorkType.FIXED, "고정 근무"), (WorkType.IRREGULAR, "불규칙 근무"))


class StaffWorkPlan(models.Model):
    """불규칙 근무자의 날짜별 근무 예정.

    아르바이트는 요일도 시각도 매번 달라 '근무 기준' 틀에 넣을 수 없다. 미리 정해 두되
    갑자기 바뀌는 일이 잦아 그날만 고치고 사유를 남긴다.

    급여는 여기 적힌 시간으로 계산한다 — 몇 분 일찍 오고 늦게 왔다고 급여를 깎거나
    더하지 않는다. 실제로 찍은 시각은 따로 보여 주기만 한다."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="work_plans")
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    note = models.TextField(blank=True, default="")     # 그날만 바꾼 사유
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_staff_work_plan"
        unique_together = ("staff", "date")
        ordering = ["date"]


class WorkPlanChange(models.Model):
    """근무표 변경 이력. 급여가 이 표에서 나오므로 누가 언제 무엇을 바꿨는지 남아야 한다."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="work_plan_changes")
    date = models.DateField()
    old_start = models.CharField(max_length=5, blank=True, default="")
    old_end = models.CharField(max_length=5, blank=True, default="")
    new_start = models.CharField(max_length=5, blank=True, default="")
    new_end = models.CharField(max_length=5, blank=True, default="")
    note = models.TextField(blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_work_plan_change"
        ordering = ["-id"]


class WorkSchedule(models.Model):
    """정규 근무 기준. 학생 시간표와 같은 '적용 시작일' 방식 — 바꾸면 이전 것은 전날로
    끝나고 새 줄이 생겨 과거 기록이 그대로 남는다.
    staff 가 있으면 그 직원 개별 기준(지점 기본값보다 우선), 없으면 branch 의 기본값."""
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE,
                               related_name="work_schedules")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.CASCADE, related_name="work_schedules")
    active_from = models.DateField()
    active_until = models.DateField(null=True, blank=True)   # 비어 있으면 계속 적용
    start_time = models.TimeField()
    end_time = models.TimeField()
    workdays = models.CharField(max_length=16, default="012345")  # 0=월 … 6=일
    break_per_hours = models.PositiveSmallIntegerField(default=4)   # N시간마다
    break_minutes = models.PositiveSmallIntegerField(default=30)    # M분 휴게
    reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_work_schedule"
        ordering = ["-active_from", "-id"]


class StaffAttendance(models.Model):
    """직원 출퇴근. 하루 1건(사번으로 키오스크에서 찍거나 포털에서 기록)."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="staff_attendance")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="staff_attendance")   # 찍은 지점(타 지점 출강 대비)
    date = models.DateField()
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    in_source = models.CharField(max_length=16, blank=True, default="")   # KIOSK / PORTAL
    out_source = models.CharField(max_length=16, blank=True, default="")
    note = models.TextField(blank=True, default="")       # 본인이 쓰는 비고
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_staff_attendance"
        unique_together = ("staff", "date")
        ordering = ["-date", "-id"]


class StaffAttendanceChange(models.Model):
    """출퇴근 기록의 모든 변경. 본인이 시각을 고치려면 승인 요청으로 남고,
    원장 이상이 직접 고치면 DIRECT 로 바로 반영된다."""
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DIRECT = "DIRECT"
    CANCELLED = "CANCELLED"

    attendance = models.ForeignKey(StaffAttendance, on_delete=models.CASCADE, related_name="changes")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    field = models.CharField(max_length=16)      # IN / OUT / NOTE / CHECK / CANCEL
    old_value = models.CharField(max_length=64, blank=True, default="")
    new_value = models.CharField(max_length=64, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, default=DIRECT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=16, blank=True, default="")   # KIOSK / PORTAL
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_staff_attendance_change"
        ordering = ["-create_time", "-id"]


class LeaveKind(object):
    ANNUAL = "ANNUAL"        # 연차
    HALF_AM = "HALF_AM"      # 반차(오전)
    HALF_PM = "HALF_PM"      # 반차(오후)
    SICK = "SICK"            # 병가
    OFFICIAL = "OFFICIAL"    # 공가
    UNPAID = "UNPAID"        # 무급휴가


LEAVE_KIND_CHOICES = [
    (LeaveKind.ANNUAL, "연차"),
    (LeaveKind.HALF_AM, "반차(오전)"),
    (LeaveKind.HALF_PM, "반차(오후)"),
    (LeaveKind.SICK, "병가"),
    (LeaveKind.OFFICIAL, "공가"),
    (LeaveKind.UNPAID, "무급휴가"),
]


class StaffLeave(models.Model):
    """연차·휴가. 지금은 '누가 언제 무엇으로 쉬었나' 기록만 하고 잔여일수는 관리하지 않는다."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="staff_leaves")
    date = models.DateField()
    kind = models.CharField(max_length=16, default=LeaveKind.ANNUAL)
    reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    is_deleted = models.BooleanField(default=False)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    deleted_at = models.DateTimeField(null=True, blank=True)
    delete_reason = models.CharField(max_length=255, blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_staff_leave"
        ordering = ["-date", "-id"]


class ProfileVerification(models.Model):
    """학부모 확인. 직원이 먼저 입력해 둔 학생 정보를, 학부모가 빈 양식에 독립적으로
    작성해 대조한다. 값이 다르면 직원이 항목별로 최종값을 정한다(둘 중 하나를 고르거나
    통화하며 들은 값을 직접 입력).

    학생 계정은 등록 시점에 이미 만들어지고 바로 운영에 쓰인다. 이 확인은 정보의
    정확성을 위한 절차일 뿐 등록의 전제조건이 아니다(예전에는 학부모가 늦게 쓰면
    학생 자체가 만들어지지 않아 시간표·출결을 못 넣었음)."""
    SENT = "SENT"
    SUBMITTED = "SUBMITTED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="profile_verifications")
    token = models.CharField(max_length=48, blank=True, default="", db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default=SENT)
    staff_snapshot = models.TextField(blank=True, default="")  # 링크 생성 시점의 직원 입력값(JSON)
    parent_data = models.TextField(blank=True, default="")     # 학부모 제출값(JSON)
    submitted_at = models.DateTimeField(null=True, blank=True)
    resolved_data = models.TextField(blank=True, default="")   # 승인 시 항목별 채택 결과(JSON)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_profile_verification"
        ordering = ["-create_time", "-id"]


# ── 자격증 · 대회 ──

class ExamKind(object):
    CERT = "CERT"        # 자격증
    CONTEST = "CONTEST"  # 대회


EXAM_KIND_CHOICES = [(ExamKind.CERT, "자격증"), (ExamKind.CONTEST, "대회")]


class EntryMode(object):
    INDIVIDUAL = "INDIVIDUAL"
    GROUP = "GROUP"
    BOTH = "BOTH"


ENTRY_MODE_CHOICES = [
    (EntryMode.INDIVIDUAL, "개인 접수"),
    (EntryMode.GROUP, "단체 접수"),
    (EntryMode.BOTH, "개인 + 단체"),
]


class ExamCatalog(models.Model):
    """자격증·대회 '종류'. 한 번 등록해 두고 계속 쓴다(COS Pro, 정보올림피아드 …).

    자격증은 급수·언어 조합이 수십 가지라 조합을 미리 만들지 않는다. 여기엔 고를 수 있는
    급수·언어 목록만 두고, 실제로 무엇을 보는지는 학생별 참가 기록에서 정한다
    (같은 시험일에 학생마다 다른 급수·언어를 보기 때문)."""
    kind = models.CharField(max_length=16, default=ExamKind.CERT)
    name = models.CharField(max_length=64)                      # COS Pro / 정보올림피아드
    organizer = models.CharField(max_length=64, blank=True, default="")   # YBM
    homepage = models.CharField(max_length=255, blank=True, default="")    # 일정 확인하러 갈 곳
    apply_url = models.CharField(max_length=255, blank=True, default="")   # 접수 페이지
    notice_url = models.CharField(max_length=255, blank=True, default="")  # 공지사항
    entry_mode = models.CharField(max_length=16, default=EntryMode.INDIVIDUAL)
    fee = models.PositiveIntegerField(null=True, blank=True)     # 기본 응시료(안내용)
    # 급수·부문은 응시료가 저마다 다를 수 있어 [{"name","fee"}] 로 담는다(fee 가 없으면 기본 응시료).
    levels = models.TextField(blank=True, default="")            # [{"name":"1급","fee":40000}, ...]
    tracks = models.TextField(blank=True, default="")            # [{"name":"Python","fee":null}, ...]
    # 대회는 예선1차·예선2차·본선처럼 여러 번에 나눠 치른다. 회차를 만들 때 골라 쓴다.
    rounds = models.TextField(blank=True, default="")            # [{"name":"예선1차","fee":null}, ...]
    # 어디서 보는 시험인지(온라인·바깥 고사장·우리 학원). 회차를 만들 때 골라 쓴다.
    venues = models.TextField(blank=True, default="")            # [{"name":"온라인","fee":null}, ...]
    annual = models.BooleanField(default=False)                  # 해마다 열리는가(대회)
    # 작년 기록이 없을 때 쓸 '확인 시작 시기'(MM-DD). 이 무렵이면 올해 일정을 확인하라고 알린다.
    check_from = models.CharField(max_length=8, blank=True, default="")
    note = models.TextField(blank=True, default="")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE,
                               related_name="exam_catalogs")     # 비우면 전 지점
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_exam_catalog"
        ordering = ["kind", "order", "name"]


class ExamSession(models.Model):
    """회차(실제 일정).

    자격증은 우리가 날짜를 정하는 특별시험이라 '날짜'가 곧 회차이고, 무엇을 보는지는
    학생마다 다르므로 catalog 를 비워 둔다. 대회는 회차 자체가 그 대회라 catalog 를 채운다.
    대회는 주최측이 일정을 늦게 잡는 일이 많아 confirmed=False 로 먼저 만들어 둘 수 있다."""
    kind = models.CharField(max_length=16, default=ExamKind.CERT)
    catalog = models.ForeignKey(ExamCatalog, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="sessions")
    title = models.CharField(max_length=128, blank=True, default="")
    exam_date = models.DateField(null=True, blank=True)          # 미확정이면 비움
    exam_time = models.CharField(max_length=5, blank=True, default="")   # HH:MM (비우면 미정)
    # 온라인 대회는 정해진 날 모여서 보는 게 아니라 기간 안에 올린다. 그 기간의 시작.
    # 마감은 exam_date 를 그대로 쓴다(D-day·정렬·알림이 모두 그 값을 본다).
    submit_from = models.DateField(null=True, blank=True)
    apply_from = models.DateField(null=True, blank=True)
    apply_until = models.DateField(null=True, blank=True)        # 자격증은 시험일-2일 기본
    result_date = models.DateField(null=True, blank=True)
    entry_mode = models.CharField(max_length=16, blank=True, default="")  # 종류가 '개인+단체'일 때 회차에서 결정
    # 이 회차가 어떤 급수·언어·진행 단계인지. 종류에 적어 둔 목록에서 고른다.
    # 학생을 붙일 때 미리 채워지고, 학생마다 다르면 각자 바꾼다.
    level = models.CharField(max_length=64, blank=True, default="")
    track = models.CharField(max_length=64, blank=True, default="")
    round = models.CharField(max_length=64, blank=True, default="")   # 대회만(예선1차·본선)
    place = models.CharField(max_length=128, blank=True, default="")
    fee = models.PositiveIntegerField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    confirmed = models.BooleanField(default=True)                # 대회 일정 미확정 표시
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE,
                               related_name="exam_sessions")
    is_deleted = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_exam_session"
        ordering = ["-exam_date", "-id"]


class ExamTeam(models.Model):
    """대회 팀. 한 대회 안에 개인전·팀전이 섞이므로 팀은 회차에 딸린다."""
    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=64)
    note = models.TextField(blank=True, default="")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_exam_team"
        ordering = ["name"]


class ExamStage(object):
    """진행 단계. 대회는 '참여 의사'를 먼저 묻고 참여하겠다는 학생만 접수로 넘어간다.
    자격증은 정규 과정 안에서 자연스럽게 진행되므로 준비 안내 → 접수 안내만 쓴다."""
    CANDIDATE = "CANDIDATE"      # 담아둔 대상(아직 안내 전)
    NOTIFIED = "NOTIFIED"        # 안내 발송(대회: 참여 의사 확인 / 자격증: 준비 안내)
    JOIN_YES = "JOIN_YES"        # 참여하겠다고 함(대회)
    JOIN_NO = "JOIN_NO"          # 안 하겠다고 함(대회)
    APPLY_GUIDE = "APPLY_GUIDE"  # 접수 안내
    APPLIED = "APPLIED"          # 접수 완료
    DONE = "DONE"                # 응시·참가 완료


EXAM_STAGE_CHOICES = [
    (ExamStage.CANDIDATE, "대상"),
    (ExamStage.NOTIFIED, "안내"),
    (ExamStage.JOIN_YES, "참여"),
    (ExamStage.JOIN_NO, "불참"),
    (ExamStage.APPLY_GUIDE, "접수 안내"),
    (ExamStage.APPLIED, "접수 완료"),
    (ExamStage.DONE, "응시 완료"),
]


class ExamContactKind(object):
    NOTIFY = "NOTIFY"            # 준비·참여 의사 안내
    APPLY_GUIDE = "APPLY_GUIDE"  # 접수 안내(2차·3차로 이어짐)
    ETC = "ETC"


EXAM_CONTACT_CHOICES = [
    (ExamContactKind.NOTIFY, "안내"),
    (ExamContactKind.APPLY_GUIDE, "접수 안내"),
    (ExamContactKind.ETC, "기타 연락"),
]


class ExamContact(models.Model):
    """연락 이력. '누구에게 어떤 안내를 언제 보냈나'를 남긴다.
    같은 종류를 여러 번 보내면 상태가 '접수 안내(2차)'처럼 올라간다 —
    꾸준히 독려하고 있다는 근거가 필요하기 때문."""
    entry = models.ForeignKey("ExamEntry", on_delete=models.CASCADE, related_name="contacts")
    kind = models.CharField(max_length=16, default=ExamContactKind.APPLY_GUIDE)
    note = models.TextField(blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_exam_contact"
        ordering = ["-create_time", "-id"]


class ExamChangeKind(object):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


EXAM_CHANGE_CHOICES = ((ExamChangeKind.CREATE, "등록"), (ExamChangeKind.UPDATE, "수정"),
                       (ExamChangeKind.DELETE, "삭제"))


class ExamChange(models.Model):
    """회차(시험·대회) 등록·수정·삭제 이력.

    지웠다 다시 만드는 일이 잦아 '언제 무엇을 지웠는지' 를 나중에 확인해야 한다.
    회차가 지워져도 남아야 하므로 이름은 글로 함께 적어 둔다."""
    session = models.ForeignKey(ExamSession, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="changes")
    kind = models.CharField(max_length=16, default=ExamChangeKind.CREATE)
    exam_kind = models.CharField(max_length=16, blank=True, default="")   # CERT / CONTEST
    label = models.CharField(max_length=160, blank=True, default="")      # 회차 이름(지워져도 남게)
    detail = models.TextField(blank=True, default="")                     # 날짜·바뀐 값 등
    entry_count = models.PositiveSmallIntegerField(default=0)             # 함께 내려간 참가 수
    students = models.TextField(blank=True, default="")                   # 함께 내려간 학생 이름
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "academy_exam_change"
        ordering = ["-id"]


class ExamEntry(models.Model):
    """참가 — 목록 화면의 한 줄. '누구 / 무엇 / 언제 / 접수했나'."""
    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name="entries")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="exam_entries")
    # 자격증은 학생마다 무엇을 보는지 다르다(브랜드·급수·언어). 대회는 회차의 catalog 를 따른다.
    catalog = models.ForeignKey(ExamCatalog, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="entries")
    level = models.CharField(max_length=16, blank=True, default="")   # 2급
    track = models.CharField(max_length=32, blank=True, default="")   # Python
    round = models.CharField(max_length=64, blank=True, default="")   # 대회의 진행(예선1차·본선)
    # 자격증은 학생마다 시험 보는 날이 다르다. 날짜마다 회차를 새로 만들면 같은 자격증이
    # 여러 개로 갈라져 불편해서 날짜를 학생 쪽에 둔다. 대회는 날이 하나라 회차 것을 쓴다.
    exam_date = models.DateField(null=True, blank=True)
    exam_time = models.CharField(max_length=5, blank=True, default="")   # HH:MM
    apply_until = models.DateField(null=True, blank=True)
    result_date = models.DateField(null=True, blank=True)
    place = models.CharField(max_length=128, blank=True, default="")
    team = models.ForeignKey(ExamTeam, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="members")
    stage = models.CharField(max_length=16, default=ExamStage.CANDIDATE)
    applied = models.BooleanField(default=False)
    applied_at = models.DateField(null=True, blank=True)
    # 접수에 쓴 계정(학생 상세의 아이디 관리와 연결) — 부모가 아이디를 잊는 일이 잦다
    credential = models.ForeignKey(StudentCredential, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    fee_paid = models.BooleanField(default=False)     # 단체 접수일 때만 쓴다
    result = models.CharField(max_length=32, blank=True, default="")   # 합격/불합격/수상
    score = models.CharField(max_length=32, blank=True, default="")
    note = models.TextField(blank=True, default="")
    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    is_deleted = models.BooleanField(default=False)
    # 도전하려다 그만둔 것도 이력이라 지운 때와 지운 사람을 남긴다
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    deleted_reason = models.CharField(max_length=32, blank=True, default="")   # SESSION=회차가 지워짐
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    create_time = models.DateTimeField(auto_now_add=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_exam_entry"
        ordering = ["-id"]


class MenuSetting(models.Model):
    """포털 상단 메뉴 켜고 끄기.

    지금 안 쓰는 메뉴(그룹·특강, 개발 요청)를 감추되 없애지는 않는다 — 나중에 다시
    쓸 수 있기 때문. 역할별로 제한하거나 특정 직원만 예외를 둘 수도 있다."""
    key = models.CharField(max_length=32)                  # dashboard, classes, devboard …
    # 지점별 설정. 비어 있으면 전 지점 기본값(본부가 정한 것)이고, 지점 설정이 있으면 그게 우선.
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE,
                               related_name="menu_settings")
    enabled = models.BooleanField(default=True)            # 꺼두면 그 지점에서 안 보인다
    roles = models.TextField(blank=True, default="")       # JSON 역할 목록. 비면 제한 없음
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_menu_setting"
        unique_together = ("key", "branch")


class MenuOverride(models.Model):
    """직원 개인별 예외. 역할 규칙보다 우선한다(이 사람만 열어주거나 이 사람만 막을 때)."""
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="menu_overrides")
    key = models.CharField(max_length=32)
    allow = models.BooleanField(default=True)
    update_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academy_menu_override"
        unique_together = ("staff", "key")
