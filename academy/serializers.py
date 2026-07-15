from utils.api import serializers

from .models import (AcademyRole, ACADEMY_ROLE_CHOICES, SELF_SIGNUP_ROLES,
                     STAFF_ROLES, Branch, SignupRequest, CourseClass, ClassEnrollment,
                     TimetableSlot, ClassSession, AttendanceRecord,
                     ATTENDANCE_STATUS_VALUES, Lead, CounselingLog,
                     CONTACT_PREFERENCES, SCHOOL_TYPES, COUNSELING_PURPOSES,
                     OptionItem, OPTION_CATEGORY_VALUES, StudentTimetable, LessonType)

ALL_ROLE_VALUES = [c[0] for c in ACADEMY_ROLE_CHOICES]
WEEKDAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "code", "name", "is_active"]


class AcademySignupSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=32)
    password = serializers.CharField(min_length=6, max_length=128)
    real_name = serializers.CharField(max_length=64)
    role = serializers.ChoiceField(choices=sorted(SELF_SIGNUP_ROLES))
    branch_id = serializers.IntegerField()
    email = serializers.EmailField(max_length=64, required=False, allow_blank=True)
    contact = serializers.CharField(max_length=32, required=False, allow_blank=True)
    memo = serializers.CharField(max_length=500, required=False, allow_blank=True)


class SignupRequestSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()
    branch = serializers.SerializerMethodField()
    reviewed_by = serializers.SerializerMethodField()

    class Meta:
        model = SignupRequest
        fields = ["id", "username", "applicant_name", "requested_role", "branch",
                  "contact", "memo", "status", "reviewed_by", "reviewed_at",
                  "reject_reason", "create_time"]

    def get_username(self, obj):
        return obj.user.username

    def get_branch(self, obj):
        if obj.requested_branch_id and obj.requested_branch:
            return {"id": obj.requested_branch.id, "code": obj.requested_branch.code,
                    "name": obj.requested_branch.name}
        return None

    def get_reviewed_by(self, obj):
        return obj.reviewed_by.username if obj.reviewed_by_id else None


class SignupApproveSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    # 승인 시 역할/지점 확정·조정(미지정 시 신청 값 사용)
    role = serializers.ChoiceField(choices=ALL_ROLE_VALUES, required=False)
    branch_id = serializers.IntegerField(required=False, allow_null=True)


class SignupRejectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AssignRoleSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    role = serializers.ChoiceField(choices=ALL_ROLE_VALUES)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    managed_branch_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False)  # 지부장 관리지점
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class CreateStaffSerializer(serializers.Serializer):
    # 로그인 아이디(username)는 사번으로 자동 생성한다. 입력받지 않음.
    password = serializers.CharField(min_length=6, max_length=128)
    real_name = serializers.CharField(max_length=64)
    role = serializers.ChoiceField(choices=sorted(STAFF_ROLES))
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    managed_branch_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False)  # 지부장 관리지점
    email = serializers.EmailField(max_length=64, required=False, allow_blank=True)


class StaffStatusSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    is_active = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class TimetableSlotSerializer(serializers.ModelSerializer):
    day_name = serializers.SerializerMethodField()

    class Meta:
        model = TimetableSlot
        fields = ["id", "day_of_week", "day_name", "start_time", "end_time", "room"]

    def get_day_name(self, obj):
        if 0 <= obj.day_of_week <= 6:
            return WEEKDAY_NAMES[obj.day_of_week]
        return ""


class CourseClassSerializer(serializers.ModelSerializer):
    branch = serializers.SerializerMethodField()
    instructor = serializers.SerializerMethodField()
    student_count = serializers.SerializerMethodField()
    timetable = serializers.SerializerMethodField()

    class Meta:
        model = CourseClass
        fields = ["id", "name", "track", "level", "branch", "instructor",
                  "student_count", "timetable", "is_active", "create_time"]

    def get_branch(self, obj):
        if obj.branch_id:
            return {"id": obj.branch.id, "code": obj.branch.code, "name": obj.branch.name}
        return None

    def get_instructor(self, obj):
        return obj.instructor.username if obj.instructor_id else None

    def get_student_count(self, obj):
        return obj.enrollments.filter(is_active=True).count()

    def get_timetable(self, obj):
        return TimetableSlotSerializer(obj.timetable_slots.all(), many=True).data


class CreateClassSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    branch_id = serializers.IntegerField()
    track = serializers.CharField(max_length=16, required=False, allow_blank=True)
    level = serializers.CharField(max_length=8, required=False, allow_blank=True)
    instructor_id = serializers.IntegerField(required=False, allow_null=True)


class EditClassSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(max_length=128, required=False)
    track = serializers.CharField(max_length=16, required=False, allow_blank=True)
    level = serializers.CharField(max_length=8, required=False, allow_blank=True)
    instructor_id = serializers.IntegerField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class EnrollSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    student_id = serializers.IntegerField()


class SetTimetableSlotSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    day_of_week = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    room = serializers.CharField(max_length=64, required=False, allow_blank=True)


class EnrollmentSerializer(serializers.ModelSerializer):
    student = serializers.SerializerMethodField()

    class Meta:
        model = ClassEnrollment
        fields = ["id", "student", "is_active", "joined_at"]

    def get_student(self, obj):
        real_name = ""
        try:
            real_name = obj.student.userprofile.real_name or ""
        except Exception:
            real_name = ""
        return {"id": obj.student.id, "username": obj.student.username, "real_name": real_name}


def _student_brief(user):
    real_name = ""
    try:
        real_name = user.userprofile.real_name or ""
    except Exception:
        real_name = ""
    return {"id": user.id, "username": user.username, "real_name": real_name}


class ClassSessionSerializer(serializers.ModelSerializer):
    class_name = serializers.SerializerMethodField()
    branch = serializers.SerializerMethodField()

    class Meta:
        model = ClassSession
        fields = ["id", "course_class_id", "class_name", "branch", "date",
                  "start_time", "end_time", "status", "topic", "create_time"]

    def get_class_name(self, obj):
        return obj.course_class.name

    def get_branch(self, obj):
        b = obj.course_class.branch
        return {"id": b.id, "code": b.code, "name": b.name} if b else None


class CreateSessionSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    date = serializers.DateField()
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)
    topic = serializers.CharField(max_length=255, required=False, allow_blank=True)


class GenerateSessionsSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    from_date = serializers.DateField()
    to_date = serializers.DateField()


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = ["id", "student", "status", "memo", "update_time"]

    def get_student(self, obj):
        return _student_brief(obj.student)


class _AttItemSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=ATTENDANCE_STATUS_VALUES)
    memo = serializers.CharField(max_length=255, required=False, allow_blank=True)


class MarkAttendanceSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    records = serializers.ListField(child=_AttItemSerializer())


# ── 상담 신청(리드) → 등록 전환 (80) ──

class LeadCreateSerializer(serializers.Serializer):
    # 접수 라우팅 위해 지점만 필요. 그 외 학생/보호자 정보는 모두 선택.
    branch_id = serializers.IntegerField()
    parent_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    parent_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    student_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    school_type = serializers.CharField(max_length=16, required=False, allow_blank=True)
    school_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    grade = serializers.CharField(max_length=16, required=False, allow_blank=True)
    interest = serializers.CharField(required=False, allow_blank=True)
    purpose = serializers.ChoiceField(choices=COUNSELING_PURPOSES, required=False, allow_blank=True)
    purpose_detail = serializers.CharField(max_length=255, required=False, allow_blank=True)


class CounselingLogSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    edited_by = serializers.SerializerMethodField()
    edits = serializers.SerializerMethodField()

    class Meta:
        model = CounselingLog
        fields = ["id", "author", "channel", "summary", "counsel_at", "next_contact_at",
                  "create_time", "is_hidden", "edited_by", "edited_at", "prev_summary", "edits"]

    def get_edits(self, obj):
        out = []
        for e in obj.edits.all():
            out.append({"actor": self._name(e.actor) if e.actor_id else None,
                        "old": e.old_summary, "time": str(e.create_time)[:16]})
        return out

    def _name(self, u):
        if not u:
            return None
        try:
            return u.userprofile.real_name or u.username
        except Exception:
            return u.username

    def get_author(self, obj):
        return self._name(obj.author) if obj.author_id else None

    def get_edited_by(self, obj):
        return self._name(obj.edited_by) if obj.edited_by_id else None


