"""학부모 확인(정보 대조) API.

등록은 직원이 아는 만큼 채워 바로 끝내고, 학부모에게는 빈 양식을 보내 독립적으로
쓰게 한 뒤 값이 다른 항목만 직원이 확인해 최종값을 정한다.

빈 양식으로 받는 이유: 우리 값을 미리 채워 보여주면 학부모는 그대로 넘기게 되어
대조의 의미가 없어진다. 어느 쪽이 맞다고 미리 정하지도 않는다 — 상담 때 잘못
받아적을 수도, 학부모가 급히 쓰다 틀릴 수도 있어서 항목을 봐야 알 수 있다.
"""
import json as _json
import re
from datetime import timedelta

from django.utils.timezone import now

from utils.api import APIView
from utils.shortcuts import rand_str
from account.decorators import admin_role_required
from account.models import User

from ..models import ProfileVerification, StudentProfile, AcademyProfile
from ..services import can_manage_branch, can_view_branch, viewable_branch_ids

LINK_DAYS = 14

# 직원도 쓰고 학부모도 쓰는 항목 — 값이 다르면 확인 대상
COMPARE_FIELDS = [
    ("birth_date", "생년월일"),
    ("gender", "성별"),
    ("school_type", "학교 구분"),
    ("school_name", "학교(학과)"),
    ("grade", "학년"),
    ("student_phone", "학생 휴대폰"),
    ("parent_name", "보호자 이름"),
    ("parent_phone", "보호자 연락처"),
    ("parent_relation", "보호자 관계"),
    ("guardian2_phone", "기타 보호자 휴대폰"),
    ("guardian2_relation", "기타 보호자 관계"),
    ("notify_optin", "등하원 알림"),
    ("zipcode", "우편번호"),
    ("address", "주소"),
    ("address_detail", "상세주소"),
]

# 학부모만 할 수 있는 것 — 비교 대상이 아니라 그대로 반영
PARENT_ONLY_FIELDS = [
    ("consent_privacy", "개인정보 동의"),
    ("consent_guardian_name", "법정대리인 성명"),
    ("consent_signature", "법정대리인 서명"),
]

FIELD_LABEL = dict(COMPARE_FIELDS + PARENT_ONLY_FIELDS)

_SCHOOL_SUFFIX = ("초등학교", "중학교", "고등학교", "학교", "초", "중", "고")


def norm(field, v):
    """표기 차이(하이픈·띄어쓰기·학교명 접미사)를 걷어낸 비교용 값.
    이걸 안 하면 '010-1234-5678'과 '01012345678'이 다르다고 나와, 매번 모든 항목이
    빨갛게 떠 아무도 확인하지 않게 된다."""
    if isinstance(v, bool):
        return "1" if v else "0"
    s = ("" if v is None else str(v)).strip()
    if not s:
        return ""
    if field in ("parent_phone", "student_phone", "guardian2_phone", "zipcode"):
        return "".join(ch for ch in s if ch.isdigit())
    if field == "grade":
        d = "".join(ch for ch in s if ch.isdigit())
        return d or s
    if field == "school_name":
        s = re.sub(r"\s+", "", s)
        for suf in _SCHOOL_SUFFIX:
            if len(s) > len(suf) and s.endswith(suf):
                return s[: -len(suf)]
        return s
    if field in ("address", "address_detail"):
        return re.sub(r"\s+", " ", s).strip()
    if field == "notify_optin":
        return "1" if s in ("1", "True", "true", "Y", "수신") else "0"
    return re.sub(r"\s+", " ", s)


def profile_values(sp):
    """학생 프로필에서 비교 대상 값만 뽑는다."""
    out = {}
    for f, _ in COMPARE_FIELDS:
        v = getattr(sp, f, "")
        if f == "birth_date":
            v = str(v) if v else ""
        out[f] = v
    return out


def load_json(s):
    try:
        return _json.loads(s) if s else {}
    except (ValueError, TypeError):
        return {}


