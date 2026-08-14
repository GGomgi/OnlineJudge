import os
import json as _json
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from utils.api import APIView, validate_serializer, CSRFExemptAPIView
from utils.shortcuts import rand_str

from account.models import User, UserProfile
from ..models import (AcademyProfile, AcademyRole, Branch, SignupRequest, CourseClass,
                      AttendanceRecord, Lead, OptionItem)
from ..models import (StudentTimetable, GuardianStudent, StaffProfile, STAFF_ROLES,
                      HRNotice, StaffDocument, StaffProfileHistory)
from ..models import DevRequest, DevRequestComment, Notification, Message
from ..models import StudentProfile, DailyAttendance, EnrollmentStatus, LessonOccurrence, OccurrenceStatus
from ..models import KioskDevice, KioskDeviceStatus, AttendanceChange
from .admin import ensure_occurrences, _adhoc_lesson_rows


def _kst_dt_str(dt):
    """저장된 UTC datetime을 KST(+9h) 'YYYY-MM-DD HH:MM' 문자열로. API 응답에 create_time 등
    사용자 표시용 시각을 담을 때는 str(dt)[:16] 대신 반드시 이 함수를 써야 한다(그렇지 않으면 9시간 어긋남)."""
    if not dt:
        return ""
    return str(dt + timedelta(hours=9))[:16]


def _hm_kst(dt):
    """저장된 UTC datetime을 KST(+9) HH:MM 문자열로."""
    if not dt:
        return ""
    return (dt + timedelta(hours=9)).strftime("%H:%M")


def _kst_today():
    return (now() + timedelta(hours=9)).date()


def _norm_phone(v):
    """전화번호에서 숫자만 추출."""
    return "".join(ch for ch in (v or "") if ch.isdigit())


def _name_of(u):
    if not u:
        return None
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username


TRACKED_HR_FIELDS = ["zipcode", "address", "address_detail", "phone",
                     "dependents_decided", "dependents", "emergency_contacts",
                     "sex_offense_consent",
                     # 누가 언제 서류를 면제했는지는 나중에 근거가 필요하다
                     "waived_docs"]


def _doc_data(d):
    return {"id": d.id, "group": d.group, "title": d.title, "url": d.url,
            "doc_date": str(d.doc_date) if d.doc_date else None,
            "uploaded_at": _kst_dt_str(d.create_time), "order": d.order,
            "visible_to_staff": d.visible_to_staff}


def record_hr_history(staff_user, actor, before, after, reason=""):
    rows = []
    for f in TRACKED_HR_FIELDS:
        o = str(before.get(f, "")); n = str(after.get(f, ""))
        if o != n:
            rows.append(StaffProfileHistory(user=staff_user, actor=actor, field=f,
                                            old_value=o, new_value=n, reason=reason))
    if rows:
        StaffProfileHistory.objects.bulk_create(rows)
from ..serializers import (AcademySignupSerializer, BranchSerializer,
                           SignupRequestSerializer, CourseClassSerializer,
                           ClassSessionSerializer, LeadCreateSerializer,
                           StudentTimetableSerializer, SaveStaffProfileSerializer)
from account.decorators import login_required


def _parse_json_list(s):
    try:
        v = _json.loads(s) if s else []
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _staff_profile_data(p):
    return {
        "zipcode": p.zipcode, "address": p.address, "address_detail": p.address_detail, "phone": p.phone,
        "resident_copy": p.resident_copy, "bankbook_copy": p.bankbook_copy,
        "graduation_cert": p.graduation_cert, "transcript": p.transcript,
        "family_relation_cert": p.family_relation_cert,
        "dependents_decided": p.dependents_decided,
        "dependents": _parse_json_list(p.dependents),
        "emergency_contacts": _parse_json_list(p.emergency_contacts),
        "sex_offense_consent": p.sex_offense_consent,
        "sex_offense_signature": p.sex_offense_signature,
        "sex_offense_date": str(p.sex_offense_date) if p.sex_offense_date else None,
        "file_uploaded_at": _parse_json_obj(p.file_uploaded_at),
        "completed": p.is_complete(),
        "missing": p.missing_items(),
        "waived_docs": sorted(p.waived_set()),
    }


def _parse_json_obj(s):
    try:
        v = _json.loads(s) if s else {}
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


# 직원 인사 정보 업로드 가능한 단일 파일 필드
STAFF_FILE_FIELDS = {"resident_copy", "bankbook_copy", "graduation_cert", "transcript", "family_relation_cert"}
STAFF_UPLOAD_SUFFIXES = [".gif", ".jpg", ".jpeg", ".bmp", ".png", ".pdf"]


def _is_staff_user(user):
    prof = getattr(user, "academy_profile", None)
    if prof and prof.role in STAFF_ROLES:
        return True
    return bool(user.is_admin_role())


class BranchListAPI(APIView):
    def get(self, request):
        """가입 폼 등에서 사용하는 활성 지점 목록(공개)."""
        branches = Branch.objects.filter(is_active=True)
        return self.success(BranchSerializer(branches, many=True).data)


class OptionListAPI(APIView):
    def get(self, request):
        """포털 폼에서 쓰는 선택 목록(활성). 카테고리별로 묶어서 반환(공개).
        상담 신청서는 비로그인 작성이라 공개 조회를 허용한다."""
        result = {}
        for opt in OptionItem.objects.filter(is_active=True).order_by("category", "order", "id"):
            result.setdefault(opt.category, []).append(
                {"value": opt.value, "label": opt.label, "allow_custom": opt.allow_custom,
                 "color": opt.color, "is_active": True})
        return self.success(result)


class AcademySignupAPI(APIView):
    @validate_serializer(AcademySignupSerializer)
    def post(self, request):
        """학생/학부모 가입 신청. 비활성 계정을 생성하고 승인 대기 상태로 둔다."""
        data = request.data
        username = data["username"].lower()
        email = (data.get("email") or "").lower() or None

        if User.objects.filter(username=username).exists():
            return self.error("Username already exists")
        if email and User.objects.filter(email=email).exists():
            return self.error("Email already exists")

        branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
        if not branch:
            return self.error("Invalid branch")

        with transaction.atomic():
            user = User.objects.create(username=username, email=email, is_disabled=True)
            user.set_password(data["password"])
            user.save()
            UserProfile.objects.create(user=user, real_name=data["real_name"])
            AcademyProfile.objects.create(user=user, role=data["role"], branch=branch)
            SignupRequest.objects.create(
                user=user,
                requested_role=data["role"],
                requested_branch=branch,
                applicant_name=data["real_name"],
                contact=data.get("contact", "") or "",
                memo=data.get("memo", "") or "",
            )
        return self.success("Signup request submitted")


