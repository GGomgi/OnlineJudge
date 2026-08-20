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

    # 회수제 지점(김포)은 주3·4회도 표에서 금액을 직접 받는다 — '4주 12회 얼마'로 팔기
    # 때문이다. 기간제 지점은 주3회 이상을 회당 단가로 흩어 셈한다.
    top = 4 if getattr(prof.branch if prof and prof.branch_id else None,
                       "is_session_based", False) else 2
    if n <= top and len(set(durs)) == 1:
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
    from .models import Branch
    branch = Branch.objects.filter(id=branch_id).first()
    rates = rate_table(branch_id)
    if slots:
        durs = sorted(s.duration_minutes for s in slots)
    elif st and st.planned_sessions and st.planned_duration:
        durs = [st.planned_duration] * st.planned_sessions
    else:
        return None, "시간표 없음"
    n = len(durs)
    top = 4 if getattr(branch, "is_session_based", False) else 2
    if n <= top and len(set(durs)) == 1:
        amt = rates.get((n, durs[0]))
        return (amt, "") if amt else (None, "기준표에 없음")
    total = 0
    for d in durs:
        u = unit_price(rates, d)
        if u is None:
            return None, "기준표에 없음"
        total += u * WEEKS_PER_MONTH
    return int(round(total)), ""


def _disc_value(r):
    """이 학생에게 적용할 값. 사람마다 다른 경우(진학 할인)는 덮어쓴 값을 쓴다."""
    return r.item.value if r.value_override is None else r.value_override


def _pick_discounts(student_id, for_ym):
    """이 달에 붙일 할인 줄을 고른다.

    '한 번만' 할인은 이미 쓴 것이면 빠진다. 그 달 청구서에 쓴 것이면 그대로 붙어
    있어야 하므로(다시 뽑아도 금액이 같아야 한다) 쓴 달이 지금 보는 달과 같으면 붙인다.

    '한 달에 한 줄'(소개 할인)은 여러 건이 밀려 있어도 그 달에는 하나만 붙이고
    나머지는 다음 달로 넘긴다. 같은 달에 셋을 소개해도 그 달 원비가 무너지지 않고,
    소개한 만큼은 달을 나눠 다 받는다.
    """
    ym = for_ym or ""
    rows, pool = [], {}
    for r in StudentDiscount.objects.filter(student_id=student_id, is_active=True) \
                                    .select_related("item").order_by("id"):
        if not r.item.recurring and r.used_ym and r.used_ym != ym:
            continue                      # 다른 달에 이미 쓴 한 번만 할인
        if r.item.once_per_month:
            pool.setdefault(r.item_id, []).append(r)
            continue
        rows.append(r)
    later = {}
    for k, group in pool.items():
        # 그 달 청구서에 이미 쓴 줄이 있으면 그것이 이긴다(다시 뽑아도 금액이 같아야 한다)
        pick = next((x for x in group if x.used_ym == ym), group[0])
        rows.append(pick)
        if len(group) > 1:
            later[group[0].item.name] = len(group) - 1
    return rows, later


def _apply_discounts(student_id, out, for_ym=None):
    """정액을 먼저 빼고, 남은 금액에 비율을 적용한다.

    겹쳐 붙은 할인에는 지점의 상한이 걸린다. 다만 '상한 제외'로 표시한 항목
    (소개 할인)은 세지 않는다 — 원비를 깎아 주는 것이 아니라 소개해 준 데 대한
    답례라, 상한에 밀려 사라지면 뜻이 없어진다.
    """
    rows, later = _pick_discounts(student_id, for_ym)

    def line(r, off):
        return {"id": r.id, "name": r.item.name, "kind": r.item.kind,
                "value": _disc_value(r), "off": off, "note": r.note,
                "recurring": r.item.recurring, "used_ym": r.used_ym,
                "capped": False, "excluded": r.item.exclude_from_cap}

    amount = out["base"]
    out["queued_discounts"] = later          # 다음 달로 밀린 건수
    if amount is None:
        out["discounts"] = [line(r, 0) for r in rows]
        return out

    prof = AcademyProfile.objects.filter(user_id=student_id, is_deleted=False).first()
    branch = prof.branch if prof and prof.branch_id else None
    cap_pct = getattr(branch, "discount_cap_percent", 0) or 0
    cap_amt = getattr(branch, "discount_cap_amount", 0) or 0

    base = amount
    lines, capped_sum, free_sum = [], 0, 0
    # 상한에 드는 것과 안 드는 것을 나눠 셈한다. 정액 먼저, 비율 나중은 그대로다.
    for group in (False, True):              # False=상한 대상, True=상한 제외
        for kind in ("AMOUNT", "PERCENT"):
            for r in [x for x in rows if bool(x.item.exclude_from_cap) == group and x.item.kind == kind]:
                v = _disc_value(r)
                off = min(v, amount) if kind == "AMOUNT" else int(amount * v / 100.0)
                off = max(0, min(off, amount))
                amount -= off
                if group:
                    free_sum += off
                else:
                    capped_sum += off
                lines.append(line(r, off))

    # 상한을 넘으면 상한 대상 줄만 비율대로 줄인다
    limit = None
    if cap_pct:
        limit = int(base * cap_pct / 100.0)
    if cap_amt:
        limit = cap_amt if limit is None else min(limit, cap_amt)
    if limit is not None and capped_sum > limit:
        keep = limit
        for ln in lines:
            if ln["excluded"]:
                continue
            share = int(round(ln["off"] * limit / float(capped_sum))) if capped_sum else 0
            share = min(share, keep)
            keep -= share
            if share != ln["off"]:
                ln["capped"] = True
            ln["off"] = share
        amount = base - limit - free_sum
        out["cap_applied"] = {"limit": limit, "before": capped_sum}

    out["cap"] = {"percent": cap_pct, "amount": cap_amt}
    out["discounts"] = lines
    out["amount"] = max(0, int(amount))
    return out