def name_of(u):
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username if u else ""


def build_diff(staff, parent):
    """직원 값과 학부모 값을 세 갈래로 나눈다.
    - conflict: 실제로 값이 다름 → 반드시 골라야 함(기본 선택 없음)
    - format:   표기만 다름 → 접어두고 우리 값 유지
    - added:    우리가 비운 곳을 학부모가 채움 → 충돌이 아니라 추가
    """
    conflict, fmt, added, same = [], [], [], []
    for f, label in COMPARE_FIELDS:
        sv = staff.get(f, "")
        pv = parent.get(f, "")
        sv_s = "" if sv is None else (str(sv) if not isinstance(sv, bool) else sv)
        pv_s = "" if pv is None else (str(pv) if not isinstance(pv, bool) else pv)
        ns, np_ = norm(f, sv), norm(f, pv)
        row = {"field": f, "label": label, "staff": sv_s, "parent": pv_s}
        if not np_:
            same.append(row)            # 학부모가 비움 → 우리 값 유지
        elif not ns:
            added.append(row)
        elif ns == np_:
            if str(sv_s).strip() != str(pv_s).strip():
                fmt.append(row)         # 뜻은 같고 표기만 다름
            else:
                same.append(row)
        else:
            conflict.append(row)
    return {"conflict": conflict, "format": fmt, "added": added, "same_count": len(same)}


def verification_row(v, with_diff=False):
    sp = getattr(v.student, "student_profile", None)
    row = {
        "id": v.id, "student_id": v.student_id, "student_name": name_of(v.student),
        "status": v.status, "token": v.token,
        "expires": str(v.expires_at + timedelta(hours=9))[:16] if v.expires_at else "",
        "expired": bool(v.expires_at and v.expires_at < now()),
        "submitted_at": str(v.submitted_at + timedelta(hours=9))[:16] if v.submitted_at else "",
        "created_by": name_of(v.created_by), "created_at": str(v.create_time + timedelta(hours=9))[:16],
        "resolved_by": name_of(v.resolved_by),
        "resolved_at": str(v.resolved_at + timedelta(hours=9))[:16] if v.resolved_at else "",
        "waiting_days": (now().date() - (v.create_time.date())).days,
    }
    if with_diff and v.status == ProfileVerification.SUBMITTED:
        # 대조는 '지금의 학생 정보'와 한다. 링크를 보낸 뒤 직원이 값을 고쳤을 수 있으므로
        # 생성 시점 스냅샷이 아니라 현재 값이 맞다.
        staff = profile_values(sp) if sp else load_json(v.staff_snapshot)
        parent = load_json(v.parent_data)
        row["diff"] = build_diff(staff, parent)
        row["parent_only"] = [{"field": f, "label": lb, "value": parent.get(f, "")}
                              for f, lb in PARENT_ONLY_FIELDS]
    return row