class LeadCreateAPI(APIView):
    @validate_serializer(LeadCreateSerializer)
    def post(self, request):
        """상담 신청서 접수(무로그인). 계정은 생성하지 않는다(등록 전환 시 생성)."""
        data = request.data
        branch = Branch.objects.filter(id=data["branch_id"], is_active=True).first()
        if not branch:
            return self.error("Invalid branch")
        # 직원(로그인) 작성=선택사항. 무로그인(신청자 직접 작성)=전 항목 필수.
        is_staff = bool(getattr(request.user, "is_authenticated", False)) and _is_staff_user(request.user)
        if not is_staff:
            required = [("parent_name", "학부모 이름"), ("parent_phone", "학부모 연락처"),
                        ("student_name", "자녀 이름"), ("school_type", "학교 구분"),
                        ("school_name", "학교 이름"), ("grade", "학년"), ("purpose", "학생의 목표")]
            for f, label in required:
                if not (data.get(f) or "").strip():
                    return self.error(label + "을(를) 입력해 주세요.")
            if data.get("purpose") == "ETC" and not (data.get("purpose_detail") or "").strip():
                return self.error("학생의 목표를 직접 입력해 주세요.")
        # 상담한 때는 직원만 정할 수 있다. 신청자가 스스로 적는 화면에는 이 칸이 없다.
        counsel_at = None
        if is_staff:
            from .admin import _parse_kst_local_dt
            counsel_at = _parse_kst_local_dt(data.get("counsel_at"))
        Lead.objects.create(
            branch=branch,
            counsel_at=counsel_at,
            channel=((data.get("channel") or "VISIT") if is_staff else "VISIT"),
            parent_name=(data.get("parent_name") or "").strip(),
            parent_phone=(data.get("parent_phone") or "").strip(),
            student_name=(data.get("student_name") or "").strip(),
            school_type=data.get("school_type", "") or "",
            school_name=data.get("school_name", "") or "",
            grade=data.get("grade", "") or "",
            interest=data.get("interest", "") or "",
            purpose=data.get("purpose", "") or "",
            purpose_detail=data.get("purpose_detail", "") or "",
        )
        return self.success("상담 신청이 접수되었습니다.")


_ENROLL_LABELS = [
    ("student_name", "학생 성명"), ("birth_date", "생년월일"), ("gender", "성별"),
    ("student_phone", "학생 휴대폰"), ("parent_name", "보호자 이름"), ("parent_phone", "보호자 연락처"),
    ("parent_relation", "보호자 관계"), ("notify_optin", "등하원 알림"),
    ("guardian2_phone", "기타 보호자 휴대폰"), ("guardian2_relation", "기타 보호자 관계"),
    ("school_type", "학교 구분"), ("school_name", "학교"), ("grade", "학년"),
    ("zipcode", "우편번호"), ("address", "주소"), ("address_detail", "상세주소"),
    ("consent_guardian_name", "법정대리인 성명"), ("memo", "기타(요청·알림 사항)"),
    ("parent_password", "학부모 계정 비밀번호"),
]


def _enroll_disp(k, v):
    if k == "notify_optin":
        return "수신" if v else "미수신"
    if k == "gender":
        return {"M": "남", "F": "여"}.get(v, v or "")
    if k == "parent_password":
        return "(변경됨)" if v else "(없음)"  # 비밀번호 값 자체는 이력에 노출하지 않음
    return str(v) if v not in (None, "") else "(없음)"


def _enroll_diff(old, new):
    """학부모 재제출 시 변경 필드 diff. [{label, old, new}]."""
    out = []
    for k, label in _ENROLL_LABELS:
        ov, nv = old.get(k), new.get(k)
        if k == "parent_password":
            if (nv or "") and nv != ov:  # 새 비밀번호를 입력했을 때만 "변경됨" 기록
                out.append({"label": label, "old": "", "new": "(변경됨)"})
            continue
        same = (bool(ov) == bool(nv)) if k == "notify_optin" else ((ov or "") == (nv or ""))
        if not same:
            out.append({"label": label, "old": _enroll_disp(k, ov), "new": _enroll_disp(k, nv)})
    return out


