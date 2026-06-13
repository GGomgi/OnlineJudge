"""학원 역할 ↔ 기존 OJ admin_type/problem_permission 동기화 및 지점 스코프 헬퍼."""
from account.models import AdminType, ProblemPermission
from .models import AcademyRole, ALL_BRANCH_ROLES

# 학원 역할 → 기존 OJ admin_type 매핑 (업스트림 기능 호환 유지)
ROLE_ADMIN_TYPE = {
    AcademyRole.HQ_ADMIN: AdminType.SUPER_ADMIN,
    AcademyRole.HR_ADMIN: AdminType.ADMIN,
    AcademyRole.BRANCH_MANAGER: AdminType.ADMIN,
    AcademyRole.INSTRUCTOR: AdminType.ADMIN,
    AcademyRole.TA: AdminType.ADMIN,
    AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN: AdminType.ADMIN,
    AcademyRole.STUDENT: AdminType.REGULAR_USER,
    AcademyRole.PARENT: AdminType.REGULAR_USER,
}

# 학원 역할 → 문제 관리 권한 매핑
ROLE_PROBLEM_PERMISSION = {
    AcademyRole.HQ_ADMIN: ProblemPermission.ALL,
    AcademyRole.HR_ADMIN: ProblemPermission.NONE,
    AcademyRole.BRANCH_MANAGER: ProblemPermission.OWN,
    AcademyRole.INSTRUCTOR: ProblemPermission.OWN,
    AcademyRole.TA: ProblemPermission.OWN,
    AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN: ProblemPermission.OWN,
    AcademyRole.STUDENT: ProblemPermission.NONE,
    AcademyRole.PARENT: ProblemPermission.NONE,
}


def apply_role(user, role, branch=None):
    """사용자에게 학원 역할/지점을 부여하고 admin_type·problem_permission 을 동기화한다.
    AcademyProfile 이 없으면 생성한다. 전지점 역할이면 branch 는 무시되어 null 로 저장된다."""
    from .models import AcademyProfile
    profile, _ = AcademyProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.branch = None if role in ALL_BRANCH_ROLES else branch
    profile.save()

    user.admin_type = ROLE_ADMIN_TYPE.get(role, AdminType.REGULAR_USER)
    user.problem_permission = ROLE_PROBLEM_PERMISSION.get(role, ProblemPermission.NONE)
    user.save()
    return profile


def staff_scope(user):
    """교직원의 지점 스코프를 (전지점여부, branch_id, role) 로 반환.
    academy_profile 이 없는 슈퍼관리자는 전지점으로 취급한다."""
    profile = getattr(user, "academy_profile", None)
    if profile is None:
        if user.is_super_admin():
            return True, None, AcademyRole.HQ_ADMIN
        return False, None, None
    return profile.is_all_branch(), profile.branch_id, profile.role


def can_manage_branch(user, target_branch_id):
    """해당 사용자가 대상 지점을 관리할 수 있는지 (전지점이면 항상 가능)."""
    all_branch, branch_id, role = staff_scope(user)
    if all_branch:
        return True
    if branch_id is None:
        return False
    return branch_id == target_branch_id