class ProfileVerifyAdminAPI(APIView):
    """확인 링크 발급·목록·취소. GET ?student_id= 또는 ?pending=1(대기 목록)"""

    @admin_role_required
    def get(self, request):
        sid = request.GET.get("student_id")
        if sid:
            u = User.objects.filter(id=sid).first()
            if not u:
                return self.error("학생이 없습니다.")
            ap = getattr(u, "academy_profile", None)
            if ap and not can_view_branch(request.user, ap.branch_id):
                return self.error("권한이 없습니다.")
            qs = ProfileVerification.objects.filter(student_id=sid).select_related(
                "student", "student__userprofile", "created_by", "resolved_by")
            return self.success([verification_row(v, with_diff=True) for v in qs[:20]])

        view = viewable_branch_ids(request.user)
        qs = ProfileVerification.objects.filter(
            status__in=[ProfileVerification.SENT, ProfileVerification.SUBMITTED]).select_related(
            "student", "student__userprofile", "created_by")
        rows = []
        for v in qs[:300]:
            ap = getattr(v.student, "academy_profile", None)
            if view is not None and ap and ap.branch_id not in view:
                continue
            rows.append(verification_row(v))
        rows.sort(key=lambda r: (r["status"] != "SUBMITTED", -r["waiting_days"]))
        return self.success(rows)

    @admin_role_required
    def post(self, request):
        """확인 링크 발급(재발급 포함). {student_id}"""
        u = User.objects.filter(id=request.data.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        ap = getattr(u, "academy_profile", None)
        if ap and not can_manage_branch(request.user, ap.branch_id):
            return self.error("권한이 없습니다.")
        sp = getattr(u, "student_profile", None)
        if not sp:
            return self.error("학생 정보가 없습니다.")
        # 진행 중인 링크가 있으면 새로 만들지 않고 토큰만 새로 발급한다(링크가 여러 개
        # 돌아다니면 어느 것이 최신인지 알 수 없음).
        v = ProfileVerification.objects.filter(
            student=u, status__in=[ProfileVerification.SENT, ProfileVerification.SUBMITTED]).first()
        if not v:
            v = ProfileVerification(student=u, created_by=request.user)
        v.token = rand_str(24)
        v.expires_at = now() + timedelta(days=LINK_DAYS)
        v.status = ProfileVerification.SENT
        v.staff_snapshot = _json.dumps(profile_values(sp), ensure_ascii=False)
        v.parent_data = ""
        v.submitted_at = None
        v.save()
        url = request.build_absolute_uri("/portal/?verify=" + v.token)
        return self.success({"id": v.id, "token": v.token, "url": url,
                             "expires": str(v.expires_at + timedelta(hours=9))[:16],
                             "student_name": name_of(u)})

    @admin_role_required
    def delete(self, request):
        """확인 요청 취소(링크 무효화)."""
        v = ProfileVerification.objects.filter(id=request.GET.get("id")).select_related("student").first()
        if not v:
            return self.error("확인 요청이 없습니다.")
        ap = getattr(v.student, "academy_profile", None)
        if ap and not can_manage_branch(request.user, ap.branch_id):
            return self.error("권한이 없습니다.")
        v.status = ProfileVerification.CANCELLED
        v.token = ""
        v.save(update_fields=["status", "token"])
        return self.success("ok")


class ProfileVerifyResolveAPI(APIView):
    """대조 결과 확정. {id, values:{field: 최종값}, note?}
    values 에 담긴 값이 그대로 학생 정보가 된다(우리 값·학부모 값·직접 입력 무엇이든)."""

    @admin_role_required
    def post(self, request):
        v = ProfileVerification.objects.filter(id=request.data.get("id")).select_related("student").first()
        if not v:
            return self.error("확인 요청이 없습니다.")
        if v.status != ProfileVerification.SUBMITTED:
            return self.error("아직 학부모가 제출하지 않았습니다.")
        ap = getattr(v.student, "academy_profile", None)
        if ap and not can_manage_branch(request.user, ap.branch_id):
            return self.error("권한이 없습니다.")
        sp = getattr(v.student, "student_profile", None)
        if not sp:
            return self.error("학생 정보가 없습니다.")

        parent = load_json(v.parent_data)
        staff = profile_values(sp)
        diff = build_diff(staff, parent)
        values = request.data.get("values") or {}

        # 값이 다른 항목은 반드시 골라야 한다. 기본값을 미리 찍어두면 그냥 승인해 버려
        # 대조 자체가 무의미해지므로 서버에서도 막는다.
        missing = [r["label"] for r in diff["conflict"] if r["field"] not in values]
        if missing:
            return self.error("확인이 끝나지 않은 항목이 있습니다: " + ", ".join(missing))

        changed = []
        for f, label in COMPARE_FIELDS:
            if f not in values:
                continue
            new = values[f]
            old = staff.get(f, "")
            if f == "notify_optin":
                new = bool(new) if isinstance(new, bool) else str(new) in ("1", "true", "True", "수신")
            elif f == "birth_date":
                new = (new or "") or None
            else:
                new = ("" if new is None else str(new)).strip()
            if str(old or "") == str(new or ""):
                continue
            setattr(sp, f, new)
            changed.append("%s: %s → %s" % (label, old or "(없음)", new or "(없음)"))

        # 학부모만 쓸 수 있는 항목은 비교 없이 그대로 반영
        for f, label in PARENT_ONLY_FIELDS:
            pv = parent.get(f)
            if pv in (None, ""):
                continue
            if getattr(sp, f, "") != pv:
                setattr(sp, f, pv)
                changed.append("%s 반영" % label)
        if parent.get("consent_privacy") and not sp.consent_date:
            sp.consent_date = now().date()

        # 학생 정보 변경 이력에 남긴다(학생 상세에서 그대로 보이는 그 이력)
        log = load_json(sp.edit_log) or []
        if not isinstance(log, list):
            log = []
        log.append({"time": str(now() + timedelta(hours=9))[:16], "by": name_of(request.user),
                    "items": ["학부모 확인 완료"] + changed})
        sp.edit_log = _json.dumps(log, ensure_ascii=False)
        sp.save()

        v.status = ProfileVerification.DONE
        v.resolved_data = _json.dumps({"values": values, "changed": changed}, ensure_ascii=False)
        v.resolved_by = request.user
        v.resolved_at = now()
        v.token = ""
        v.save()
        return self.success({"changed": changed})


class ProfileVerifyPublicAPI(APIView):
    """무로그인 학부모 확인 양식. GET ?token= / POST 제출.

    빈 양식으로 내려준다 — 학생 이름만 고정으로 보여주고(다른 아이 걸 쓰는 사고 방지)
    나머지는 학부모가 직접 채운다."""

    def get(self, request):
        token = (request.GET.get("token") or "").strip()
        v = ProfileVerification.objects.select_related("student").filter(token=token).first() if token else None
        if not v:
            return self.error("링크가 올바르지 않습니다.")
        if v.status == ProfileVerification.DONE:
            return self.error("이미 확인이 완료되었습니다. 수정이 필요하면 학원에 문의해 주세요.")
        if v.status == ProfileVerification.CANCELLED:
            return self.error("사용할 수 없는 링크입니다. 학원에 문의해 주세요.")
        if v.expires_at and v.expires_at < now():
            return self.error("링크가 만료되었습니다(%d일 경과). 학원에 재발급을 요청해 주세요." % LINK_DAYS)
        ap = getattr(v.student, "academy_profile", None)
        return self.success({
            "student_name": name_of(v.student),
            "branch": (ap.branch.name if (ap and ap.branch_id) else ""),
            "already_submitted": v.status == ProfileVerification.SUBMITTED,
            "expires": str(v.expires_at + timedelta(hours=9))[:16] if v.expires_at else "",
            "fields": [{"field": f, "label": lb} for f, lb in COMPARE_FIELDS],
        })

    def post(self, request):
        data = request.data
        token = (data.get("token") or "").strip()
        v = ProfileVerification.objects.select_related("student").filter(token=token).first() if token else None
        if not v:
            return self.error("링크가 올바르지 않습니다.")
        if v.status == ProfileVerification.DONE:
            return self.error("이미 확인이 완료되어 수정할 수 없습니다. 학원에 문의해 주세요.")
        if v.status == ProfileVerification.CANCELLED:
            return self.error("사용할 수 없는 링크입니다.")
        if v.expires_at and v.expires_at < now():
            return self.error("링크가 만료되었습니다(%d일 경과)." % LINK_DAYS)

        payload = {}
        for f, _ in COMPARE_FIELDS:
            val = data.get(f)
            payload[f] = bool(val) if f == "notify_optin" else ("" if val is None else str(val).strip())
        for f, _ in PARENT_ONLY_FIELDS:
            val = data.get(f)
            payload[f] = bool(val) if f == "consent_privacy" else ("" if val is None else str(val).strip())

        if not payload.get("birth_date"):
            return self.error("생년월일을 입력해 주세요.")
        if not payload.get("parent_phone"):
            return self.error("보호자 연락처를 입력해 주세요.")
        if not payload.get("consent_privacy"):
            return self.error("개인정보 수집·이용에 동의해 주세요.")
        if not payload.get("consent_guardian_name"):
            return self.error("법정대리인 성명을 입력해 주세요.")

        v.parent_data = _json.dumps(payload, ensure_ascii=False)
        v.status = ProfileVerification.SUBMITTED
        v.submitted_at = now()
        v.save(update_fields=["parent_data", "status", "submitted_at"])
        return self.success({"ok": True, "student_name": name_of(v.student)})


# ─────────────────────── 등하원 안내 음성 ───────────────────────

class StudentVoiceAdminAPI(APIView):
    """학생 이름 안내 음성 관리. GET 현황 / POST 생성(개별·일괄) / DELETE 삭제."""

    @admin_role_required
    def get(self, request):
        from ..services_voice import voice_status, voice_for, voice_url
        from ..models import StudentProfile, EnrollmentStatus
        view = viewable_branch_ids(request.user)
        qs = StudentProfile.objects.select_related("user", "user__userprofile").exclude(
            enrollment_status=EnrollmentStatus.WITHDRAWN)
        rows, missing = [], 0
        for sp in qs:
            ap = getattr(sp.user, "academy_profile", None)
            if view is not None and ap and ap.branch_id not in view:
                continue
            st = voice_status(sp.user_id)
            ok = all(st.values())
            if not ok:
                missing += 1
            rows.append({"student_id": sp.user_id, "name": name_of(sp.user),
                         "gender": sp.gender or "", "voice": voice_for(sp.gender),
                         "ready": ok, "detail": st,
                         "url_in": voice_url(sp.user_id, "in"),
                         "url_out": voice_url(sp.user_id, "out")})
        rows.sort(key=lambda r: (r["ready"], r["name"]))
        return self.success({"rows": rows, "total": len(rows), "missing": missing})

    @admin_role_required
    def post(self, request):
        from ..services_voice import build_student_voice, has_voice
        from ..models import StudentProfile, EnrollmentStatus
        sid = request.data.get("student_id")
        if sid:
            u = User.objects.filter(id=sid).first()
            if not u:
                return self.error("학생이 없습니다.")
            ap = getattr(u, "academy_profile", None)
            if ap and not can_manage_branch(request.user, ap.branch_id):
                return self.error("권한이 없습니다.")
            ok, err = build_student_voice(u)
            return self.success({"made": 1 if ok else 0, "failed": ([name_of(u)] if not ok else []),
                                 "error": err})
        # 일괄 — 이미 있는 학생은 건너뛴다(only_missing=0 이면 전부 다시 만든다)
        only_missing = str(request.data.get("only_missing", "1")) not in ("0", "false", "False")
        view = viewable_branch_ids(request.user)
        made, failed = 0, []
        for sp in StudentProfile.objects.select_related("user", "user__userprofile").exclude(
                enrollment_status=EnrollmentStatus.WITHDRAWN):
            ap = getattr(sp.user, "academy_profile", None)
            if view is not None and ap and ap.branch_id not in view:
                continue
            if only_missing and has_voice(sp.user_id):
                continue
            ok, err = build_student_voice(sp.user)
            if ok:
                made += 1
            else:
                failed.append(name_of(sp.user))
        return self.success({"made": made, "failed": failed})

    @admin_role_required
    def delete(self, request):
        from ..services_voice import delete_student_voice
        u = User.objects.filter(id=request.GET.get("student_id")).first()
        if not u:
            return self.error("학생이 없습니다.")
        ap = getattr(u, "academy_profile", None)
        if ap and not can_manage_branch(request.user, ap.branch_id):
            return self.error("권한이 없습니다.")
        delete_student_voice(u.id)
        return self.success("ok")