class EnrollAPI(APIView):
    """등록 링크(무로그인). 상담 후 학부모가 인적사항·동의를 원격 작성.
    GET ?token= 로 프리필 조회, POST 로 제출(직원 검토 후 확정 생성). 7일 내 재수정 가능."""
    def get(self, request):
        token = (request.GET.get("token") or "").strip()
        lead = Lead.objects.select_related("branch").filter(enroll_token=token).first() if token else None
        if not lead:
            return self.error("링크가 올바르지 않습니다.")
        if lead.status == "CONVERTED":
            return self.error("이미 등록이 완료되었습니다. 수정이 필요하면 학원에 문의해 주세요.")
        if lead.enroll_token_expires and lead.enroll_token_expires < now():
            return self.error("링크가 만료되었습니다(7일 경과). 학원에 재발급을 요청해 주세요.")
        seed = {}
        if lead.enroll_data:
            try:
                seed = _json.loads(lead.enroll_data)
            except (ValueError, TypeError):
                seed = {}
        submitted = lead.enroll_status == "SUBMITTED"
        return self.success({
            "branch": (lead.branch.name if lead.branch_id else ""),
            "already_submitted": submitted,
            "expires": str((lead.enroll_token_expires + timedelta(hours=9)))[:16] if lead.enroll_token_expires else "",
            # 제출본이 있으면 그 값으로, 없으면 리드 기본값으로 프리필
            "student_name": seed.get("student_name") or lead.student_name,
            "parent_name": seed.get("parent_name") or lead.parent_name,
            "parent_phone": seed.get("parent_phone") or lead.parent_phone,
            "school_type": seed.get("school_type") or lead.school_type,
            "school_name": seed.get("school_name") or lead.school_name,
            "grade": seed.get("grade") or lead.grade,
            "gender": seed.get("gender", ""), "birth_date": seed.get("birth_date", ""),
            "parent_relation": seed.get("parent_relation", ""),
            "notify_optin": bool(seed.get("notify_optin", False)),
            "student_phone": seed.get("student_phone", ""),
            "zipcode": seed.get("zipcode", ""), "address": seed.get("address", ""),
            "address_detail": seed.get("address_detail", ""),
            "consent_guardian_name": seed.get("consent_guardian_name", ""),
            "guardian2_phone": seed.get("guardian2_phone", ""),
            "guardian2_relation": seed.get("guardian2_relation", ""),
            "memo": seed.get("memo", ""),
            "has_password": bool(seed.get("parent_password")),  # 이전 제출에서 비번을 설정했는지 여부만 표시(값은 미노출)
        })

    def post(self, request):
        data = request.data
        token = (data.get("token") or "").strip()
        lead = Lead.objects.filter(enroll_token=token).first() if token else None
        if not lead:
            return self.error("링크가 올바르지 않습니다.")
        if lead.status == "CONVERTED":
            return self.error("이미 등록이 완료되어 수정할 수 없습니다. 학원에 문의해 주세요.")
        if lead.enroll_token_expires and lead.enroll_token_expires < now():
            return self.error("링크가 만료되었습니다(7일 경과).")
        # 학부모 작성 항목(인적사항·주소·보호자·동의). 과정·시간표·계정은 직원이 확정.
        keep = ["student_name", "birth_date", "gender", "student_phone",
                "parent_name", "parent_phone", "parent_relation", "notify_optin",
                "guardian2_phone", "guardian2_relation",
                "school_type", "school_name", "grade",
                "zipcode", "address", "address_detail", "memo",
                "consent_privacy", "consent_guardian_name", "consent_signature"]
        payload = {k: data.get(k) for k in keep}
        already_submitted = lead.enroll_status == "SUBMITTED"
        old = {}
        if already_submitted and lead.enroll_data:
            try:
                old = _json.loads(lead.enroll_data)
            except (ValueError, TypeError):
                old = {}
        # 학부모 계정 아이디는 항상 보호자 연락처로 서버가 고정(부모가 임의 지정 불가)
        pw = (data.get("parent_password") or "").strip()
        if pw:
            if not (pw.isdigit() and len(pw) == 6):
                return self.error("학부모 계정 비밀번호는 숫자 6자리로 입력해 주세요.")
            payload["parent_password"] = pw
        else:
            if not already_submitted:
                return self.error("학부모 계정 비밀번호(숫자 6자리)를 설정해 주세요.")
            payload["parent_password"] = old.get("parent_password", "")  # 비워두면 기존 비번 유지
        if not payload.get("consent_privacy"):
            return self.error("개인정보 수집·이용에 동의해 주세요.")
        if not (payload.get("consent_guardian_name") or "").strip():
            return self.error("법정대리인 성명을 입력해 주세요.")
        # 재제출(수정)이면 변경 이력 기록 + '수정됨' 플래그(직원 확인용)
        if already_submitted:
            changes = _enroll_diff(old, payload)
            if changes:
                try:
                    log = _json.loads(lead.enroll_edit_log) if lead.enroll_edit_log else []
                except (ValueError, TypeError):
                    log = []
                log.append({"time": str(now() + timedelta(hours=9))[:16], "changes": changes})
                lead.enroll_edit_log = _json.dumps(log, ensure_ascii=False)
                lead.enroll_edited = True
        # 리드 기본 정보도 최신값으로 반영(직원 확정 화면 프리필용)
        for f in ("student_name", "parent_name", "parent_phone", "school_type", "school_name", "grade"):
            if payload.get(f):
                setattr(lead, f, payload.get(f))
        lead.enroll_data = _json.dumps(payload, ensure_ascii=False)
        lead.enroll_status = "SUBMITTED"
        lead.enroll_submitted_at = now()
        # 링크는 7일 만료 유지(재수정 가능). 즉시 만료하지 않음.
        lead.save()
        return self.success("제출되었습니다. 7일 이내에는 이 링크로 다시 수정할 수 있습니다.")


class StaffNameHintAPI(APIView):
    def get(self, request):
        """로그인 화면용: 사번(username)으로 직원 이름 힌트. 직원 계정만, 활성만."""
        from ..models import STAFF_ROLES
        sabun = (request.GET.get("sabun") or "").strip()
        if len(sabun) < 3:
            return self.success({"name": ""})
        prof = AcademyProfile.objects.select_related("user").filter(
            user__username=sabun, role__in=STAFF_ROLES, user__is_disabled=False).first()
        name = ""
        if prof:
            try:
                name = prof.user.userprofile.real_name or ""
            except Exception:
                name = ""
        return self.success({"name": name})


class MyAcademyProfileAPI(APIView):
    @login_required
    def get(self, request):
        """로그인 사용자의 학원 역할/소속 지점. 직원 화면 기본값(지점 prefill) 등에 사용."""
        from ..models import ACADEMY_ROLE_CHOICES
        profile = AcademyProfile.objects.select_related("branch").filter(user=request.user).first()
        if not profile:
            return self.success(None)
        role_label = dict(ACADEMY_ROLE_CHOICES).get(profile.role, profile.role)
        branch = None
        if profile.branch_id and profile.branch:
            branch = {"id": profile.branch.id, "code": profile.branch.code, "name": profile.branch.name}
        from ..services import editable_branch_ids, viewable_branch_ids, can_manage_staff
        edit_ids = editable_branch_ids(request.user)   # None=전체
        view_ids = viewable_branch_ids(request.user)
        vbranches = []
        if view_ids:
            vbranches = [{"id": b.id, "name": b.name}
                         for b in Branch.objects.filter(id__in=view_ids)]
        return self.success({
            "role": profile.role,
            "role_label": role_label,
            "branch": branch,
            "is_all_branch": profile.is_all_branch(),
            "is_staff_role": profile.is_staff_role(),
            "edit_all_branch": edit_ids is None,                 # 전지점 수정 가능(본부/super)
            "editable_branch_ids": edit_ids or [],               # 수정 가능 지점(빈 배열=수정 불가)
            "viewable_branch_ids": [] if view_ids is None else view_ids,
            "viewable_branches": vbranches,                      # 열람 지점 이름(헤더·표시용)
            "can_manage_staff": can_manage_staff(request.user),
        })


def _resolve_target(request):
    """조회 대상 사용자: 기본은 본인. 학부모가 student_id 로 자녀를 지정하면
    보호자 연결을 확인하고 그 자녀를 반환. 권한 없으면 None."""
    user = request.user
    student_id = request.GET.get("student_id")
    if not student_id:
        return user
    if str(student_id) == str(user.id):
        return user
    link = GuardianStudent.objects.filter(parent=user, student_id=student_id).select_related("student").first()
    if link:
        return link.student
    return None


class MyChildrenAPI(APIView):
    @login_required
    def get(self, request):
        """학부모 로그인 시 연결된 자녀 목록(11 §9 자녀 스위처용)."""
        links = GuardianStudent.objects.filter(parent=request.user).select_related("student")
        children = []
        for l in links:
            s = l.student
            real_name = ""
            try:
                real_name = s.userprofile.real_name or ""
            except Exception:
                real_name = ""
            children.append({"id": s.id, "username": s.username, "real_name": real_name,
                             "relation": l.relation})
        return self.success(children)


