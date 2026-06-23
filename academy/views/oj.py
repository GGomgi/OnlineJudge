import os
import json as _json
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now

from utils.api import APIView, validate_serializer
from utils.shortcuts import rand_str

from account.models import User, UserProfile
from ..models import (AcademyProfile, AcademyRole, Branch, SignupRequest, CourseClass,
                      AttendanceRecord, Lead, OptionItem)
from ..models import (StudentTimetable, GuardianStudent, StaffProfile, STAFF_ROLES,
                      HRNotice, StaffDocument, StaffProfileHistory)

TRACKED_HR_FIELDS = ["zipcode", "address", "address_detail", "phone",
                     "dependents_decided", "dependents", "emergency_contacts",
                     "sex_offense_consent"]


def _doc_data(d):
    return {"id": d.id, "group": d.group, "title": d.title, "url": d.url,
            "doc_date": str(d.doc_date) if d.doc_date else None,
            "uploaded_at": str(d.create_time)[:16], "order": d.order,
            "visible_to_staff": d.visible_to_staff}


def record_hr_history(staff_user, actor, before, after):
    rows = []
    for f in TRACKED_HR_FIELDS:
        o = str(before.get(f, "")); n = str(after.get(f, ""))
        if o != n:
            rows.append(StaffProfileHistory(user=staff_user, actor=actor, field=f,
                                            old_value=o, new_value=n))
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
                {"value": opt.value, "label": opt.label, "allow_custom": opt.allow_custom})
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
        Lead.objects.create(
            branch=branch,
            parent_name=data["parent_name"],
            parent_phone=data["parent_phone"],
            student_name=data["student_name"],
            school_type=data.get("school_type", "") or "",
            school_name=data.get("school_name", "") or "",
            grade=data.get("grade", "") or "",
            interest=data.get("interest", "") or "",
            purpose=data.get("purpose", "") or "",
            purpose_detail=data.get("purpose_detail", "") or "",
        )
        return self.success("상담 신청이 접수되었습니다.")


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
        result["history"] = [{"field": h.field, "time": str(h.create_time)[:16]}
                             for h in StaffProfileHistory.objects.filter(user=request.user)[:100]]
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
            ts[field] = str(now())[:16]
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
        # 개별 수업 시간표(학생 본인 또는 담당 강사)
        individual = StudentTimetable.objects.select_related(
            "student", "branch", "instructor").exclude(status="ENDED").filter(
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
