from utils.api import serializers

from .models import (AcademyRole, ACADEMY_ROLE_CHOICES, SELF_SIGNUP_ROLES,
                     Branch, SignupRequest, CourseClass, ClassEnrollment,
                     TimetableSlot, ClassSession, AttendanceRecord,
                     ATTENDANCE_STATUS_VALUES, Lead, CounselingLog,
                     CONTACT_PREFERENCES, SCHOOL_TYPES, COUNSELING_PURPOSES)

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
    branch_id = serializers.IntegerField()
    parent_name = serializers.CharField(max_length=64)
    parent_phone = serializers.CharField(max_length=32)
    student_name = serializers.CharField(max_length=64)
    school_type = serializers.CharField(max_length=16, required=False, allow_blank=True)
    school_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    grade = serializers.CharField(max_length=16, required=False, allow_blank=True)
    interest = serializers.CharField(required=False, allow_blank=True)
    purpose = serializers.ChoiceField(choices=COUNSELING_PURPOSES)
    purpose_detail = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs.get("purpose") == "ETC" and not (attrs.get("purpose_detail") or "").strip():
            raise serializers.ValidationError("직접 입력 시 상담 목적 내용을 적어주세요.")
        return attrs


class CounselingLogSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = CounselingLog
        fields = ["id", "author", "channel", "summary", "next_contact_at", "create_time"]

    def get_author(self, obj):
        return obj.author.username if obj.author_id else None


class LeadSerializer(serializers.ModelSerializer):
    branch = serializers.SerializerMethodField()
    logs = CounselingLogSerializer(many=True, read_only=True)
    converted_username = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = ["id", "branch", "parent_name", "parent_phone", "student_name",
                  "school_type", "school_name", "grade", "interest", "contact_preference",
                  "purpose", "purpose_detail",
                  "status", "converted_username", "close_reason", "create_time", "logs"]

    def get_branch(self, obj):
        return {"id": obj.branch.id, "code": obj.branch.code, "name": obj.branch.name} if obj.branch_id else None

    def get_converted_username(self, obj):
        return obj.converted_user.username if obj.converted_user_id else None


class AddCounselingNoteSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField()
    summary = serializers.CharField()
    channel = serializers.CharField(max_length=16, required=False, allow_blank=True)
    next_contact_at = serializers.DateField(required=False, allow_null=True)


class ConvertLeadSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField()
    login_id = serializers.CharField(max_length=32)
    password = serializers.CharField(min_length=6, max_length=128)
    birth_date = serializers.DateField(required=False, allow_null=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    student_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)


class CloseLeadSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField()
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
