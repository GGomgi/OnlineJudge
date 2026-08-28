"""학생 한 명의 원비를 계산한다.

금액을 저장하지 않고 볼 때마다 셈한다 — 시간표를 바꾸면 원비도 따라가야 하기 때문이다.
사람이 직접 정한 금액(MANUAL)만 저장된다.

계산 규칙은 docs/81_TUITION_PRICING_DESIGN.md 를 따른다.

  회당 단가(t) = 주2회 t분 금액 ÷ 8

  주1·2회, 회당 시간이 모두 같음  →  기준표 금액 그대로
  주1·2회, 회당 시간이 섞임        →  Σ (각 회차의 회당 단가 × 4)
  주3회 이상                       →  Σ (각 회차의 회당 단가 × 4)   ※ 회수제

할인은 겹치지 않는다. 여럿이 걸려도 큰 것 하나만 붙는다(소개 할인만 따로 붙는다).
항목마다 최대치가 있고, 그 값은 기준표(DiscountCap)가 정한다.
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


def months_enrolled(student_id, on=None):
    """이 학생이 몇 달째 다니는가. 수업 시작일이 없으면 등록일을 본다."""
    sp = StudentProfile.objects.filter(user_id=student_id).first()
    d0 = (sp.lesson_start_date if sp else None) or (sp.enrollment_date if sp else None)
    if not d0:
        return 0
    d1 = on or _today()
    m = (d1.year - d0.year) * 12 + (d1.month - d0.month)
    if d1.day < d0.day:
        m -= 1
    return max(0, m)


def cap_for(branch_id, scope, sessions, months):
    """최대치 기준표에서 이 학생에게 걸리는 뚜껑을 찾는다.

    조건을 만족하는 줄 가운데 주회수가 가장 큰 것, 그 안에서 개월이 가장 큰 것.
    지점 표가 있으면 지점 것이 먼저다 — 지점마다 다르게 줄 수 있어야 한다.
    표에 줄이 없으면 뚜껑이 없는 것으로 본다(None).
    """
    from .models import DiscountCap
    q = DiscountCap.objects.filter(scope=scope, sessions_min__lte=sessions,
                                   months_min__lte=months)
    rows = [r for r in q if r.branch_id in (branch_id, None)]
    if not rows:
        return None
    rows.sort(key=lambda r: (0 if r.branch_id else 1, -r.sessions_min, -r.months_min))
    return rows[0].amount


def _apply_discounts(student_id, out, for_ym=None):
    """할인은 겹치지 않는다. 큰 것 하나만 붙는다.

    진학과 형제가 함께 걸려도 둘 중 큰 쪽만 간다. 밀린 줄도 청구서에 남긴다 —
    지워 버리면 학부모가 "형제 할인은요?" 하고 물을 때 댈 말이 없다.

    '따로 붙음'(소개 할인)은 이 겨룸 밖이다. 원비를 깎아 주는 것이 아니라 소개해 준 데
    대한 답례라, 진학 할인에 밀려 사라지면 뜻이 없어진다.
    """
    rows, later = _pick_discounts(student_id, for_ym)

    amount = out["base"]
    out["queued_discounts"] = later          # 다음 달로 밀린 건수

    def line(r, off, cap=None, beaten=False):
        return {"id": r.id, "name": r.item.name, "kind": r.item.kind,
                "value": _disc_value(r), "off": off, "note": r.note,
                "recurring": r.item.recurring, "used_ym": r.used_ym,
                "raw": None, "capped": False, "cap": cap,
                "alone": r.item.stands_alone, "beaten": beaten}

    if amount is None:
        out["discounts"] = [line(r, 0) for r in rows]
        return out

    prof = AcademyProfile.objects.filter(user_id=student_id, is_deleted=False).first()
    branch_id = prof.branch_id if prof else None
    sessions = out.get("sessions") or 0
    months = months_enrolled(student_id)
    out["enrolled_months"] = months

    base = amount
    lines = []
    for r in rows:
        v = _disc_value(r)
        raw = v if r.item.kind == "AMOUNT" else int(base * v / 100.0)
        cap = cap_for(branch_id, r.item.cap_scope or "DEFAULT", sessions, months)
        off = raw if cap is None else min(raw, cap)
        ln = line(r, max(0, min(off, base)), cap)
        ln["raw"] = raw
        ln["capped"] = (cap is not None and raw > cap)
        lines.append(ln)

    alone = [ln for ln in lines if ln["alone"]]
    rivals = [ln for ln in lines if not ln["alone"]]
    if rivals:
        # 같은 금액이면 먼저 붙인 것이 이긴다(줄 차례가 곧 붙인 차례다)
        win = max(rivals, key=lambda ln: ln["off"])
        for ln in rivals:
            if ln is not win:
                ln["beaten"] = True
                ln["off"] = 0
    total_off = sum(ln["off"] for ln in lines)
    out["discounts"] = lines
    out["amount"] = max(0, int(base - total_off))
    out["cap_rule"] = {"sessions": sessions, "months": months}
    return out
