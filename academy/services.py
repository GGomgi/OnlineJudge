"""학원 역할 ↔ 기존 OJ admin_type/problem_permission 동기화 및 지점 스코프 헬퍼."""
from account.models import AdminType, ProblemPermission
from .models import AcademyRole, ALL_BRANCH_ROLES

# 학원 역할 → 기존 OJ admin_type 매핑 (업스트림 기능 호환 유지)
ROLE_ADMIN_TYPE = {
    AcademyRole.HQ_ADMIN: AdminType.SUPER_ADMIN,
    AcademyRole.HR_ADMIN: AdminType.ADMIN,
    AcademyRole.REGIONAL_MANAGER: AdminType.ADMIN,
    AcademyRole.BRANCH_MANAGER: AdminType.ADMIN,
    AcademyRole.VICE_PRINCIPAL: AdminType.ADMIN,
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
    AcademyRole.REGIONAL_MANAGER: ProblemPermission.OWN,
    AcademyRole.BRANCH_MANAGER: ProblemPermission.OWN,
    AcademyRole.VICE_PRINCIPAL: ProblemPermission.OWN,
    AcademyRole.INSTRUCTOR: ProblemPermission.OWN,
    AcademyRole.TA: ProblemPermission.OWN,
    AcademyRole.EXTERNAL_INSTRUCTOR_ADMIN: ProblemPermission.OWN,
    AcademyRole.STUDENT: ProblemPermission.NONE,
    AcademyRole.PARENT: ProblemPermission.NONE,
}

# 직원 인사·역할 관리 권한 역할(부원장·지부장 제외: 지부장은 열람전용)
STAFF_MGMT_ROLES = {AcademyRole.HQ_ADMIN, AcademyRole.HR_ADMIN, AcademyRole.BRANCH_MANAGER}


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
    """교직원의 지점 스코프를 (전지점여부, branch_id, role) 로 반환(주 소속 지점).
    academy_profile 이 없는 슈퍼관리자는 전지점으로 취급한다."""
    profile = getattr(user, "academy_profile", None)
    if profile is None:
        if user.is_super_admin():
            return True, None, AcademyRole.HQ_ADMIN
        return False, None, None
    return profile.is_all_branch(), profile.branch_id, profile.role


def editable_branch_ids(user):
    """수정(쓰기) 가능한 지점 id 목록. 전지점이면 None(=전체), 권한 없으면 [].
    지부장(REGIONAL_MANAGER)은 열람 전용이라 수정 지점 없음([]).
    그 외 단일지점 역할은 [본인 소속]."""
    profile = getattr(user, "academy_profile", None)
    if profile is None:
        return None if user.is_super_admin() else []
    if profile.is_all_branch():
        return None
    if profile.role == AcademyRole.REGIONAL_MANAGER:
        return []
    return [profile.branch_id] if profile.branch_id else []


def viewable_branch_ids(user):
    """열람(읽기) 가능한 지점 id 목록. 수정 지점 + managed_branches(지부장 겸직 포함).
    전지점이면 None(=전체)."""
    edit = editable_branch_ids(user)
    if edit is None:
        return None
    ids = set(edit)
    profile = getattr(user, "academy_profile", None)
    if profile is not None:
        ids.update(profile.managed_branches.values_list("id", flat=True))
    return list(ids)


# 하위호환 별칭(기존 호출부): 관리=수정 범위
def managed_branch_ids(user):
    return editable_branch_ids(user)


def can_manage_branch(user, target_branch_id):
    """대상 지점을 수정(쓰기)할 수 있는지. 전지점이면 항상 가능."""
    ids = editable_branch_ids(user)
    if ids is None:
        return True
    return target_branch_id in ids


def can_view_branch(user, target_branch_id):
    """대상 지점을 열람(읽기)할 수 있는지. 수정 가능 + 지부장 열람지점 포함."""
    ids = viewable_branch_ids(user)
    if ids is None:
        return True
    return target_branch_id in ids


def can_manage_staff(user):
    """직원 인사·역할 관리 권한(부원장·지부장 제외)."""
    if user.is_super_admin():
        return True
    profile = getattr(user, "academy_profile", None)
    return bool(profile and profile.role in STAFF_MGMT_ROLES)