class GuardianMeAPI(APIView):
    @login_required
    def get(self, request):
        """학부모 본인 기본정보(상담 신청서 자동 채움용). 학부모가 아니면 null."""
        profile = AcademyProfile.objects.select_related("branch").filter(user=request.user).first()
        if not profile or profile.role != AcademyRole.PARENT:
            return self.success(None)
        real_name = ""
        try:
            real_name = request.user.userprofile.real_name or ""
        except Exception:
            real_name = ""
        branch = None
        if profile.branch_id and profile.branch:
            branch = {"id": profile.branch.id, "code": profile.branch.code, "name": profile.branch.name}
        return self.success({"parent_name": real_name, "parent_phone": profile.phone, "branch": branch})


class StaffProfileAPI(APIView):
    @login_required
    def get(self, request):
        """직원 본인 인사 정보 조회(+완료 여부). 직원이 아니면 null."""
        if not _is_staff_user(request.user):
            return self.success(None)
        p = StaffProfile.objects.filter(user=request.user).first()
        if not p:
            p = StaffProfile.objects.create(user=request.user)
        result = _staff_profile_data(p)
        # 본인 노출 문서(서류함) + 본인 수정 이력(항목·시각만)
        docs = StaffDocument.objects.filter(user=request.user, visible_to_staff=True)
        result["documents"] = [_doc_data(d) for d in docs]
        # 무엇이 어떻게 바뀌었고 누가 고쳤는지까지 본인도 본다.
        # 원장이 대신 고친 것도 여기 남아야 나중에 되짚을 수 있다.
        def _actor(h):
            if not h.actor_id:
                return ""
            try:
                return h.actor.userprofile.real_name or h.actor.username
            except Exception:
                return h.actor.username
        result["history"] = [{"field": h.field, "old": h.old_value, "new": h.new_value,
                              "actor": ("" if h.actor_id == request.user.id else _actor(h)),
                              "reason": h.reason, "time": _kst_dt_str(h.create_time)}
                             for h in StaffProfileHistory.objects.filter(user=request.user)
                                                                 .select_related("actor")[:100]]
        return self.success(result)

    @validate_serializer(SaveStaffProfileSerializer)
    @login_required
    def post(self, request):
        """직원 본인 인사 정보 저장(주소·연락처 필수, 피부양자/비상연락망/동의)."""
        if not _is_staff_user(request.user):
            return self.error("직원만 사용할 수 있습니다.")
        data = request.data
        p, _ = StaffProfile.objects.get_or_create(user=request.user)
        before = {f: getattr(p, f) for f in TRACKED_HR_FIELDS}
        old_deps = p.dependents or ""
        old_decided = p.dependents_decided
        p.zipcode = data.get("zipcode", "") or ""
        p.address = data["address"]
        p.address_detail = data.get("address_detail", "") or ""
        p.phone = data["phone"]
        p.dependents_decided = bool(data.get("dependents_decided"))
        if "dependents" in data:
            p.dependents = data.get("dependents") or ""
        if "emergency_contacts" in data:
            p.emergency_contacts = data.get("emergency_contacts") or ""
        p.sex_offense_consent = bool(data.get("sex_offense_consent"))
        if data.get("sex_offense_signature"):
            p.sex_offense_signature = data["sex_offense_signature"]
            p.sex_offense_date = data.get("sex_offense_date") or now().date()
        p.save()

        # 변경 이력 기록(누가·항목·전→후)
        after = {f: getattr(p, f) for f in TRACKED_HR_FIELDS}
        record_hr_history(request.user, request.user, before, after)

        # 4대보험 피부양자 변경 시 관리자에게 통보(쪽지)
        dependents_changed = (p.dependents or "") != old_deps or p.dependents_decided != old_decided
        if dependents_changed:
            prof = getattr(request.user, "academy_profile", None)
            real_name = ""
            try:
                real_name = request.user.userprofile.real_name or ""
            except Exception:
                real_name = ""
            HRNotice.objects.create(
                staff=request.user, branch=(prof.branch if prof else None),
                kind="DEPENDENTS",
                message=f"{real_name or request.user.username} 직원이 4대보험 피부양자 정보를 수정했습니다.")

        result = _staff_profile_data(p)
        result["dependents_changed"] = dependents_changed
        return self.success(result)


class StaffProfileUploadAPI(APIView):
    request_parsers = ()

    @login_required
    def post(self, request):
        """인사 서류 파일 업로드(등본/통장사본/졸업·성적증명서, 피부양자 가족관계증명서).
        field=필드명, file=파일. 피부양자 증명서는 field=family_cert & index=n."""
        if not _is_staff_user(request.user):
            return self.error("직원만 사용할 수 있습니다.")
        field = request.POST.get("field", "")
        f = request.FILES.get("file")
        if not f:
            return self.error("파일이 없습니다.")
        if f.size > 8 * 1024 * 1024:
            return self.error("파일이 너무 큽니다(최대 8MB).")
        suffix = os.path.splitext(f.name)[-1].lower()
        if suffix not in STAFF_UPLOAD_SUFFIXES:
            return self.error("이미지(jpg/png 등) 또는 PDF만 업로드할 수 있습니다.")
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        name = "hr_" + rand_str(16) + suffix
        with open(os.path.join(settings.UPLOAD_DIR, name), "wb") as out:
            for chunk in f:
                out.write(chunk)
        url = f"{settings.UPLOAD_PREFIX}/{name}"

        p, _ = StaffProfile.objects.get_or_create(user=request.user)
        if field in STAFF_FILE_FIELDS:
            setattr(p, field, url)
            ts = _parse_json_obj(p.file_uploaded_at)
            ts[field] = str(now() + timedelta(hours=9))[:16]  # KST(UTC+9) 표시
            p.file_uploaded_at = _json.dumps(ts, ensure_ascii=False)
            p.save()
        elif field == "family_cert":
            try:
                idx = int(request.POST.get("index"))
            except (TypeError, ValueError):
                return self.error("피부양자 번호가 올바르지 않습니다.")
            deps = _parse_json_list(p.dependents)
            if not (0 <= idx < len(deps)):
                return self.error("피부양자 목록을 먼저 저장하세요.")
            deps[idx]["family_cert"] = url
            p.dependents = _json.dumps(deps, ensure_ascii=False)
            p.save()
        else:
            return self.error("알 수 없는 필드입니다.")
        return self.success({"field": field, "url": url, "completed": p.is_complete()})


