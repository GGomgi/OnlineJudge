"""학생 한 명의 원비를 계산한다.

금액을 저장하지 않고 볼 때마다 셈한다 — 시간표를 바꾸면 원비도 따라가야 하기 때문이다.
사람이 직접 정한 금액(MANUAL)만 저장된다.

계산 규칙은 docs/81_TUITION_PRICING_DESIGN.md 를 따른다.

  회당 단가(t) = 주2회 t분 금액 ÷ 8

  주1·2회, 회당 시간이 모두 같음  →  기준표 금액 그대로
  주1·2회, 회당 시간이 섞임        →  Σ (각 회차의 회당 단가 × 4)
  주3회 이상                       →  Σ (각 회차의 회당 단가 × 4)   ※ 회수제

할인은 정액을 먼저 빼고, 남은 금액에 비율을 적용한다(순서에 따라 금액이 달라져 못박아 둔다).
"""
from datetime import timedelta

from django.utils.timezone import now

from .models import (StudentTimetable, StudentProfile, AcademyProfile, EnrollmentStatus,
                     TuitionRate, StudentTuition, StudentDiscount)

# 한 달을 몇 회로 보는가. 주2회 기준 8회이므로 주당 4회.
WEEKS_PER_MONTH = 4


def _today():
    return (now() + timedelta(hours=9)).date()


def active_slots(student_id, on=None):
    """오늘(또는 지정일) 적용 기간 안에 있는 시간표만.

    요일을 옮기면 옛 줄은 기간만 끊기고 상태는 ACTIVE 로 남는다(과거 기록 보존).
    상태만 보고 세면 주1회가 주2회로 둔갑한다 — 반드시 기간까지 본다.
    """
    from .views.admin import _slot_active_on
    d = on or _today()
    return [t for t in StudentTimetable.objects.filter(student_id=student_id).exclude(status="ENDED")
            if _slot_active_on(t, d)]


def rate_table(branch_id):
    return {(r.sessions_per_week, r.duration_minutes): r.amount
            for r in TuitionRate.objects.filter(branch_id=branch_id) if r.amount}


def unit_price(rates, duration):
    """회당 단가 = 주2회 그 시간 금액 ÷ 8. 그 칸이 비어 있으면 셈할 수 없다."""
    base = rates.get((2, duration))
    return (base / 8.0) if base else None


def compute(student_id, for_ym=None):
    """{mode, amount, base, source, discounts, warnings, sessions, durations} 를 돌려준다.

    amount 가 None 이면 '금액 미정'이다. 임의로 계산해 틀린 금액을 청구하느니 비워 둔다.
    """
    prof = AcademyProfile.objects.filter(user_id=student_id, is_deleted=False).first()
    branch_id = prof.branch_id if prof else None
    st = StudentTuition.objects.filter(student_id=student_id).first()
    mode = st.mode if st else "AUTO"
    out = {"mode": mode, "amount": None, "base": None, "source": "",
           "discounts": [], "warnings": [], "sessions": 0, "durations": [],
           "planned_sessions": (st.planned_sessions if st else None),
           "planned_duration": (st.planned_duration if st else None),
           "manual_amount": (st.manual_amount if st else None),
           "note": (st.note if st else "")}

    slots = active_slots(student_id)
    durs = sorted(s.duration_minutes for s in slots)
    out["sessions"] = len(slots)
    out["durations"] = durs

    # 예정과 실제가 어긋나면 알린다 — 시간표를 나중에 넣다가 달라지는 일이 잦다
    if st and st.planned_sessions and slots and st.planned_sessions != len(slots):
        out["warnings"].append("예정 주%d회인데 시간표는 주%d회입니다."
                               % (st.planned_sessions, len(slots)))
    if st and st.planned_duration and durs and set(durs) != {st.planned_duration}:
        out["warnings"].append("예정 %d분인데 시간표는 %s분입니다."
                               % (st.planned_duration, "+".join(str(x) for x in durs)))

    if mode == "UNDECIDED":
        out["source"] = "미정"
        return out

    if mode == "MANUAL":
        out["base"] = st.manual_amount if st else None
        out["source"] = "직접 지정"
        if out["base"] is None:
            out["warnings"].append("직접 지정인데 금액이 비어 있습니다.")
        # 기준표대로면 얼마인지 함께 알려 준다 — 얼마나 깎아 주고 있는지 보여야 한다
        out["auto_base"] = _auto_base(branch_id, slots, st)[0]
        return _apply_discounts(student_id, out, for_ym)

    # ── 자동 ──
    out["auto_base"] = None
    if not branch_id:
        out["warnings"].append("지점이 없어 기준표를 찾을 수 없습니다.")
        return out
    rates = rate_table(branch_id)

    if not slots:
        # 시간표가 아직 없으면 미리 정해 둔 주횟수·시간으로 셈한다
        if not (st and st.planned_sessions and st.planned_duration):
            out["warnings"].append("시간표가 없습니다. 주횟수와 회당 시간을 미리 정해 두거나 미정으로 두세요.")
            return out
        n, durs = st.planned_sessions, [st.planned_duration] * st.planned_sessions
        out["source"] = "예정 주%d회 %d분" % (n, st.planned_duration)
    else:
        n = len(slots)
        out["source"] = "시간표 주%d회 %s분" % (n, "+".join(str(x) for x in durs))

    if n <= 2 and len(set(durs)) == 1:
        amt = rates.get((n, durs[0]))
        if amt is None:
            out["warnings"].append("기준표에 주%d회 %d분 금액이 없습니다." % (n, durs[0]))
            return out
        out["base"] = amt
        out["auto_base"] = amt
    else:
        # 회당 단가로 회차마다 셈해 더한다(주3회 이상 · 회당 시간 섞임)
        total, missing = 0, []
        for d in durs:
            u = unit_price(rates, d)
            if u is None:
                missing.append(d)
            else:
                total += u * WEEKS_PER_MONTH
        if missing:
            out["warnings"].append("기준표에 주2회 %s분 금액이 없어 회당 단가를 셈할 수 없습니다."
                                   % "·".join(str(x) for x in sorted(set(missing))))
            return out
        out["base"] = int(round(total))
        out["auto_base"] = out["base"]
        out["source"] += " · 회당 단가"

    return _apply_discounts(student_id, out, for_ym)


