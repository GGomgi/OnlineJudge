"""알림톡 보내기 — 솔라피 어댑터와 보내기 서비스(docs/84).

중계사를 갈아 끼울 때 이 파일 하나만 고치면 되게 어댑터를 한 겹 둔다.

보내는 것보다 **남기는 것**이 먼저다. 보내지 않기로 한 것도 까닭과 함께 남긴다 —
조용히 넘어가면 왜 안 갔는지 아무도 모른다.
"""
import hashlib
import hmac
import json as _json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone as _tz

from django.utils.timezone import now

from .models import (NotifyLog, NotifyTemplate, NotifyKind, NotifyStatus,
                     StudentProfile, AcademyProfile, EnrollmentStatus)

API = "https://api.solapi.com"
TIMEOUT = 15


def _env(k, d=""):
    return (os.environ.get(k) or d).strip()


def configured():
    return bool(_env("SOLAPI_API_KEY") and _env("SOLAPI_API_SECRET") and _env("SOLAPI_PFID"))


def _auth_header():
    key, sec = _env("SOLAPI_API_KEY"), _env("SOLAPI_API_SECRET")
    date = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = uuid.uuid4().hex
    sig = hmac.new(sec.encode(), (date + salt).encode(), hashlib.sha256).hexdigest()
    return "HMAC-SHA256 apiKey=%s, date=%s, salt=%s, signature=%s" % (key, date, salt, sig)


def _call(path, body=None, method="GET"):
    req = urllib.request.Request(
        API + path, method=method,
        data=(_json.dumps(body).encode() if body is not None else None),
        headers={"Authorization": _auth_header(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return True, _json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return False, _json.loads(e.read().decode() or "{}")
        except Exception:
            return False, {"errorMessage": "HTTP %s" % e.code}
    except Exception as e:
        return False, {"errorMessage": "%s: %s" % (type(e).__name__, e)}


def balance():
    ok, d = _call("/cash/v1/balance")
    if not ok:
        return None
    return {"point": d.get("point", 0), "balance": d.get("balance", 0)}


def norm_phone(p):
    d = re.sub(r"[^0-9]", "", str(p or ""))
    return d if d.startswith("01") and 10 <= len(d) <= 11 else ""


def render(body, variables):
    """#{학생명} 자리에 값을 넣는다. 보낸 글을 그대로 남기려면 우리도 같은 셈을 해야 한다."""
    out = body or ""
    for k, v in (variables or {}).items():
        out = out.replace("#{%s}" % k, str(v if v is not None else ""))
    return out


def _send_alimtalk(to, template_id, variables):
    """솔라피로 알림톡 한 건. (성공?, 메시지id, 까닭) 을 돌려준다."""
    ko = {"pfId": _env("SOLAPI_PFID"), "templateId": template_id,
          "variables": {("#{%s}" % k): str(v if v is not None else "")
                        for k, v in (variables or {}).items()},
          "disableSms": _env("SOLAPI_SMS_FALLBACK", "0") != "1"}
    msg = {"to": to, "kakaoOptions": ko}
    sender = norm_phone(_env("SOLAPI_SENDER_PHONE"))
    if sender:
        msg["from"] = sender
    ok, d = _call("/messages/v4/send", {"message": msg}, method="POST")
    if not ok:
        return False, "", (d.get("errorMessage") or d.get("message") or str(d))[:200]
    m = d.get("messageId") or (d.get("messageList") or [{}])[0].get("messageId", "")
    st = (d.get("statusCode") or "")
    # 2000 대가 접수 성공. 그 밖은 중계사가 받아 주지 않은 것이다.
    if st and not str(st).startswith("2"):
        return False, m, "%s %s" % (st, d.get("statusMessage") or "")[:200]
    return True, m, ""


def template_for(kind, branch_id=None):
    """이 지점에서 쓸 템플릿. 지점 것이 전 지점 것보다 앞선다."""
    rows = list(NotifyTemplate.objects.filter(kind=kind, is_active=True))
    for r in rows:
        if r.branch_id == branch_id:
            return r
    for r in rows:
        if r.branch_id is None:
            return r
    return None


def notify(student, kind, variables, to_name="", to_phone="", actor=None,
           idem_key=None, ref_id=None, force=False):
    """한 건 보내고 남긴다.

    force=True 면 자동 발송이 꺼져 있어도 보낸다(사람이 화면에서 누른 경우).
    보내지 않기로 하면 SKIPPED 로 까닭과 함께 남긴다.
    """
    prof = AcademyProfile.objects.filter(user=student, is_deleted=False).first()
    sp = StudentProfile.objects.filter(user=student).first()
    tpl = template_for(kind, prof.branch_id if prof else None)

    def _log(status, message="", body="", channel="", pid=""):
        try:
            return NotifyLog.objects.create(
                student=student, branch_id=(prof.branch_id if prof else None),
                to_name=to_name, to_phone=to_phone or "", kind=kind, channel=channel,
                provider=("SOLAPI" if channel else ""),
                template_code=(tpl.template_id if tpl else ""),
                body=body, variables=_json.dumps(variables or {}, ensure_ascii=False),
                status=status, message=message[:255], provider_msg_id=pid,
                idem_key=(idem_key or None), actor=actor, ref_id=ref_id,
                sent_time=(now() if status == NotifyStatus.SENT else None))
        except Exception:
            return None

    if idem_key and NotifyLog.objects.filter(idem_key=idem_key).exists():
        return None, "이미 보낸 건입니다."
    if not tpl or not tpl.template_id:
        return _log(NotifyStatus.SKIPPED, "승인된 템플릿이 아직 없습니다."), "템플릿 없음"
    body = render(tpl.body, variables)
    if not tpl.auto and not force:
        return _log(NotifyStatus.SKIPPED, "자동 발송이 꺼져 있습니다.", body), "자동 꺼짐"
    if sp and sp.enrollment_status != EnrollmentStatus.ENROLLED:
        return _log(NotifyStatus.SKIPPED, "재원 중이 아닙니다.", body), "재원 아님"
    if kind in (NotifyKind.ARRIVE, NotifyKind.LEAVE) and not (sp and sp.notify_optin):
        return _log(NotifyStatus.SKIPPED, "등하원 알림을 받지 않는 집입니다.", body), "미수신"
    to = norm_phone(to_phone)
    if not to:
        return _log(NotifyStatus.SKIPPED, "받을 번호가 없습니다.", body), "번호 없음"
    if not configured():
        return _log(NotifyStatus.SKIPPED, "솔라피 설정이 없습니다.", body), "설정 없음"

    ok, pid, err = _send_alimtalk(to, tpl.template_id, variables)
    row = _log(NotifyStatus.SENT if ok else NotifyStatus.FAILED,
               "" if ok else err, body, channel="KAKAO", pid=pid)
    return row, ("" if ok else err)