class ChangePasswordAPI(APIView):
    @login_required
    def post(self, request):
        """본인 비밀번호 변경. 변경 후에도 로그인 유지(세션 auth 해시 갱신)."""
        from django.contrib import auth
        from django.contrib.auth import update_session_auth_hash
        old = request.data.get("old_password", "")
        new = request.data.get("new_password", "")
        if len(new) < 6:
            return self.error("새 비밀번호는 6자 이상이어야 합니다.")
        u = auth.authenticate(username=request.user.username, password=old)
        if not u:
            return self.error("현재 비밀번호가 올바르지 않습니다.")
        request.user.set_password(new)
        request.user.save()
        update_session_auth_hash(request, request.user)
        return self.success("변경되었습니다.")


class MySignupStatusAPI(APIView):
    @login_required
    def get(self, request):
        """본인 가입 신청 상태 조회."""
        req = SignupRequest.objects.filter(user=request.user).first()
        if not req:
            return self.success(None)
        return self.success(SignupRequestSerializer(req).data)


class MyTimetableAPI(APIView):
    @login_required
    def get(self, request):
        """내 시간표: 개별 수업 시간표 + 그룹(특강) 반.
        학생=본인 개별 슬롯/수강 반, 강사=담당 개별 슬롯/담당 반.
        학부모는 student_id 로 자녀 시간표 조회."""
        user = _resolve_target(request)
        if user is None:
            return self.error("권한이 없습니다.")
        # 개별 수업 시간표(학생 본인 또는 담당 강사). 종료/휴원중지(ENDED/PAUSED)는 제외.
        individual = StudentTimetable.objects.select_related(
            "student", "branch", "instructor").exclude(status__in=["ENDED", "PAUSED"]).filter(
            Q(student=user) | Q(instructor=user))
        # 그룹/특강 반: 담당 강사 + 수강 중(중복 제거)
        teaching = CourseClass.objects.filter(instructor=user, is_active=True)
        enrolled = CourseClass.objects.filter(
            enrollments__student=user, enrollments__is_active=True, is_active=True)
        classes = (teaching | enrolled).distinct().select_related("branch", "instructor")
        return self.success({
            "individual": StudentTimetableSerializer(individual, many=True).data,
            "groups": CourseClassSerializer(classes, many=True).data,
        })


class MyAttendanceAPI(APIView):
    @login_required
    def get(self, request):
        """본인(또는 학부모의 자녀) 출결 내역(최근순). class_id 로 특정 반 필터 가능."""
        target = _resolve_target(request)
        if target is None:
            return self.error("권한이 없습니다.")
        qs = AttendanceRecord.objects.filter(student=target).select_related(
            "session", "session__course_class", "session__course_class__branch")
        class_id = request.GET.get("class_id")
        if class_id:
            qs = qs.filter(session__course_class_id=class_id)
        qs = qs.order_by("-session__date")
        results = []
        for r in qs[:200]:
            results.append({
                "session": ClassSessionSerializer(r.session).data,
                "status": r.status,
                "memo": r.memo,
            })
        return self.success(results)


# ── 개발 요청 게시판(로그인한 모든 사용자) ──

def _u_name(u):
    if not u:
        return ""
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username


def _is_admin(u):
    return bool(u and u.is_authenticated and u.is_admin_role())


def _is_hq(u):
    """본부 관리자 이상(슈퍼 관리자 또는 academy 역할 HQ_ADMIN)."""
    if not (u and u.is_authenticated):
        return False
    if u.is_super_admin():
        return True
    prof = getattr(u, "academy_profile", None)
    return bool(prof and prof.role == AcademyRole.HQ_ADMIN)


_DEV_STATUSES = (DevRequest.NONE, DevRequest.REVIEWING, DevRequest.IN_PROGRESS,
                 DevRequest.CONFIRMED, DevRequest.DONE)
_DEV_LABEL = {"NONE": "접수", "CONFIRMED": "확인", "REVIEWING": "검토중",
              "IN_PROGRESS": "개발중", "DONE": "완료"}


def _notify(recipient_id, actor, kind, text, link_type="", link_id=None):
    """알림 적립. 받는 사람이 없거나 본인 행동이면 생략."""
    if not recipient_id or (actor and actor.id == recipient_id):
        return
    Notification.objects.create(recipient_id=recipient_id, actor=actor, kind=kind,
                                text=text, link_type=link_type, link_id=link_id)


class NotificationAPI(APIView):
    @login_required
    def get(self, request):
        """내 알림 목록 + 안 읽은 수."""
        qs = request.user.notifications.select_related("actor")[:50]
        items = [{"id": n.id, "kind": n.kind, "text": n.text, "actor": _u_name(n.actor),
                  "link_type": n.link_type, "link_id": n.link_id, "is_read": n.is_read,
                  "time": _kst_dt_str(n.create_time)} for n in qs]
        unread = request.user.notifications.filter(is_read=False).count()
        return self.success({"unread": unread, "items": items})

    @login_required
    def post(self, request):
        """읽음 처리. {id} 또는 {all:true}."""
        if request.data.get("all"):
            request.user.notifications.filter(is_read=False).update(is_read=True)
        elif request.data.get("id"):
            request.user.notifications.filter(id=request.data.get("id")).update(is_read=True)
        return self.success(request.user.notifications.filter(is_read=False).count())


class MessageAPI(APIView):
    @login_required
    def get(self, request):
        """쪽지함. ?box=inbox(기본)|sent. 받은함이면 안읽음 수도 반환."""
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        me = request.user
        box = request.GET.get("box") or "inbox"
        if box == "sent":
            qs = Message.objects.select_related("recipient").filter(sender=me, sender_hidden=False)
            items = [{"id": m.id, "other": _u_name(m.recipient), "body": m.body,
                      "is_read": m.is_read, "time": _kst_dt_str(m.create_time), "mine": True} for m in qs[:200]]
        else:
            qs = Message.objects.select_related("sender").filter(recipient=me, recipient_hidden=False)
            items = [{"id": m.id, "other": _u_name(m.sender), "body": m.body,
                      "is_read": m.is_read, "time": _kst_dt_str(m.create_time), "mine": False} for m in qs[:200]]
        unread = Message.objects.filter(recipient=me, recipient_hidden=False, is_read=False).count()
        return self.success({"unread": unread, "items": items})

    @login_required
    def post(self, request):
        """쪽지 보내기. {recipient_id, body}."""
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        rid = request.data.get("recipient_id")
        body = (request.data.get("body") or "").strip()
        if not rid or not body:
            return self.error("받는 사람과 내용을 입력하세요.")
        target = User.objects.filter(id=rid).first()
        if not target or not _is_staff_user(target):
            return self.error("받는 사람을 찾을 수 없습니다.")
        Message.objects.create(sender=request.user, recipient=target, body=body)
        _notify(target.id, request.user, Notification.MESSAGE,
                "%s님이 쪽지를 보냈습니다." % _u_name(request.user), "message", None)
        return self.success(True)

    @login_required
    def put(self, request):
        """받은 쪽지 읽음 처리. {id}."""
        m = Message.objects.filter(id=request.data.get("id"), recipient=request.user).first()
        if m and not m.is_read:
            m.is_read = True
            m.save(update_fields=["is_read"])
        return self.success(True)

    @login_required
    def delete(self, request):
        """쪽지 소프트삭제(내 쪽에서만 숨김)."""
        m = Message.objects.filter(id=request.GET.get("id")).first()
        if not m:
            return self.error("쪽지가 없습니다.")
        if m.sender_id == request.user.id:
            m.sender_hidden = True
            m.save(update_fields=["sender_hidden"])
        elif m.recipient_id == request.user.id:
            m.recipient_hidden = True
            m.save(update_fields=["recipient_hidden"])
        else:
            return self.error("권한이 없습니다.")
        return self.success(True)