def _auto_base(branch_id, slots, st):
    """기준표대로면 얼마인가. (금액, 까닭) — 못 셈하면 (None, 까닭)."""
    if not branch_id:
        return None, "지점 없음"
    rates = rate_table(branch_id)
    if slots:
        durs = sorted(s.duration_minutes for s in slots)
    elif st and st.planned_sessions and st.planned_duration:
        durs = [st.planned_duration] * st.planned_sessions
    else:
        return None, "시간표 없음"
    n = len(durs)
    if n <= 2 and len(set(durs)) == 1:
        amt = rates.get((n, durs[0]))
        return (amt, "") if amt else (None, "기준표에 없음")
    total = 0
    for d in durs:
        u = unit_price(rates, d)
        if u is None:
            return None, "기준표에 없음"
        total += u * WEEKS_PER_MONTH
    return int(round(total)), ""


def _apply_discounts(student_id, out, for_ym=None):
    """정액을 먼저 빼고, 남은 금액에 비율을 적용한다.

    '한 번만' 할인은 이미 쓴 것이면 빠진다. 그 달 청구서에 쓴 것이면 그대로 붙어
    있어야 하므로(다시 뽑아도 금액이 같아야 한다) 쓴 달이 지금 보는 달과 같으면 붙인다.
    """
    rows = []
    for r in StudentDiscount.objects.filter(student_id=student_id, is_active=True) \
                                    .select_related("item"):
        if not r.item.recurring and r.used_ym and r.used_ym != (for_ym or ""):
            continue                      # 다른 달에 이미 쓴 한 번만 할인
        rows.append(r)
    amount = out["base"]
    if amount is None:
        out["discounts"] = [{"id": r.id, "name": r.item.name, "kind": r.item.kind,
                             "value": r.item.value, "off": 0, "note": r.note,
                             "recurring": r.item.recurring, "used_ym": r.used_ym} for r in rows]
        return out
    lines = []
    for r in [x for x in rows if x.item.kind == "AMOUNT"]:
        off = min(r.item.value, amount)
        amount -= off
        lines.append({"id": r.id, "name": r.item.name, "kind": "AMOUNT",
                      "value": r.item.value, "off": off, "note": r.note,
                      "recurring": r.item.recurring, "used_ym": r.used_ym})
    for r in [x for x in rows if x.item.kind == "PERCENT"]:
        off = int(amount * r.item.value / 100.0)
        amount -= off
        lines.append({"id": r.id, "name": r.item.name, "kind": "PERCENT",
                      "value": r.item.value, "off": off, "note": r.note,
                      "recurring": r.item.recurring, "used_ym": r.used_ym})
    out["discounts"] = lines
    out["amount"] = max(0, int(amount))
    return out
