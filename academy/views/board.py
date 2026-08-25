"""게시판 — 공지 · 원내 규정 · 수업자료를 폴더 트리 하나로.

성격을 이름이 아니라 폴더의 성질로 가른다(docs/82). 공지만 특별히 다루면 규정이
들어올 때 또 특별 취급을 만들어야 한다.

파일은 개수로 막지 않고 용량으로 막는다 — 예제 30개를 한 번에 올리고 싶은데
10개에서 끊기면 글을 셋으로 쪼개게 되고 그게 더 나쁘다.
"""
import difflib
import json as _json
import os as _os
from datetime import datetime, timedelta

from django.conf import settings as _settings
from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from utils.api import APIView
from utils.shortcuts import rand_str as _rand_str
from account.decorators import admin_role_required

from ..models import (BoardFolder, BoardPost, BoardFile, BoardRead, BoardPostVersion,
                      BoardComment, Branch, AcademyProfile, AcademyRole, STAFF_ROLES)
from ..services import viewable_branch_ids
from .exam import menu_denied

MAX_FILE = 50 * 1024 * 1024          # 파일 하나 50MB
MAX_POST = 200 * 1024 * 1024         # 한 글 합계 200MB
MAX_DEPTH = 3
# 실행파일만 막는다. 나머지는 무엇이 올라올지 미리 알 수 없어 막지 않는다.
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".scr", ".msi", ".com", ".pif", ".vbs", ".js", ".jar"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

_DIRECTOR_UP = {"HQ_ADMIN", "HR_ADMIN", "REGIONAL_MANAGER", "BRANCH_MANAGER"}
_HQ = {"HQ_ADMIN", "HR_ADMIN"}


def _name_of(u):
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username if u else ""


def _kst(dt):
    return str(dt + timedelta(hours=9))[:19] if dt else ""


def _role_of(user):
    p = AcademyProfile.objects.filter(user=user, is_deleted=False).first()
    return (p.role if p else ""), (p.branch_id if p else None)


def _is_super(user):
    return getattr(user, "admin_type", "") == "Super Admin"


def _can_see(folder, role, branch_id, is_super, user=None, seeable_owners=None):
    # 개인 폴더는 만든 사람이 쓰는 서랍이다. 다른 직원은 못 보고, 원장·본부는 본다
    # (회사 안에서 쓰는 것이라 관리 밖에 두지 않는다).
    if folder.scope == "PRIVATE":
        if user and folder.created_by_id == user.id:
            return True
        if is_super:
            return True
        if role in _DIRECTOR_UP and seeable_owners is not None:
            return folder.created_by_id in seeable_owners
        return False
    if is_super:
        return True
    if folder.scope == "ALL":
        return True
    if folder.scope == "DIRECTOR":
        return role in _DIRECTOR_UP
    if folder.scope == "HQ":
        return role in _HQ
    if folder.scope == "BRANCH":
        return folder.branch_id is None or folder.branch_id == branch_id
    return False


def _can_write(folder, role, branch_id, is_super, user=None):
    """글을 쓸 수 있는가. 폴더에 따로 정해 두지 않았으면 볼 수 있으면 쓸 수 있다."""
    if not _can_see(folder, role, branch_id, is_super, user):
        return False
    if folder.scope == "PRIVATE":
        return True
    ws = folder.write_scope or ""
    if not ws:
        return True
    if is_super:
        return True
    if ws == "DIRECTOR":
        return role in _DIRECTOR_UP
    if ws == "HQ":
        return role in _HQ
    return True


def _can_edit_folders(user):
    """폴더를 만들고 고치고 지우는 것은 원장 이상."""
    return _is_super(user) or _role_of(user)[0] in _DIRECTOR_UP


def _seeable_owners(user, role, is_super):
    """원장·본부가 개인 폴더를 볼 수 있는 직원. 원장은 자기 지점만."""
    if not (is_super or role in _DIRECTOR_UP):
        return None
    view = viewable_branch_ids(user)
    q = AcademyProfile.objects.filter(is_deleted=False, role__in=STAFF_ROLES)
    if view is not None:
        q = q.filter(branch_id__in=view)
    return set(q.values_list("user_id", flat=True))


def _folder_editable(f, user):
    """이 폴더를 고치고 지울 수 있는가. 개인 폴더는 만든 사람, 나머지는 원장 이상."""
    if f.scope == "PRIVATE":
        return f.created_by_id == user.id
    return _can_edit_folders(user)


