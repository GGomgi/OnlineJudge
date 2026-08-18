"""월 청구서와 납부.

청구와 납부를 따로 둔다 — 늦게 내는 사람이 많고 두 달치를 한 번에 내는 일도 있어,
한 번 낸 돈이 여러 달 청구서에 나눠 붙어야 하기 때문이다.

청구서는 만들 때의 금액을 굳혀 둔다. 나중에 기준표가 바뀌어도 지난 청구서는 그대로여야
장부가 된다.
"""
import json as _json
from datetime import timedelta, datetime

from django.db.models import Q, Sum
from django.utils.timezone import now

from utils.api import APIView
from account.decorators import admin_role_required
from account.models import User

from ..models import (Invoice, Payment, PaymentAlloc, AcademyProfile, AcademyRole,
                      StudentProfile, EnrollmentStatus, Branch)
from ..services import viewable_branch_ids, can_manage_branch, can_view_branch
from ..services_tuition import compute

_METHOD = {"TRANSFER": "계좌이체", "CASH": "현금", "CARD": "카드", "ETC": "기타"}


def _name_of(u):
    try:
        return u.userprofile.real_name or u.username
    except Exception:
        return u.username if u else ""


def _kst_today():
    return (now() + timedelta(hours=9)).date()


def _paid_map(invoice_ids):
    """청구서마다 얼마가 채워졌는지."""
    out = {}
    for r in PaymentAlloc.objects.filter(invoice_id__in=invoice_ids, payment__is_void=False) \
                                 .values("invoice_id").annotate(s=Sum("amount")):
        out[r["invoice_id"]] = r["s"] or 0
    return out


def _inv_row(inv, paid):
    p = paid.get(inv.id, 0)
    try:
        lines = _json.loads(inv.lines) if inv.lines else []
    except (ValueError, TypeError):
        lines = []
    return {"id": inv.id, "ym": inv.ym, "student_id": inv.student_id,
            "name": _name_of(inv.student), "branch": (inv.branch.name if inv.branch_id else ""),
            "base_amount": inv.base_amount, "discount_amount": inv.discount_amount,
            "amount": inv.amount, "paid": p, "remain": max(0, inv.amount - p),
            "source": inv.source, "lines": lines, "note": inv.note,
            "revision": inv.revision,
            "is_void": inv.is_void, "void_reason": inv.void_reason,
            "state": ("취소" if inv.is_void else ("완납" if p >= inv.amount else ("일부" if p else "미납")))}


def live_invoices(**flt):
    """살아 있는 청구서. 같은 학생·같은 달에 여러 차수가 있으면 마지막 것만."""
    best = {}
    for inv in Invoice.objects.filter(is_void=False, **flt).select_related(
            "student", "student__userprofile", "branch"):
        k = (inv.student_id, inv.ym)
        if k not in best or inv.revision > best[k].revision:
            best[k] = inv
    return list(best.values())


def _branch_scope(request):
    view = viewable_branch_ids(request.user)
    bid = request.GET.get("branch_id") or request.data.get("branch_id") if hasattr(request, "data") else None
    return view, bid


