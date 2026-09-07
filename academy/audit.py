"""무엇을 만들고 고치고 지웠는지 한 줄 남긴다.

기능마다 이력 표를 따로 두면 새 기능을 만들 때마다 표를 만들고 사용 이력 화면에도
이어 줘야 한다. 그러다 빠진다(원비·할인 2026-09-07). 제 화면에서 이력을 따로 보여 줄
일이 없는 것은 여기 한 줄만 남기면 된다.

    audit(request, "게시판", "만듦", "수업자료 › 8월 진도표", detail="파일 2개")

남기는 것이 실패해도 하던 일은 끝나야 한다. 이력 때문에 저장이 막히면 본말이 뒤집힌다.
"""
from .models import AuditLog, AcademyProfile

_ACTION = {"만듦": AuditLog.CREATE, "고침": AuditLog.UPDATE, "지움": AuditLog.DELETE,
           "CREATE": AuditLog.CREATE, "UPDATE": AuditLog.UPDATE, "DELETE": AuditLog.DELETE}


def audit(request, kind, action, target="", detail="", reason="", student=None, branch_id=None):
    try:
        user = getattr(request, "user", None) if request is not None else None
        if branch_id is None and user is not None:
            prof = AcademyProfile.objects.filter(user=user, is_deleted=False).first()
            branch_id = prof.branch_id if prof else None
        AuditLog.objects.create(
            actor=(user if (user is not None and getattr(user, "id", None)) else None),
            branch_id=branch_id, kind=kind[:24],
            action=_ACTION.get(action, AuditLog.UPDATE),
            target=str(target or "")[:120], student=student,
            detail=str(detail or "")[:255], reason=str(reason or "")[:255])
    except Exception:
        pass        # 이력 때문에 하던 일이 막히면 안 된다