def _kind_of(ext):
    if ext in IMG_EXT:
        return "img"
    if ext == ".pdf":
        return "pdf"
    if ext in (".zip", ".7z", ".rar", ".tar", ".gz"):
        return "zip"
    if ext in (".hwp", ".hwpx", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md"):
        return "doc"
    return "etc"


def _folder_row(f, unread=0, posts=0, multi=False, me=None, owners_meta=None):
    # 이름표를 줄마다 다는 대신 덩어리로 묶는다. 대부분이 우리 지점 폴더라 [전 지점]을
    # 붙이든 [우리 지점만]을 붙이든 흩어져 읽기 어렵다. 묶어 두면 이름표가 필요 없다.
    mine = bool(me and f.created_by_id == me.id)
    owner, orole, obranch = "", "", ""
    if f.scope == "PRIVATE" and f.created_by_id and owners_meta:
        orole, obranch = owners_meta.get(f.created_by_id, ("", ""))
    if f.scope == "PRIVATE":
        # 사람 이름을 덩어리로 세우면 '전 지점·인천청라' 와 같은 층에 섞여 무엇인지
        # 헷갈린다. 개인 폴더는 한 덩어리로 묶고, 누구 것인지는 줄에 적는다.
        # 내 서랍과 남의 서랍은 성질이 다르다. 내 것은 늘 쓰는 것이고, 남의 것은
        # 볼 일이 있을 때만 여는 것이다. 덩어리를 갈라 남의 것은 접어 둔다.
        if mine:
            gk, gl = "private", "개인 폴더"
        else:
            gk, gl = "staff", "직원 폴더"
            owner = _name_of(f.created_by) or "이름 없음"
    elif f.scope == "BRANCH" and f.branch_id:
        gk, gl = "b%d" % f.branch_id, f.branch.name
    else:
        gk, gl = "all", "전 지점"
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id, "icon": f.icon,
            "group_key": gk, "group_label": gl, "mine": mine, "owner": owner,
            "owner_id": f.created_by_id, "owner_role": orole, "owner_branch": obranch,
            "scope": f.scope, "branch_id": f.branch_id, "need_read": f.need_read,
            "versioned": f.versioned, "allow_comments": f.allow_comments,
            "write_scope": f.write_scope,
            "sort_mode": f.sort_mode, "order": f.order, "depth": f.depth,
            "pin": f.pin_when_collapsed,
            "posts": posts, "unread": unread}


def _file_row(x):
    return {"id": x.id, "name": x.name, "url": x.url, "thumb": x.thumb_url,
            "size": x.size, "kind": x.kind,
            "by": _name_of(x.uploaded_by) if x.uploaded_by_id else "",
            "time": _kst(x.create_time)}


def _post_row(p, me=None, with_body=False):
    d = {"id": p.id, "folder_id": p.folder_id, "title": p.title,
         "pinned": p.is_pinned, "author": _name_of(p.author) if p.author_id else "",
         "author_id": p.author_id, "time": _kst(p.create_time),
         "files": p.files.count(),
         "students": [{"id": u.id, "name": _name_of(u)} for u in p.students.all()]}
    if with_body:
        d["body"] = p.body
        d["file_list"] = [_file_row(x) for x in p.files.all()]
    return d