class InvoiceAPI(APIView):
    """월 청구서. GET 은 그달 목록, POST 는 미리보기(commit=false)/만들기(commit=true)."""

    @admin_role_required
    def get(self, request):
        view = viewable_branch_ids(request.user)
        ym = (request.GET.get("ym") or str(_kst_today())[:7]).strip()
        flt = {"ym": ym}
        if view is not None:
            flt["branch_id__in"] = view
        bid = request.GET.get("branch_id")
        if bid:
            flt["branch_id"] = bid
        rows = sorted(live_invoices(**flt), key=lambda x: _name_of(x.student))
        paid = _paid_map([x.id for x in rows])
        out = [_inv_row(x, paid) for x in rows]
        live = out
        return self.success({
            "ym": ym, "rows": out,
            "total": sum(x["amount"] for x in live),
            "paid": sum(x["paid"] for x in live),
            "remain": sum(x["remain"] for x in live),
            "branches": [{"id": b.id, "name": b.name} for b in Branch.objects.filter(
                is_active=True, **({"id__in": view} if view is not None else {}))],
        })

    @admin_role_required
    def post(self, request):
        """{branch_id, ym, commit}. 이미 있는 학생은 건너뛴다(두 번 눌러도 안전)."""
        d = request.data
        ym = (d.get("ym") or str(_kst_today())[:7]).strip()
        if len(ym) != 7 or ym[4] != "-":
            return self.error("달이 올바르지 않습니다(2026-08).")
        bid = d.get("branch_id")
        if not bid:
            return self.error("지점을 고르세요.")
        if not can_manage_branch(request.user, int(bid)):
            return self.error("이 지점을 관리할 권한이 없습니다.")
        commit = bool(d.get("commit"))

        enrolled = set(StudentProfile.objects.filter(
            enrollment_status=EnrollmentStatus.ENROLLED).values_list("user_id", flat=True))
        profs = AcademyProfile.objects.filter(
            is_deleted=False, role=AcademyRole.STUDENT, branch_id=bid, user_id__in=enrolled
        ).select_related("user", "user__userprofile", "branch")
        # 이미 만든 것은 건너뛰지 않고 '몇 차로 나갔는지'를 함께 보여 준다.
        # 금액을 고쳐 다시 안내할 일이 있어서다(결석 이월 등).
        cur = {}
        for inv in live_invoices(ym=ym, student_id__in=[p.user_id for p in profs]):
            cur[inv.student_id] = inv
        paid_now = _paid_map([i.id for i in cur.values()])
        # 지난달에 조정한 게 있으면 알려 준다. 금액을 물려받지는 않는다 —
        # 결석 이월 같은 건 그 달 한 번뿐이라 물려받으면 매달 새어 나간다.
        y, m = int(ym[:4]), int(ym[5:7])
        pym = "%04d-%02d" % ((y - 1, 12) if m == 1 else (y, m - 1))
        prev = {i.student_id: i for i in live_invoices(
            ym=pym, student_id__in=[p.user_id for p in profs])}

        # 금액만 봐서는 맞는지 알 수 없다. 학교·요일·시간·과목·담당까지 옆에 놓아
        # 한 줄로 확인할 수 있게 한다.
        from .admin import _school_short
        from ..services_tuition import active_slots
        sprof = {x.user_id: x for x in StudentProfile.objects.filter(
            user_id__in=[p.user_id for p in profs])}
        WD = ["월", "화", "수", "목", "금", "토", "일"]

        want = d.get("items")            # 고른 학생만. 없으면 전부(미리보기)
        pick = {int(x["student_id"]): x for x in want} if want else None

        made, skipped, undecided, rows = 0, 0, 0, []
        for p in sorted(profs, key=lambda x: _name_of(x.user)):
            t = compute(p.user_id, ym)
            slots = sorted(active_slots(p.user_id), key=lambda x: (x.weekday, x.start_time))
            sp = sprof.get(p.user_id)
            row = {"student_id": p.user_id, "name": _name_of(p.user),
                   "amount": t["amount"], "base": t["base"], "source": t["source"],
                   "discounts": t["discounts"], "warnings": t["warnings"],
                   "school": (_school_short(sp) if sp else ""),
                   "sessions": len(slots),
                   "weekdays": ",".join(WD[x.weekday] for x in slots),
                   "times": "+".join(str(x.duration_minutes) for x in slots),
                   "dur_max": max([x.duration_minutes for x in slots] or [0]),
                   "subjects": " · ".join(sorted({(x.subject or "") for x in slots if x.subject})),
                   "instructors": " · ".join(sorted({_name_of(x.instructor)
                                                     for x in slots if x.instructor_id})) or "미배정",
                   "mode": t["mode"], "auto_base": t.get("auto_base")}
            pv = prev.get(p.user_id)
            if pv:
                row["prev"] = {"ym": pv.ym, "amount": pv.amount, "note": pv.note,
                               "revision": pv.revision,
                               "adjusted": (pv.amount != (pv.base_amount - pv.discount_amount))}
            old_inv = cur.get(p.user_id)
            if old_inv:
                row.update({"invoice_id": old_inv.id, "revision": old_inv.revision,
                            "issued_amount": old_inv.amount, "issued_note": old_inv.note,
                            "paid": paid_now.get(old_inv.id, 0)})
            rows.append(row)
            if t["amount"] is None:
                undecided += 1
                continue                      # 금액 미정은 만들지 않는다. 틀린 금액을 남기느니 비운다
            if not commit:
                continue
            if pick is not None and p.user_id not in pick:
                skipped += 1
                continue
            it = (pick or {}).get(p.user_id) or {}
            amt = str(it.get("amount", "")).replace(",", "").strip()
            amount = int(amt) if amt.isdigit() else t["amount"]
            note = (it.get("note") or "").strip()
            if old_inv:
                # 다시 뽑기 — 받은 돈이 붙어 있으면 막는다(먼저 납부를 취소해야 한다)
                if paid_now.get(old_inv.id, 0):
                    skipped += 1
                    continue
                rev = old_inv.revision + 1
                old_inv.is_void = True
                old_inv.void_reason = "%d차 수정발행으로 대체" % rev
                old_inv.save(update_fields=["is_void", "void_reason"])
            else:
                rev = 1
            # 한 번만 할인을 이 청구서에서 썼다고 표시한다 — 다음 달부터는 빠진다
            from ..models import StudentDiscount
            for dc in t["discounts"]:
                if not dc.get("recurring"):
                    StudentDiscount.objects.filter(id=dc["id"], used_ym="").update(used_ym=ym)
            Invoice.objects.create(
                student_id=p.user_id, branch_id=p.branch_id, ym=ym, revision=rev,
                base_amount=t["base"] or 0,
                discount_amount=sum(x["off"] for x in t["discounts"]),
                amount=amount, source=t["source"], note=note,
                lines=_json.dumps(t["discounts"], ensure_ascii=False),
                created_by=request.user)
            made += 1
        return self.success({"ym": ym, "commit": commit, "rows": rows,
                             "made": made, "skipped": skipped, "undecided": undecided,
                             "total": sum(r["amount"] or 0 for r in rows)})

    @admin_role_required
    def delete(self, request):
        """청구서 취소. 지우지 않고 취소로 둔다 — 돈이 오간 기록이다."""
        inv = Invoice.objects.filter(id=request.GET.get("id")).first()
        if not inv:
            return self.error("청구서가 없습니다.")
        if not can_manage_branch(request.user, inv.branch_id):
            return self.error("권한이 없습니다.")
        if PaymentAlloc.objects.filter(invoice=inv, payment__is_void=False).exists():
            return self.error("이미 받은 돈이 붙어 있습니다. 납부를 먼저 취소하세요.")
        inv.is_void = True
        inv.void_reason = (request.GET.get("reason") or "").strip()
        inv.save(update_fields=["is_void", "void_reason"])
        # 이 달에 썼던 '한 번만' 할인을 다시 풀어 준다 — 안 쓴 것이 되어야 한다
        from ..models import StudentDiscount
        StudentDiscount.objects.filter(student_id=inv.student_id, used_ym=inv.ym).update(used_ym="")
        return self.success({"ok": True})