class LeadSerializer(serializers.ModelSerializer):
    branch = serializers.SerializerMethodField()
    logs = serializers.SerializerMethodField()
    converted_username = serializers.SerializerMethodField()
    reservations = serializers.SerializerMethodField()
    display_status = serializers.SerializerMethodField()
    enroll = serializers.SerializerMethodField()
    edits = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = ["id", "branch", "parent_name", "parent_phone", "student_name",
                  "school_type", "school_name", "grade", "interest", "contact_preference",
                  "purpose", "purpose_detail",
                  "status", "converted_username", "close_reason", "create_time", "logs",
                  "is_hidden", "reservations", "display_status", "enroll", "edits"]

    def get_edits(self, obj):
        import json as _je
        try:
            return _je.loads(obj.edit_log) if obj.edit_log else []
        except (ValueError, TypeError):
            return []

    def get_enroll(self, obj):
        import json as _j
        data = None
        if obj.enroll_data:
            try:
                data = _j.loads(obj.enroll_data)
            except (ValueError, TypeError):
                data = None
        return {"status": obj.enroll_status or "",
                "token": obj.enroll_token or "",
                "path": ("/portal/?enroll=" + obj.enroll_token) if obj.enroll_token else "",
                "expires": str(obj.enroll_token_expires)[:16] if obj.enroll_token_expires else "",
                "submitted_at": str(obj.enroll_submitted_at)[:16] if obj.enroll_submitted_at else "",
                "data": data}

    def _name(self, u):
        if not u:
            return None
        try:
            return u.userprofile.real_name or u.username
        except Exception:
            return u.username

    def get_reservations(self, obj):
        from django.utils.timezone import now
        out = []
        for r in obj.reservations.all():
            if r.status != "ACTIVE":
                continue
            import json as _je
            try:
                edits = _je.loads(r.edit_log) if r.edit_log else []
            except (ValueError, TypeError):
                edits = []
            from datetime import timedelta as _td
            sched_kst = (r.scheduled_at + _td(hours=9)).strftime("%Y-%m-%dT%H:%M") if r.scheduled_at else None
            created_kst = (r.create_time + _td(hours=9)).strftime("%Y-%m-%dT%H:%M") if r.create_time else None
            out.append({"id": r.id, "scheduled_at": sched_kst,
                        "note": r.note, "created_by": self._name(r.created_by),
                        "created_at": created_kst,
                        "is_past": bool(r.scheduled_at and r.scheduled_at < now()),
                        "edits": edits})
        return out

    def get_display_status(self, obj):
        from django.utils.timezone import now
        if obj.status == "CONVERTED":
            return "등록완료"
        if obj.status == "CLOSED":
            return "종결"
        has_future = any(r.status == "ACTIVE" and r.scheduled_at and r.scheduled_at >= now()
                         for r in obj.reservations.all())
        return "상담예약중" if has_future else "상담"

    def get_branch(self, obj):
        return {"id": obj.branch.id, "code": obj.branch.code, "name": obj.branch.name} if obj.branch_id else None

    def get_converted_username(self, obj):
        return obj.converted_user.username if obj.converted_user_id else None

    def get_logs(self, obj):
        # 원장 이상은 숨김 상담기록도 표시(삭제됨), 그 외는 숨김 제외
        show_hidden = self.context.get("show_hidden", False)
        logs = list(obj.logs.all())
        if not show_hidden:
            logs = [l for l in logs if not l.is_hidden]
        return CounselingLogSerializer(logs, many=True).data


class AddCounselingNoteSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField()
    summary = serializers.CharField()
    channel = serializers.CharField(max_length=16, required=False, allow_blank=True)
    counsel_at = serializers.DateTimeField(required=False, allow_null=True)
    next_contact_at = serializers.DateField(required=False, allow_null=True)


class ConvertLeadSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField()
    parent_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    login_id = serializers.CharField(max_length=32)
    password = serializers.CharField(min_length=6, max_length=128)
    birth_date = serializers.DateField()
    gender = serializers.ChoiceField(choices=["M", "F"], required=False, allow_blank=True)
    student_phone = serializers.CharField(max_length=32)
    zipcode = serializers.CharField(max_length=16, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255)
    address_detail = serializers.CharField(max_length=255, required=False, allow_blank=True)
    # 등록 과정·교육 일정
    program = serializers.CharField(max_length=16, required=False, allow_blank=True)
    program_language = serializers.CharField(max_length=16, required=False, allow_blank=True)
    program_custom = serializers.CharField(max_length=255, required=False, allow_blank=True)
    programs = serializers.CharField(required=False, allow_blank=True)  # JSON 다중 과정
    weekly_sessions = serializers.IntegerField(required=False, allow_null=True)
    class_schedule = serializers.CharField(required=False, allow_blank=True)
    schedule_pending = serializers.BooleanField(required=False, default=False)
    lesson_start_date = serializers.DateField(required=False, allow_null=True)
    # 학부모(보호자) 계정 — 자녀 기록 열람용. 미입력 시 전화번호로 자동 생성/연결(11 §9)
    parent_login_id = serializers.CharField(max_length=32, required=False, allow_blank=True)
    parent_password = serializers.CharField(max_length=128, required=False, allow_blank=True)
    # 개인정보 동의(법정대리인)
    consent_privacy = serializers.BooleanField()
    consent_guardian_name = serializers.CharField(max_length=64)
    consent_signature = serializers.CharField()
    consent_date = serializers.DateField(required=False, allow_null=True)

    def validate_consent_privacy(self, v):
        if not v:
            raise serializers.ValidationError("개인정보 수집·이용 동의가 필요합니다.")
        return v


class CloseLeadSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField()
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)


# ── 선택 목록(옵션 마스터) ──

class OptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OptionItem
        fields = ["id", "category", "value", "label", "order", "is_active", "allow_custom", "color"]


class CreateOptionSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=OPTION_CATEGORY_VALUES)
    value = serializers.CharField(max_length=32)
    label = serializers.CharField(max_length=64)
    order = serializers.IntegerField(required=False, default=0)
    allow_custom = serializers.BooleanField(required=False, default=False)
    color = serializers.CharField(max_length=16, required=False, allow_blank=True)


class UpdateOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    label = serializers.CharField(max_length=64, required=False)
    order = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField(required=False)
    allow_custom = serializers.BooleanField(required=False)
    color = serializers.CharField(max_length=16, required=False, allow_blank=True)


class ReorderOptionSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=OPTION_CATEGORY_VALUES)
    ids = serializers.ListField(child=serializers.IntegerField())


class SaveStaffProfileSerializer(serializers.Serializer):
    zipcode = serializers.CharField(max_length=16, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255)
    address_detail = serializers.CharField(max_length=255, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32)
    dependents_decided = serializers.BooleanField(required=False, default=False)
    dependents = serializers.CharField(required=False, allow_blank=True)          # JSON
    emergency_contacts = serializers.CharField(required=False, allow_blank=True)   # JSON
    sex_offense_consent = serializers.BooleanField(required=False, default=False)
    sex_offense_signature = serializers.CharField(required=False, allow_blank=True)
    sex_offense_date = serializers.DateField(required=False, allow_null=True)


# ── 개별 수업 시간표 (12) ──

def _add_minutes(t, minutes):
    if not t:
        return None
    total = t.hour * 60 + t.minute + (minutes or 0)
    total %= 24 * 60
    return "%02d:%02d" % (total // 60, total % 60)


class StudentTimetableSerializer(serializers.ModelSerializer):
    student = serializers.SerializerMethodField()
    branch = serializers.SerializerMethodField()
    instructor = serializers.SerializerMethodField()
    day_name = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()

    class Meta:
        model = StudentTimetable
        fields = ["id", "student", "branch", "class_type", "weekday", "day_name",
                  "start_time", "duration_minutes", "end_time", "instructor",
                  "program", "subject", "frequency", "room", "status", "create_time"]

    def get_student(self, obj):
        return _student_brief(obj.student)

    def get_branch(self, obj):
        b = obj.branch
        return {"id": b.id, "code": b.code, "name": b.name} if obj.branch_id else None

    def get_instructor(self, obj):
        if not obj.instructor_id:
            return None
        real_name = ""
        try:
            real_name = obj.instructor.userprofile.real_name or ""
        except Exception:
            real_name = ""
        return {"id": obj.instructor.id, "username": obj.instructor.username, "real_name": real_name}

    def get_day_name(self, obj):
        return WEEKDAY_NAMES[obj.weekday] if 0 <= obj.weekday <= 6 else ""

    def get_end_time(self, obj):
        return _add_minutes(obj.start_time, obj.duration_minutes)


class CreateStudentTimetableSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField()
    duration_minutes = serializers.IntegerField(min_value=10, max_value=600, required=False, default=60)
    instructor_id = serializers.IntegerField(required=False, allow_null=True)
    program = serializers.CharField(max_length=32, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=64, required=False, allow_blank=True)
    frequency = serializers.ChoiceField(choices=["WEEKLY", "BIWEEKLY"], required=False)
    room = serializers.CharField(max_length=64, required=False, allow_blank=True)
    class_type = serializers.ChoiceField(choices=[LessonType.PRIVATE, LessonType.GROUP], required=False)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)


class EditStudentTimetableSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    weekday = serializers.IntegerField(min_value=0, max_value=6, required=False)
    start_time = serializers.TimeField(required=False)
    duration_minutes = serializers.IntegerField(min_value=10, max_value=600, required=False)
    instructor_id = serializers.IntegerField(required=False, allow_null=True)
    program = serializers.CharField(max_length=32, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=64, required=False, allow_blank=True)
    frequency = serializers.ChoiceField(choices=["WEEKLY", "BIWEEKLY"], required=False)
    room = serializers.CharField(max_length=64, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["ACTIVE", "PAUSED", "ENDED"], required=False)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