class BoardFolderAPI(APIView):
    """폴더 트리 읽기 · 만들기 · 고치기 · 지우기."""

    @admin_role_required
    def get(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        me = request.user
        role, bid = _role_of(me)
        sup = _is_super(me)
        view = viewable_branch_ids(me)
        owners = None
        if role in _DIRECTOR_UP or sup:
            oq = AcademyProfile.objects.filter(is_deleted=False, role__in=STAFF_ROLES)
            if view is not None:
                oq = oq.filter(branch_id__in=view)
            owners = set(oq.values_list("user_id", flat=True))
        folders = [f for f in BoardFolder.objects.filter(is_deleted=False)
                   .select_related("parent", "branch", "created_by", "created_by__userprofile")
                   if _can_see(f, role, bid, sup, me, owners)]
        ids = [f.id for f in folders]
        counts, unread = {}, {}
        for p in BoardPost.objects.filter(is_deleted=False, folder_id__in=ids) \
                                  .values_list("id", "folder_id"):
            counts[p[1]] = counts.get(p[1], 0) + 1
        # 읽음 확인 폴더의 안 읽은 글 수 — 폴더 이름 옆에 붙는다
        need = [f.id for f in folders if f.need_read]
        if need:
            seen = set(BoardRead.objects.filter(user=me, post__folder_id__in=need)
                       .values_list("post_id", flat=True))
            for pid, fid in BoardPost.objects.filter(is_deleted=False, folder_id__in=need) \
                                             .values_list("id", "folder_id"):
                if pid not in seen:
                    unread[fid] = unread.get(fid, 0) + 1
        multi = (view is None or len(view) > 1)
        # 개인 폴더 주인의 역할·지점 — 원장 > 강사 > 조교 차례로 늘어놓으려면 있어야 한다
        oids = {f.created_by_id for f in folders if f.scope == "PRIVATE" and f.created_by_id}
        ometa = {p.user_id: (p.role, p.branch.name if p.branch_id else "본부")
                 for p in AcademyProfile.objects.filter(user_id__in=oids, is_deleted=False)
                                                .select_related("branch")} if oids else {}
        rows = [_folder_row(f, unread.get(f.id, 0), counts.get(f.id, 0), multi, me, ometa)
                for f in folders]
        return self.success({
            "rows": rows, "can_edit": _can_edit_folders(me), "can_private": True,
            "max_depth": MAX_DEPTH, "max_file": MAX_FILE, "max_post": MAX_POST,
            "branches": [{"id": b.id, "name": b.name}
                         for b in Branch.objects.filter(is_active=True)],
        })

    @admin_role_required
    def post(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        d = request.data
        # 개인 폴더는 누구나 만든다. 남이 못 보는 자기 서랍이라 막을 까닭이 없다.
        if (d.get("scope") or "ALL") != "PRIVATE" and not _can_edit_folders(request.user):
            return self.error("함께 쓰는 폴더는 원장 이상만 만들 수 있습니다.")
        name = (d.get("name") or "").strip()
        if not name:
            return self.error("폴더 이름을 적어 주세요.")
        parent = None
        if d.get("parent_id"):
            parent = BoardFolder.objects.filter(id=d["parent_id"], is_deleted=False).first()
            if not parent:
                return self.error("위 폴더가 없습니다.")
            if parent.depth >= MAX_DEPTH:
                return self.error("폴더는 %d단까지만 만들 수 있습니다. 더 깊으면 찾기 어렵습니다."
                                  % MAX_DEPTH)
        f = BoardFolder.objects.filter(id=d.get("id")).first() if d.get("id") else None
        if f:
            # 자기 밑으로는 못 옮긴다(고리가 생긴다)
            if parent:
                x = parent
                while x:
                    if x.id == f.id:
                        return self.error("폴더를 자기 아래로 옮길 수 없습니다.")
                    x = x.parent
            if f.scope == "PRIVATE" and f.created_by_id != request.user.id:
                return self.error("남의 개인 폴더는 고칠 수 없습니다.")
            if f.scope != "PRIVATE" and not _can_edit_folders(request.user):
                return self.error("함께 쓰는 폴더는 원장 이상만 고칠 수 있습니다.")
            f.name, f.parent = name, parent
        else:
            sib = BoardFolder.objects.filter(parent=parent, is_deleted=False).count()
            f = BoardFolder(name=name, parent=parent, order=sib, created_by=request.user)
        f.icon = (d.get("icon") or "")[:8]
        f.scope = d.get("scope") or "ALL"
        f.branch_id = d.get("branch_id") if f.scope == "BRANCH" else None
        if f.scope == "PRIVATE" and not f.created_by_id:
            f.created_by = request.user
        f.need_read = bool(d.get("need_read"))
        f.versioned = bool(d.get("versioned"))
        f.allow_comments = bool(d.get("allow_comments"))
        f.write_scope = d.get("write_scope") or ""
        f.sort_mode = d.get("sort_mode") or "RECENT"
        f.pin_when_collapsed = bool(d.get("pin"))
        if d.get("order") is not None:
            try:
                f.order = max(0, int(d["order"]))
            except (TypeError, ValueError):
                pass
        f.save()
        return self.success({"id": f.id})

    @admin_role_required
    def put(self, request):
        """차례 바꾸기. {id, delta} — 같은 위 폴더 안에서만 움직인다."""
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        f = BoardFolder.objects.filter(id=request.data.get("id"), is_deleted=False).first()
        if not f:
            return self.error("폴더가 없습니다.")
        if not _folder_editable(f, request.user):
            return self.error("이 폴더를 고칠 권한이 없습니다.")
        sibs = list(BoardFolder.objects.filter(parent_id=f.parent_id, is_deleted=False)
                    .order_by("order", "id"))
        i = [x.id for x in sibs].index(f.id)
        j = i + int(request.data.get("delta") or 0)
        if j < 0 or j >= len(sibs):
            return self.success({"moved": False})
        sibs.insert(j, sibs.pop(i))
        for n, x in enumerate(sibs):
            if x.order != n:
                x.order = n
                x.save(update_fields=["order"])
        return self.success({"moved": True})

    @admin_role_required
    def delete(self, request):
        """지우기. 비어 있으면 그냥 지우고, 안에 무엇이 있으면 어떻게 할지 받는다.
        mode: up(위 폴더로) / move(고른 폴더로) / purge(글까지 함께)."""
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        f = BoardFolder.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not f:
            return self.error("폴더가 없습니다.")
        if not _folder_editable(f, request.user):
            return self.error("이 폴더를 지울 권한이 없습니다.")
        kids = list(BoardFolder.objects.filter(parent=f, is_deleted=False))
        posts = BoardPost.objects.filter(folder=f, is_deleted=False)
        n_posts = posts.count()
        mode = request.GET.get("mode") or ""
        if (kids or n_posts) and not mode:
            # 무엇이 들어 있는지 알려 주고 고르게 한다
            return self.success({"need_choice": True, "posts": n_posts,
                                 "folders": len(kids),
                                 "parent_id": f.parent_id,
                                 "parent_name": (f.parent.name if f.parent_id else "맨 위")})
        with transaction.atomic():
            if kids or n_posts:
                if mode == "purge":
                    posts.update(is_deleted=True)
                    for k in kids:
                        k.is_deleted = True
                        k.save(update_fields=["is_deleted"])
                else:
                    if mode == "move":
                        to = BoardFolder.objects.filter(id=request.GET.get("to"),
                                                        is_deleted=False).first()
                        if not to:
                            return self.error("옮길 폴더를 고르세요.")
                        if to.id == f.id:
                            return self.error("자기 자신으로는 옮길 수 없습니다.")
                    else:
                        to = f.parent
                        if to is None and kids:
                            pass        # 맨 위로 올라간다
                    if to is not None and to.depth + 1 > MAX_DEPTH and kids:
                        return self.error("옮기면 %d단을 넘습니다. 다른 폴더를 고르세요." % MAX_DEPTH)
                    posts.update(folder=to) if to else posts.update(folder=None)
                    if to is None and n_posts:
                        return self.error("글을 옮길 폴더를 고르세요(맨 위에는 글을 둘 수 없습니다).")
                    for k in kids:
                        k.parent = to
                        k.save(update_fields=["parent"])
            f.is_deleted = True
            f.save(update_fields=["is_deleted"])
        return self.success({"deleted": True})


class BoardPostAPI(APIView):
    """글 목록 · 보기 · 쓰기 · 지우기."""

    @admin_role_required
    def get(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        me = request.user
        role, bid = _role_of(me)
        sup = _is_super(me)
        pid = request.GET.get("id")
        if pid:
            p = BoardPost.objects.filter(id=pid, is_deleted=False) \
                                 .select_related("folder", "author").first()
            if not p:
                return self.error("글이 없습니다.")
            if not _can_see(p.folder, role, bid, sup, me, _seeable_owners(me, role, sup)):
                return self.error("이 폴더를 볼 권한이 없습니다.")
            if p.folder.need_read:
                BoardRead.objects.get_or_create(post=p, user=me)
            d = _post_row(p, me, with_body=True)
            d["folder_name"] = p.folder.name
            d["can_edit"] = sup or p.author_id == me.id or role in _DIRECTOR_UP
            d["versioned"] = p.folder.versioned
            d["can_write"] = _can_write(p.folder, role, bid, sup, me)
            d["allow_comments"] = p.folder.allow_comments
            if p.folder.allow_comments:
                d["comments"] = [
                    {"id": c.id, "body": c.body,
                     "author": _name_of(c.author) if c.author_id else "",
                     "mine": c.author_id == me.id, "time": _kst(c.create_time)}
                    for c in p.comments.filter(is_deleted=False).select_related("author")]
            if p.folder.versioned:
                d["versions"] = [_ver_row(v) for v in
                                 BoardPostVersion.objects.filter(post=p)
                                 .select_related("author")[:100]]
            # 누가 읽었는지는 챙겨야 하는 사람만 본다. 강사가 동료의 읽음 여부를 알
            # 까닭이 없다 — 읽었는지는 그대로 기록되지만 명단은 안 내려간다.
            if p.folder.need_read and (sup or role in _DIRECTOR_UP):
                seen = set(BoardRead.objects.filter(post=p).values_list("user_id", flat=True))
                # 인천 원장이 김포 직원의 읽음까지 볼 까닭이 없다. 챙길 수 있는 사람만 센다.
                view = viewable_branch_ids(me)
                sq = AcademyProfile.objects.filter(is_deleted=False, role__in=STAFF_ROLES)
                if view is not None:
                    sq = sq.filter(branch_id__in=view)
                staff = sq.select_related("user", "user__userprofile", "branch")
                d["reads"] = {"read": [_name_of(x.user) for x in staff if x.user_id in seen],
                              "unread": [_name_of(x.user) for x in staff if x.user_id not in seen],
                              "scope": ("전 지점" if view is None else
                                        " · ".join(sorted({x.branch.name for x in staff if x.branch_id})))}
            return self.success(d)

        f = BoardFolder.objects.filter(id=request.GET.get("folder_id"), is_deleted=False).first()
        if not f:
            return self.error("폴더를 고르세요.")
        if not _can_see(f, role, bid, sup, me, _seeable_owners(me, role, sup)):
            return self.error("이 폴더를 볼 권한이 없습니다.")
        qs = BoardPost.objects.filter(folder=f, is_deleted=False) \
                              .select_related("author").prefetch_related("students", "files")
        q = (request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
        if f.sort_mode == "MANUAL":
            qs = qs.order_by("-is_pinned", "order", "-id")
        else:
            qs = qs.order_by("-is_pinned", "-id")
        rows = [_post_row(p, me) for p in qs[:300]]
        if f.need_read:
            seen = set(BoardRead.objects.filter(user=me, post__folder=f)
                       .values_list("post_id", flat=True))
            for r in rows:
                r["read"] = r["id"] in seen
        return self.success({"rows": rows, "folder": _folder_row(f),
                             "folder_name": f.name, "need_read": f.need_read,
                             "can_write": _can_write(f, role, bid, sup, me)})

    @admin_role_required
    def post(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        d = request.data
        me = request.user
        role, bid = _role_of(me)
        sup = _is_super(me)
        f = BoardFolder.objects.filter(id=d.get("folder_id"), is_deleted=False).first()
        if not f:
            return self.error("폴더를 고르세요.")
        if not _can_write(f, role, bid, sup, me):
            return self.error("이 폴더에 글을 쓸 권한이 없습니다.")
        title = (d.get("title") or "").strip()
        if not title:
            return self.error("제목을 적어 주세요.")
        p = BoardPost.objects.filter(id=d.get("id"), is_deleted=False).first() if d.get("id") else None
        if p:
            if not (sup or p.author_id == me.id or role in _DIRECTOR_UP):
                return self.error("이 글을 고칠 권한이 없습니다.")
            p.folder, p.title, p.body = f, title, d.get("body") or ""
        else:
            p = BoardPost(folder=f, title=title, body=d.get("body") or "", author=me,
                          order=BoardPost.objects.filter(folder=f).count())
        p.is_pinned = bool(d.get("pinned"))
        p.save()
        if "student_ids" in d:
            p.students.set([int(x) for x in (d.get("student_ids") or [])])
        # 판 관리 폴더면 고칠 때마다 판이 쌓인다. 바뀐 뒤의 모습을 통째로 담는다 —
        # 차이만 저장하면 중간 판 하나가 깨질 때 뒤가 다 어긋난다.
        if f.versioned:
            eff = None
            if d.get("effective_date"):
                try:
                    eff = datetime.strptime(d["effective_date"], "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    eff = None
            last = BoardPostVersion.objects.filter(post=p).order_by("-rev").first()
            names = _json.dumps([x.name for x in p.files.all()], ensure_ascii=False)
            same = (last and last.title == p.title and last.body == p.body
                    and last.files == names and not (d.get("note") or "").strip())
            if not same:
                BoardPostVersion.objects.create(
                    post=p, rev=((last.rev + 1) if last else 1), title=p.title, body=p.body,
                    files=names, note=(d.get("note") or "").strip()[:255],
                    effective_date=eff, author=request.user)
        return self.success({"id": p.id})

    @admin_role_required
    def delete(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        me = request.user
        role, _ = _role_of(me)
        p = BoardPost.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not p:
            return self.error("글이 없습니다.")
        if not (_is_super(me) or p.author_id == me.id or role in _DIRECTOR_UP):
            return self.error("이 글을 지울 권한이 없습니다.")
        p.is_deleted = True
        p.save(update_fields=["is_deleted"])
        return self.success({"deleted": True})


class BoardFileAPI(APIView):
    """파일 올리기 · 지우기. 개수는 막지 않고 용량으로 막는다."""
    request_parsers = ()

    @admin_role_required
    def post(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        p = BoardPost.objects.filter(id=request.POST.get("post_id"), is_deleted=False).first()
        if not p:
            return self.error("글이 없습니다.")
        me = request.user
        role, _ = _role_of(me)
        if not (_is_super(me) or p.author_id == me.id or role in _DIRECTOR_UP):
            return self.error("이 글에 파일을 붙일 권한이 없습니다.")
        f = request.FILES.get("file")
        if not f:
            return self.error("파일이 없습니다.")
        if f.size > MAX_FILE:
            return self.error("파일 하나는 %dMB까지입니다." % (MAX_FILE // 1024 // 1024))
        used = sum(x.size for x in p.files.all())
        if used + f.size > MAX_POST:
            return self.error("글 하나에 붙일 수 있는 합계는 %dMB까지입니다(지금 %.1fMB)."
                              % (MAX_POST // 1024 // 1024, used / 1024.0 / 1024))
        ext = _os.path.splitext(f.name)[-1].lower()
        if ext in BLOCKED_EXT:
            return self.error("실행파일은 올릴 수 없습니다(%s)." % ext)
        _os.makedirs(_settings.UPLOAD_DIR, exist_ok=True)
        name = "board_" + _rand_str(16) + ext
        path = _os.path.join(_settings.UPLOAD_DIR, name)
        with open(path, "wb") as out:
            for chunk in f:
                out.write(chunk)
        thumb = ""
        if ext in IMG_EXT:
            # 호버는 빨라야 한다. 원본을 그대로 띄우면 느려서 미리보기 구실을 못 한다.
            try:
                from PIL import Image
                im = Image.open(path)
                im.thumbnail((360, 360))
                tname = "board_t_" + _rand_str(16) + ".jpg"
                im.convert("RGB").save(_os.path.join(_settings.UPLOAD_DIR, tname), quality=82)
                thumb = "%s/%s" % (_settings.UPLOAD_PREFIX, tname)
            except Exception:
                thumb = ""
        x = BoardFile.objects.create(
            post=p, name=f.name[:255], url="%s/%s" % (_settings.UPLOAD_PREFIX, name),
            thumb_url=thumb, size=f.size, kind=_kind_of(ext),
            order=p.files.count(), uploaded_by=me)
        return self.success(_file_row(x))

    @admin_role_required
    def delete(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        x = BoardFile.objects.filter(id=request.GET.get("id")).select_related("post").first()
        if not x:
            return self.error("파일이 없습니다.")
        me = request.user
        role, _ = _role_of(me)
        if not (_is_super(me) or x.post.author_id == me.id or role in _DIRECTOR_UP):
            return self.error("이 파일을 지울 권한이 없습니다.")
        x.delete()
        return self.success({"deleted": True})


def _ver_row(v):
    try:
        files = _json.loads(v.files) if v.files else []
    except (ValueError, TypeError):
        files = []
    return {"id": v.id, "rev": v.rev, "title": v.title, "note": v.note,
            "effective_date": str(v.effective_date) if v.effective_date else "",
            "files": files, "by": _name_of(v.author) if v.author_id else "",
            "time": _kst(v.create_time)}


def _diff_lines(old_text, new_text):
    """줄 단위로 견준다. 무엇이 늘고 줄었는지가 눈에 보여야 개정을 설명할 수 있다."""
    a = (old_text or "").splitlines()
    b = (new_text or "").splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                out.append({"t": "=", "s": a[k]})
        else:
            for k in range(i1, i2):
                out.append({"t": "-", "s": a[k]})
            for k in range(j1, j2):
                out.append({"t": "+", "s": b[k]})
    return out


class BoardVersionAPI(APIView):
    """판 하나 보기 · 두 판 견주기. {post_id, rev} 또는 {post_id, a, b}."""

    @admin_role_required
    def get(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        p = BoardPost.objects.filter(id=request.GET.get("post_id"), is_deleted=False) \
                             .select_related("folder").first()
        if not p:
            return self.error("글이 없습니다.")
        role, bid = _role_of(request.user)
        if not _can_see(p.folder, role, bid, _is_super(request.user), request.user, _seeable_owners(request.user, role, _is_super(request.user))):
            return self.error("이 폴더를 볼 권한이 없습니다.")
        vs = {v.rev: v for v in BoardPostVersion.objects.filter(post=p).select_related("author")}
        if not vs:
            return self.success({"rows": [], "diff": []})
        if request.GET.get("rev"):
            v = vs.get(int(request.GET["rev"]))
            if not v:
                return self.error("그 판이 없습니다.")
            return self.success({"one": _ver_row(v), "body": v.body})
        try:
            rb = int(request.GET.get("b") or max(vs))
            ra = int(request.GET.get("a") or (rb - 1))
        except (TypeError, ValueError):
            return self.error("판 번호가 올바르지 않습니다.")
        vb = vs.get(rb)
        if not vb:
            return self.error("그 판이 없습니다.")
        va = vs.get(ra)
        old_body, old_files = (va.body if va else ""), (_ver_row(va)["files"] if va else [])
        new_files = _ver_row(vb)["files"]
        return self.success({
            "a": (_ver_row(va) if va else None), "b": _ver_row(vb),
            "title_changed": bool(va and va.title != vb.title),
            "old_title": (va.title if va else ""), "new_title": vb.title,
            "diff": _diff_lines(old_body, vb.body),
            "files_added": [x for x in new_files if x not in old_files],
            "files_removed": [x for x in old_files if x not in new_files],
        })


# ── 학생 기록 · 연결된 글 (docs/82 5·6장) ──

from ..models import StudentRecord, StudentRecordFile, OptionItem      # noqa: E402
from ..services import can_manage_branch, can_view_branch              # noqa: E402


def _rec_file_row(x):
    return {"id": x.id, "name": x.name, "url": x.url, "thumb": x.thumb_url,
            "size": x.size, "kind": x.kind,
            "by": _name_of(x.uploaded_by) if x.uploaded_by_id else "",
            "time": _kst(x.create_time)}


def _rec_row(r, with_files=True):
    d = {"id": r.id, "kind": r.kind, "date": str(r.date) if r.date else "",
         "title": r.title, "body": r.body,
         "author": _name_of(r.author) if r.author_id else "",
         "time": _kst(r.create_time)}
    if with_files:
        d["file_list"] = [_rec_file_row(x) for x in r.files.all()]
    return d


def _student_ok(request, student_id, edit=False):
    """그 학생을 볼(고칠) 수 있는가."""
    prof = AcademyProfile.objects.filter(user_id=student_id, is_deleted=False).first()
    if not prof:
        return False
    return (can_manage_branch(request.user, prof.branch_id) if edit
            else can_view_branch(request.user, prof.branch_id))


class StudentRecordAPI(APIView):
    """학생 개인 기록. 종류는 관리자가 정하는 목록이라 학년이 올라도 화면을 안 고친다."""

    @admin_role_required
    def get(self, request):
        sid = request.GET.get("student_id")
        if not _student_ok(request, sid):
            return self.error("이 학생을 볼 권한이 없습니다.")
        qs = StudentRecord.objects.filter(student_id=sid, is_deleted=False) \
                                  .select_related("author").prefetch_related("files")
        kind = request.GET.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        kinds = [{"value": o.value, "label": o.label}
                 for o in OptionItem.objects.filter(category="student_record", is_active=True)
                                            .order_by("order", "id")]
        return self.success({"rows": [_rec_row(r) for r in qs[:300]], "kinds": kinds,
                             "can_edit": _student_ok(request, sid, edit=True)})

    @admin_role_required
    def post(self, request):
        d = request.data
        sid = d.get("student_id")
        if not _student_ok(request, sid, edit=True):
            return self.error("이 학생을 고칠 권한이 없습니다.")
        title = (d.get("title") or "").strip()
        if not title:
            return self.error("제목을 적어 주세요.")
        dt = None
        if d.get("date"):
            try:
                dt = datetime.strptime(d["date"], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                dt = None
        r = StudentRecord.objects.filter(id=d.get("id"), is_deleted=False).first() if d.get("id") else None
        if r:
            if str(r.student_id) != str(sid):
                return self.error("학생이 다릅니다.")
        else:
            r = StudentRecord(student_id=sid, author=request.user)
        r.kind = d.get("kind") or ""
        r.date, r.title, r.body = dt, title, d.get("body") or ""
        r.save()
        return self.success(_rec_row(r))

    @admin_role_required
    def delete(self, request):
        r = StudentRecord.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not r:
            return self.error("기록이 없습니다.")
        if not _student_ok(request, r.student_id, edit=True):
            return self.error("이 학생을 고칠 권한이 없습니다.")
        r.is_deleted = True
        r.save(update_fields=["is_deleted"])
        return self.success({"deleted": True})


class StudentRecordFileAPI(APIView):
    """학생 기록 파일. 게시판과 같은 잣대를 쓴다."""
    request_parsers = ()

    @admin_role_required
    def post(self, request):
        r = StudentRecord.objects.filter(id=request.POST.get("record_id"), is_deleted=False).first()
        if not r:
            return self.error("기록이 없습니다.")
        if not _student_ok(request, r.student_id, edit=True):
            return self.error("이 학생을 고칠 권한이 없습니다.")
        f = request.FILES.get("file")
        if not f:
            return self.error("파일이 없습니다.")
        if f.size > MAX_FILE:
            return self.error("파일 하나는 %dMB까지입니다." % (MAX_FILE // 1024 // 1024))
        used = sum(x.size for x in r.files.all())
        if used + f.size > MAX_POST:
            return self.error("기록 하나에 붙일 수 있는 합계는 %dMB까지입니다."
                              % (MAX_POST // 1024 // 1024))
        ext = _os.path.splitext(f.name)[-1].lower()
        if ext in BLOCKED_EXT:
            return self.error("실행파일은 올릴 수 없습니다(%s)." % ext)
        _os.makedirs(_settings.UPLOAD_DIR, exist_ok=True)
        name = "rec_" + _rand_str(16) + ext
        path = _os.path.join(_settings.UPLOAD_DIR, name)
        with open(path, "wb") as out:
            for chunk in f:
                out.write(chunk)
        thumb = ""
        if ext in IMG_EXT:
            try:
                from PIL import Image
                im = Image.open(path)
                im.thumbnail((360, 360))
                tname = "rec_t_" + _rand_str(16) + ".jpg"
                im.convert("RGB").save(_os.path.join(_settings.UPLOAD_DIR, tname), quality=82)
                thumb = "%s/%s" % (_settings.UPLOAD_PREFIX, tname)
            except Exception:
                thumb = ""
        x = StudentRecordFile.objects.create(
            record=r, name=f.name[:255], url="%s/%s" % (_settings.UPLOAD_PREFIX, name),
            thumb_url=thumb, size=f.size, kind=_kind_of(ext),
            order=r.files.count(), uploaded_by=request.user)
        return self.success(_rec_file_row(x))

    @admin_role_required
    def delete(self, request):
        x = StudentRecordFile.objects.filter(id=request.GET.get("id")) \
                                     .select_related("record").first()
        if not x:
            return self.error("파일이 없습니다.")
        if not _student_ok(request, x.record.student_id, edit=True):
            return self.error("이 학생을 고칠 권한이 없습니다.")
        x.delete()
        return self.success({"deleted": True})


class StudentBoardLinkAPI(APIView):
    """학생에게 걸린 게시판 글. 볼 수 있는 폴더의 글만 돌려준다."""

    @admin_role_required
    def get(self, request):
        sid = request.GET.get("student_id")
        if not _student_ok(request, sid):
            return self.error("이 학생을 볼 권한이 없습니다.")
        role, bid = _role_of(request.user)
        sup = _is_super(request.user)
        rows = []
        for p in BoardPost.objects.filter(students__id=sid, is_deleted=False) \
                                  .select_related("folder", "author").order_by("-id")[:200]:
            if not _can_see(p.folder, role, bid, sup, me, _seeable_owners(me, role, sup)):
                continue
            rows.append({"id": p.id, "folder_id": p.folder_id, "folder": p.folder.name,
                         "icon": p.folder.icon, "title": p.title,
                         "author": _name_of(p.author) if p.author_id else "",
                         "files": p.files.count(), "time": _kst(p.create_time)})
        return self.success({"rows": rows})


class BoardCommentAPI(APIView):
    """덧글. 폴더에서 켠 곳에만 달린다."""

    @admin_role_required
    def post(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        p = BoardPost.objects.filter(id=request.data.get("post_id"), is_deleted=False) \
                             .select_related("folder").first()
        if not p:
            return self.error("글이 없습니다.")
        role, bid = _role_of(request.user)
        if not _can_see(p.folder, role, bid, _is_super(request.user), request.user, _seeable_owners(request.user, role, _is_super(request.user))):
            return self.error("이 폴더를 볼 권한이 없습니다.")
        if not p.folder.allow_comments:
            return self.error("이 폴더는 덧글을 받지 않습니다.")
        body = (request.data.get("body") or "").strip()
        if not body:
            return self.error("덧글을 적어 주세요.")
        c = BoardComment.objects.create(post=p, body=body, author=request.user)
        return self.success({"id": c.id, "body": c.body, "author": _name_of(request.user),
                             "mine": True, "time": _kst(c.create_time)})

    @admin_role_required
    def delete(self, request):
        _d = menu_denied(request.user, "board")
        if _d:
            return self.error(_d)
        c = BoardComment.objects.filter(id=request.GET.get("id"), is_deleted=False).first()
        if not c:
            return self.error("덧글이 없습니다.")
        me = request.user
        role, _ = _role_of(me)
        if not (_is_super(me) or c.author_id == me.id or role in _DIRECTOR_UP):
            return self.error("이 덧글을 지울 권한이 없습니다.")
        c.is_deleted = True
        c.save(update_fields=["is_deleted"])
        return self.success({"deleted": True})