class PaymentAPI(APIView):
    """받은 돈. 오래 밀린 청구서부터 채운다."""

    @admin_role_required
    def get(self, request):
        sid = request.GET.get("student_id")
        if sid:
            prof = AcademyProfile.objects.filter(user_id=sid, is_deleted=False).first()
            if prof and not can_view_branch(request.user, prof.branch_id):
                return self.error("권한이 없습니다.")
            invs = list(Invoice.objects.filter(student_id=sid).select_related("student", "student__userprofile", "branch"))
            paid = _paid_map([x.id for x in invs])
            pays = [{"id": p.id, "paid_on": str(p.paid_on), "amount": p.amount,
                     "method": _METHOD.get(p.method, p.method), "note": p.note,
                     "is_void": p.is_void, "actor": _name_of(p.created_by) if p.created_by_id else "",
                     "allocs": [{"ym": a.invoice.ym, "amount": a.amount}
                                for a in p.allocs.select_related("invoice")]}
                    for p in Payment.objects.filter(student_id=sid).select_related("created_by")]
            return self.success({"invoices": [_inv_row(x, paid) for x in invs], "payments": pays})

        # 미납 모아 보기 — 누가 몇 달치 얼마 밀렸는지
        view = viewable_branch_ids(request.user)
        qs = Invoice.objects.filter(is_void=False).select_related("student", "student__userprofile", "branch")
        if view is not None:
            qs = qs.filter(branch_id__in=view)
        bid = request.GET.get("branch_id")
        if bid:
            qs = qs.filter(branch_id=bid)
        rows = list(qs)
        paid = _paid_map([x.id for x in rows])
        by = {}
        for x in rows:
            r = max(0, x.amount - paid.get(x.id, 0))
            if not r:
                continue
            k = x.student_id
            by.setdefault(k, {"student_id": k, "name": _name_of(x.student),
                              "branch": (x.branch.name if x.branch_id else ""),
                              "months": [], "remain": 0})
            by[k]["months"].append(x.ym)
            by[k]["remain"] += r
        out = sorted(by.values(), key=lambda v: (-len(v["months"]), -v["remain"]))
        for v in out:
            v["months"].sort()
        return self.success({"rows": out, "total": sum(v["remain"] for v in out)})

    @admin_role_required
    def post(self, request):
        """{student_id, paid_on, amount, method, note}. 오래 밀린 달부터 채운다."""
        d = request.data
        sid = d.get("student_id")
        prof = AcademyProfile.objects.filter(user_id=sid, is_deleted=False).first()
        if not prof:
            return self.error("학생이 없습니다.")
        if not can_manage_branch(request.user, prof.branch_id):
            return self.error("권한이 없습니다.")
        try:
            amount = int(str(d.get("amount") or "").replace(",", ""))
        except (TypeError, ValueError):
            return self.error("금액이 올바르지 않습니다.")
        if amount <= 0:
            return self.error("금액을 넣어 주세요.")
        try:
            paid_on = datetime.strptime(d.get("paid_on"), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            paid_on = _kst_today()
        pay = Payment.objects.create(
            student_id=sid, branch_id=prof.branch_id, paid_on=paid_on, amount=amount,
            method=(d.get("method") if d.get("method") in _METHOD else "TRANSFER"),
            note=(d.get("note") or "").strip(), created_by=request.user)

        # 오래된 달부터 채운다. 남으면 그대로 둔다(다음 청구서가 생기면 그때 붙인다).
        invs = list(Invoice.objects.filter(student_id=sid, is_void=False).order_by("ym"))
        paid = _paid_map([x.id for x in invs])
        left = amount
        for inv in invs:
            if left <= 0:
                break
            need = inv.amount - paid.get(inv.id, 0)
            if need <= 0:
                continue
            take = min(need, left)
            PaymentAlloc.objects.create(payment=pay, invoice=inv, amount=take)
            left -= take
        return self.success({"payment_id": pay.id, "allocated": amount - left, "left": left})

    @admin_role_required
    def delete(self, request):
        """납부 취소. 붙어 있던 것도 함께 떨어진다."""
        pay = Payment.objects.filter(id=request.GET.get("id")).first()
        if not pay:
            return self.error("납부 기록이 없습니다.")
        if not can_manage_branch(request.user, pay.branch_id):
            return self.error("권한이 없습니다.")
        pay.is_void = True
        pay.void_reason = (request.GET.get("reason") or "").strip()
        pay.save(update_fields=["is_void", "void_reason"])
        PaymentAlloc.objects.filter(payment=pay).delete()
        return self.success({"ok": True})
