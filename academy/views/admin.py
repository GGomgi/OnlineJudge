from django.utils.timezone import now

from utils.api import APIView, validate_serializer

from account.decorators import admin_role_required
from account.models import User
from ..models import (AcademyProfile, AcademyRole, ALL_BRANCH_ROLES, Branch,
                      SignupRequest, SignupStatus)
from ..serializers import (SignupRequestSerializer, SignupApproveSerializer,
                           SignupRejectSerializer, AssignRoleSerializer)
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
