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
            "is_void": inv.is_void, "void_reason": inv.void_reason,
            "state": ("취소" if inv.is_void else ("완납" if p >= inv.amount else ("일부" if p else "미납")))}


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
        qs = Invoice.objects.filter(ym=ym).select_related("student", "student__userprofile", "branch")
        if view is not None:
            qs = qs.filter(branch_id__in=view)
        bid = request.GET.get("branch_id")
        if bid:
            qs = qs.filter(branch_id=bid)
        rows = list(qs)
        paid = _paid_map([x.id for x in rows])
        out = [_inv_row(x, paid) for x in rows]
        live = [x for x in out if not x["is_void"]]
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
        have = set(Invoice.objects.filter(ym=ym, student_id__in=[p.user_id for p in profs])
                                  .values_list("student_id", flat=True))

        made, skipped, undecided, rows = 0, 0, 0, []
        for p in sorted(profs, key=lambda x: _name_of(x.user)):
            if p.user_id in have:
                skipped += 1
                continue
            t = compute(p.user_id)
            row = {"student_id": p.user_id, "name": _name_of(p.user),
                   "amount": t["amount"], "base": t["base"], "source": t["source"],
                   "discounts": t["discounts"], "warnings": t["warnings"]}
            rows.append(row)
            if t["amount"] is None:
                undecided += 1
                continue                      # 금액 미정은 만들지 않는다. 틀린 금액을 남기느니 비운다
            if commit:
                Invoice.objects.create(
                    student_id=p.user_id, branch_id=p.branch_id, ym=ym,
                    base_amount=t["base"] or 0,
                    discount_amount=sum(x["off"] for x in t["discounts"]),
                    amount=t["amount"], source=t["source"],
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
