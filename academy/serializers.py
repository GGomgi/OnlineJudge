from utils.api import serializers

from .models import (AcademyRole, ACADEMY_ROLE_CHOICES, SELF_SIGNUP_ROLES,
                     Branch, SignupRequest)

ALL_ROLE_VALUES = [c[0] for c in ACADEMY_ROLE_CHOICES]


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