class DevRequestAPI(APIView):
    @login_required
    def get(self, request):
        """직원 전용 게시판. 목록(?없음) 또는 상세(?id=). 삭제(숨김) 항목은 본부 관리자만."""
        me = request.user
        if not _is_staff_user(me):
            return self.error("권한이 없습니다.")
        hq = _is_hq(me)
        rid = request.GET.get("id")
        if rid:
            o = DevRequest.objects.select_related("author").filter(id=rid).first()
            if not o or (o.is_hidden and not hq):
                return self.error("글이 없습니다.")
            comments = []
            for c in o.comments.select_related("author").all():
                if c.is_hidden and not hq:
                    continue
                comments.append({"id": c.id, "author": _u_name(c.author), "author_id": c.author_id,
                                 "body": c.body, "is_hidden": c.is_hidden,
                                 "time": _kst_dt_str(c.create_time), "mine": c.author_id == me.id})
            return self.success({"id": o.id, "title": o.title, "body": o.body, "status": o.status,
                                 "author": _u_name(o.author), "author_id": o.author_id,
                                 "time": _kst_dt_str(o.create_time), "is_hidden": o.is_hidden,
                                 "mine": o.author_id == me.id, "can_status": hq, "comments": comments})
        from django.db.models import Count
        qs = DevRequest.objects.select_related("author").all()
        if not hq:
            qs = qs.filter(is_hidden=False)
        cc = dict(DevRequestComment.objects.filter(is_hidden=False).values("request_id")
                  .annotate(c=Count("id")).values_list("request_id", "c"))
        out = [{"id": o.id, "title": o.title, "status": o.status, "author": _u_name(o.author),
                "time": _kst_dt_str(o.create_time), "is_hidden": o.is_hidden,
                "mine": o.author_id == me.id, "comments": cc.get(o.id, 0)} for o in qs[:300]]
        return self.success(out)

    @login_required
    def post(self, request):
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        title = (request.data.get("title") or "").strip()
        if not title:
            return self.error("제목을 입력하세요.")
        o = DevRequest.objects.create(author=request.user, title=title[:200],
                                      body=(request.data.get("body") or ""))
        return self.success({"id": o.id})

    @login_required
    def put(self, request):
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        o = DevRequest.objects.filter(id=request.data.get("id")).first()
        if not o:
            return self.error("글이 없습니다.")
        hq = _is_hq(request.user)
        is_owner = (o.author_id == request.user.id)
        # 상태 변경은 본부 관리자만
        if "status" in request.data:
            if not hq:
                return self.error("상태 변경은 본부 관리자만 가능합니다.")
            st = request.data.get("status")
            if st in _DEV_STATUSES and st != o.status:
                o.status = st
                _notify(o.author_id, request.user, Notification.STATUS,
                        "내 요청 '%s' 상태가 '%s'(으)로 변경되었습니다." % (o.title[:30], _DEV_LABEL.get(st, st)),
                        "dev", o.id)
        # 제목/본문 수정은 작성자 또는 본부 관리자
        if "title" in request.data or "body" in request.data:
            if not (is_owner or hq):
                return self.error("수정 권한이 없습니다.")
            if "title" in request.data:
                t = (request.data.get("title") or "").strip()
                if t:
                    o.title = t[:200]
            if "body" in request.data:
                o.body = request.data.get("body") or ""
        o.save()
        return self.success({"id": o.id, "status": o.status})

    @login_required
    def delete(self, request):
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        o = DevRequest.objects.filter(id=request.GET.get("id")).first()
        if not o:
            return self.error("글이 없습니다.")
        if o.author_id != request.user.id and not _is_hq(request.user):
            return self.error("권한이 없습니다.")
        o.is_hidden = True
        o.save(update_fields=["is_hidden"])
        return self.success(True)


class DevCommentAPI(APIView):
    @login_required
    def post(self, request):
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        o = DevRequest.objects.filter(id=request.data.get("request_id")).first()
        if not o:
            return self.error("글이 없습니다.")
        body = (request.data.get("body") or "").strip()
        if not body:
            return self.error("내용을 입력하세요.")
        DevRequestComment.objects.create(request=o, author=request.user, body=body)
        _notify(o.author_id, request.user, Notification.COMMENT,
                "내 요청 '%s'에 덧글이 달렸습니다." % o.title[:30], "dev", o.id)
        return self.success(True)

    @login_required
    def delete(self, request):
        if not _is_staff_user(request.user):
            return self.error("권한이 없습니다.")
        c = DevRequestComment.objects.filter(id=request.GET.get("id")).first()
        if not c:
            return self.error("덧글이 없습니다.")
        if c.author_id != request.user.id and not _is_hq(request.user):
            return self.error("권한이 없습니다.")
        c.is_hidden = True
        c.save(update_fields=["is_hidden"])
        return self.success(True)


def _kiosk_device_error(branch, device_id):
    """승인된 기기(브라우저)인지 확인. 문제 없으면 None, 아니면 에러 메시지."""
    device_id = (device_id or "").strip()
    if not device_id:
        return "기기 등록이 필요합니다."
    d = KioskDevice.objects.filter(branch=branch, device_id=device_id).first()
    if not d or d.status == KioskDeviceStatus.PENDING:
        return "관리자 승인 대기 중입니다."
    if d.status == KioskDeviceStatus.REVOKED:
        return "이 기기는 사용이 취소되었습니다. 관리자에게 문의하세요."
    return None


class KioskDeviceRequestAPI(CSRFExemptAPIView):
    """무로그인 출결 키오스크: 새 기기(브라우저) 등록 요청/상태 확인. 요청할 때마다 최신 상태를
    돌려주므로 키오스크 화면에서 주기적으로 이 API를 폴링해 승인 여부를 확인한다.
    POST {b:지점id, t:키오스크토큰, device_id, user_agent?, label?(최초 등록 신청 시 사용자가 입력한 기기 이름)}"""
    def post(self, request):
        data = request.data
        branch = Branch.objects.filter(id=data.get("b")).first()
        token = (data.get("t") or "").strip()
        if not branch or not branch.kiosk_token or branch.kiosk_token != token:
            return self.error("잘못된 접근입니다.")
        device_id = (data.get("device_id") or "").strip()
        if not device_id:
            return self.error("기기 식별자가 없습니다.")
        d, _created = KioskDevice.objects.get_or_create(
            branch=branch, device_id=device_id,
            defaults={"user_agent": (data.get("user_agent") or "")[:255],
                      "label": (data.get("label") or "").strip()[:64]})
        return self.success({"status": d.status})


class KioskBoardAPI(APIView):
    """무로그인 출결 키오스크 좌측 현황판: 오늘 지점 수업 시각·학생·등원/하원만(민감정보 제외).
    GET ?b=지점id&t=키오스크토큰"""
    def get(self, request):
        branch = Branch.objects.filter(id=request.GET.get("b")).first()
        token = (request.GET.get("t") or "").strip()
        if not branch or not branch.kiosk_token or branch.kiosk_token != token:
            return self.error("잘못된 접근입니다.")
        dev_err = _kiosk_device_error(branch, request.GET.get("device_id"))
        if dev_err:
            return self.error(dev_err)
        d = _kst_today()
        ensure_occurrences(d, [branch.id])
        occ = LessonOccurrence.objects.select_related("student", "student__userprofile", "makeup_for").filter(
            date=d, branch_id=branch.id).exclude(status=OccurrenceStatus.CANCELLED).order_by("start_time", "id")
        sids = [o.student_id for o in occ]
        att = {}
        for a in DailyAttendance.objects.filter(date=d, student_id__in=sids):
            att[a.student_id] = {"in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at)}
        rows = [{"occ_id": o.id, "start_time": str(o.start_time)[:5], "student_name": _name_of(o.student),
                "duration_minutes": o.duration_minutes, "status": o.status, "is_makeup": o.is_makeup,
                "no_makeup": o.no_makeup, "student_id": o.student_id,
                "_absence_date": (str(o.makeup_for.date) if (o.is_makeup and o.makeup_for_id and o.makeup_for) else ""),
                "_absence_time": (str(o.makeup_for.start_time)[:5] if (o.is_makeup and o.makeup_for_id and o.makeup_for) else ""),
                "att": att.get(o.student_id, {"in": "", "out": ""}), "adhoc": False} for o in occ]
        for r in _adhoc_lesson_rows(d, [branch.id]):
            rows.append({"occ_id": None, "start_time": r["start_time"], "student_name": r["student_name"],
                        "duration_minutes": r["duration_minutes"], "att": r["att"], "adhoc": True,
                        "status": "SCHEDULED", "is_makeup": False, "no_makeup": False, "student_id": None,
                        "_absence_date": "", "_absence_time": ""})
        # 결석↔보강 상호 연결 표시(포털과 동일한 규칙)
        absent_ids = [r["occ_id"] for r in rows if r["status"] == OccurrenceStatus.ABSENT and r["occ_id"]]
        makeup_of = {m.makeup_for_id: m for m in LessonOccurrence.objects.filter(
            is_makeup=True, makeup_for_id__in=absent_ids)} if absent_ids else {}
        mk_keys = {(m.student_id, m.date) for m in makeup_of.values()}
        mk_att = {}
        if mk_keys:
            for a in DailyAttendance.objects.filter(
                    student_id__in={sid for sid, _ in mk_keys}, date__in={dt for _, dt in mk_keys}):
                if (a.student_id, a.date) in mk_keys:
                    mk_att[(a.student_id, a.date)] = a
        for r in rows:
            r["linked"] = None
            if r["status"] == OccurrenceStatus.ABSENT:
                mk = makeup_of.get(r["occ_id"])
                if mk:
                    a = mk_att.get((mk.student_id, mk.date))
                    done = bool(a and a.check_in_at and a.check_out_at)
                    r["linked"] = {"kind": "makeup", "date": str(mk.date), "start_time": str(mk.start_time)[:5],
                                   "status": mk.status, "done": done}
            elif r["_absence_date"]:
                r["linked"] = {"kind": "absence", "date": r["_absence_date"], "start_time": r["_absence_time"]}
            del r["_absence_date"], r["_absence_time"], r["occ_id"]
        rows.sort(key=lambda r: r["start_time"])
        return self.success({"rows": rows})


class KioskPinEntryAPI(CSRFExemptAPIView):
    """무로그인 포털 첫 화면: 키오스크 PIN(6자리)로 지점 확인 후 접속 정보 반환.
    아무나 지점을 눌러 기기 등록 신청을 남발하는 걸 막는 1차 관문(실제 조회·체크는
    여전히 관리자의 기기 승인이 있어야 가능). POST {pin}"""
    def post(self, request):
        pin = "".join(ch for ch in (request.data.get("pin") or "") if ch.isdigit())
        if len(pin) != 6:
            return self.error("6자리 번호를 입력하세요.")
        branch = Branch.objects.filter(kiosk_pin=pin, is_active=True).first()
        if not branch:
            return self.error("등록되지 않은 번호입니다. 관리자에게 확인하세요.")
        if not branch.kiosk_token:
            branch.kiosk_token = rand_str(24)
            branch.save(update_fields=["kiosk_token"])
        return self.success({"b": branch.id, "t": branch.kiosk_token, "branch_name": branch.name})


class KioskLookupAPI(APIView):
    """무로그인 출결 키오스크(학원 입구 태블릿): 보호자 연락처 뒤 4자리로 학생 조회.
    GET ?b=지점id&t=키오스크토큰&last4=1234"""
    def get(self, request):
        branch = Branch.objects.filter(id=request.GET.get("b")).first()
        token = (request.GET.get("t") or "").strip()
        if not branch or not branch.kiosk_token or branch.kiosk_token != token:
            return self.error("잘못된 접근입니다.")
        dev_err = _kiosk_device_error(branch, request.GET.get("device_id"))
        if dev_err:
            return self.error(dev_err)
        last4 = "".join(ch for ch in (request.GET.get("last4") or "") if ch.isdigit())
        if len(last4) != 4:
            return self.error("전화번호 뒤 4자리를 입력하세요.")
        matches = []
        sps = StudentProfile.objects.filter(enrollment_status=EnrollmentStatus.ENROLLED)\
            .select_related("user", "user__userprofile")
        for sp in sps:
            ap = getattr(sp.user, "academy_profile", None)
            if not ap or ap.branch_id != branch.id:
                continue
            phones = [sp.parent_phone, sp.guardian2_phone]
            if any(_norm_phone(p)[-4:] == last4 for p in phones if p):
                school = " ".join(x for x in [sp.school_name, (sp.grade + "학년" if sp.grade else "")] if x)
                matches.append({"student_id": sp.user_id, "name": _name_of(sp.user), "school": school})
        return self.success({"matches": matches})


class KioskStaffLookupAPI(CSRFExemptAPIView):
    """무로그인 키오스크 직원 출퇴근 1단계: 사번으로 직원 조회.
    습관적으로 눌러 남의 것을 찍는 사고를 막기 위해, 조회 후 이름을 한 번 더 고르게 한다.
    본인 지점 직원만 보이되, 타 지점 출강이 허용된 직원과 전지점 소속(본부·지부장)은 예외.
    POST {b, t, staff_no}"""
    def post(self, request):
        from ..views.hr import kst_today, kst_hm, name_of as _hr_name
        from ..models import StaffAttendance, StaffLeave, ALL_BRANCH_ROLES
        data = request.data
        branch = Branch.objects.filter(id=data.get("b")).first()
        token = (data.get("t") or "").strip()
        if not branch or not branch.kiosk_token or branch.kiosk_token != token:
            return self.error("잘못된 접근입니다.")
        dev_err = _kiosk_device_error(branch, data.get("device_id"))
        if dev_err:
            return self.error(dev_err)
        sabun = "".join(ch for ch in (data.get("staff_no") or "") if ch.isdigit())
        if len(sabun) < 4:
            return self.error("사번을 입력하세요.")
        d = kst_today()
        out = []
        for p in AcademyProfile.objects.filter(staff_no=sabun, role__in=STAFF_ROLES,
                                               is_deleted=False).select_related("user", "user__userprofile"):
            # 전지점 역할은 어느 지점에서나 찍을 수 있고, 그 외는 소속 지점에서만.
            if p.role not in ALL_BRANCH_ROLES and p.branch_id != branch.id:
                continue
            a = StaffAttendance.objects.filter(staff_id=p.user_id, date=d).first()
            lv = StaffLeave.objects.filter(staff_id=p.user_id, date=d, is_deleted=False).first()
            if a and a.check_in_at and a.check_out_at:
                nxt = "done"
            elif a and a.check_in_at:
                nxt = "out"
            else:
                nxt = "in"
            out.append({"staff_id": p.user_id, "name": _hr_name(p.user), "staff_no": p.staff_no,
                        "next": nxt, "in": kst_hm(a.check_in_at) if a else "",
                        "out": kst_hm(a.check_out_at) if a else "",
                        "on_leave": bool(lv)})
        if not out:
            return self.error("등록된 사번이 아닙니다. 관리자에게 확인하세요.")
        return self.success({"matches": out})


class KioskStaffCheckAPI(CSRFExemptAPIView):
    """무로그인 키오스크 직원 출퇴근 2단계: 이름을 고르면 출근/퇴근 기록.
    POST {b, t, staff_id, action: IN|OUT|UNDO}"""
    def post(self, request):
        from ..views.hr import staff_check
        data = request.data
        branch = Branch.objects.filter(id=data.get("b")).first()
        token = (data.get("t") or "").strip()
        if not branch or not branch.kiosk_token or branch.kiosk_token != token:
            return self.error("잘못된 접근입니다.")
        dev_err = _kiosk_device_error(branch, data.get("device_id"))
        if dev_err:
            return self.error(dev_err)
        u = User.objects.filter(id=data.get("staff_id")).first()
        p = getattr(u, "academy_profile", None) if u else None
        if not u or not p or p.role not in STAFF_ROLES or p.is_deleted:
            return self.error("직원을 찾을 수 없습니다.")
        from ..models import ALL_BRANCH_ROLES
        if p.role not in ALL_BRANCH_ROLES and p.branch_id != branch.id:
            return self.error("이 지점에서 찍을 수 없는 직원입니다.")
        act = (data.get("action") or "").upper()
        # 키오스크는 무로그인이라 actor 를 남길 수 없다. 찍은 사람이 곧 본인이므로 본인으로 기록.
        res = staff_check(u, act, "KIOSK", branch.id, u)
        if isinstance(res, dict) and "__ok" in res:
            return self.success(res["data"]) if res["__ok"] else self.error(res["msg"])
        return res


class KioskCheckAPI(CSRFExemptAPIView):
    """무로그인 출결 키오스크: 학생 선택 시 등원/하원 자동 판별 처리.
    POST {b:지점id, t:키오스크토큰, student_id}"""
    def post(self, request):
        data = request.data
        branch = Branch.objects.filter(id=data.get("b")).first()
        token = (data.get("t") or "").strip()
        if not branch or not branch.kiosk_token or branch.kiosk_token != token:
            return self.error("잘못된 접근입니다.")
        dev_err = _kiosk_device_error(branch, data.get("device_id"))
        if dev_err:
            return self.error(dev_err)
        u = User.objects.filter(id=data.get("student_id")).first()
        if not u:
            return self.error("학생을 찾을 수 없습니다.")
        ap = getattr(u, "academy_profile", None)
        if not ap or ap.branch_id != branch.id:
            return self.error("잘못된 접근입니다.")
        d = _kst_today()
        a, _created = DailyAttendance.objects.get_or_create(student=u, date=d, defaults={"branch": branch})
        name = _name_of(u)
        # 발생 경로 이력: 어느 키오스크 기기에서 눌렀는지 기록(수동 체크와 구분)
        dev = KioskDevice.objects.filter(branch=branch, device_id=(data.get("device_id") or "").strip()).first()
        dev_label = (dev.label if dev and dev.label else (dev.device_id[:8] if dev else "?"))
        if not a.check_in_at:
            a.check_in_at = now()
            a.save(update_fields=["check_in_at"])
            AttendanceChange.objects.create(attendance=a, actor=None,
                detail="등원 체크 %s (키오스크: %s)" % (_hm_kst(a.check_in_at), dev_label))
            return self.success({"action": "in", "name": name, "time": _hm_kst(a.check_in_at)})
        if not a.check_out_at:
            a.check_out_at = now()
            a.save(update_fields=["check_out_at"])
            AttendanceChange.objects.create(attendance=a, actor=None,
                detail="하원 체크 %s (키오스크: %s)" % (_hm_kst(a.check_out_at), dev_label))
            return self.success({"action": "out", "name": name, "time": _hm_kst(a.check_out_at)})
        return self.success({"action": "done", "name": name,
                             "in": _hm_kst(a.check_in_at), "out": _hm_kst(a.check_out_at)})
