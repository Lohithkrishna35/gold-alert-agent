"""Builds the family alert, the fallback template, and admin-only notes.

All numbers come from the database. The agent may write its own wording, but the
validator only accepts numbers that appear in `facts` below.
"""

from __future__ import annotations

import datetime as dt

from .models import CrossCheck, Evaluation, Rate

SESSION_LABEL = {"AM": "AM rate (about 12:05 PM)", "PM": "PM rate (about 5:05 PM)"}
FOOTER = (
    "ℹ️ IBJA benchmark, before GST and making charges.\n"
    "Info only, not buying advice. Confirm the rate at your jeweller."
)
SPARK = "▁▂▃▄▅▆▇█"


def inr(amount: float) -> str:
    """Indian digit grouping, whole rupees: 123092 -> '1,23,092'."""
    n = int(round(abs(amount)))
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def nice_date(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def _sparkline(values: list[float]) -> str:
    """Tiny text chart, one bar per day: works in Telegram and WhatsApp."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK[3] * len(values)
    return "".join(SPARK[round((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in values)


def trend_facts(current: Rate, all_rates: list[Rate]) -> dict:
    """7-day and 30-day low/high for 22K, honest about how much history exists."""
    upto = [r for r in all_rates if r.sort_key <= current.sort_key]
    if current not in upto:
        upto.append(current)
    upto.sort(key=lambda r: r.sort_key)
    earliest = upto[0].date
    out: dict = {}
    for days, key in ((7, "d7"), (30, "d30")):
        start = current.date - dt.timedelta(days=days - 1)
        points = [r for r in upto if r.date >= start]
        first_business = start
        while first_business.weekday() >= 5:  # IBJA publishes nothing on weekends
            first_business += dt.timedelta(days=1)
        full = earliest <= first_business
        label = f"Last {days} days" if full else f"Since {nice_date(points[0].date)[:-5]}"
        others = [r for r in points if r.sort_key < current.sort_key]
        out[f"{key}_label"] = label
        out[f"{key}_since"] = "" if full else points[0].date.isoformat()
        out[f"{key}_low"] = inr(min(r.g22 for r in points))
        out[f"{key}_high"] = inr(max(r.g22 for r in points))
        out[f"{key}_is_lowest"] = bool(others) and current.g22_10g < min(r.g22_10g for r in others)
        if key == "d7":
            last_per_day: dict[dt.date, float] = {}
            for r in points:  # sorted, so the PM rate of a day replaces its AM rate
                last_per_day[r.date] = r.g22
            out["d7_spark"] = _sparkline(list(last_per_day.values()))
    return out


def build_facts(ev: Evaluation, cc: CrossCheck, all_rates: list[Rate] | None = None) -> dict:
    """Every number the alert may contain, already formatted."""
    cur, prev = ev.current, ev.previous
    assert prev is not None
    facts: dict = {
        "rate_date": nice_date(cur.date),
        "rate_date_iso": cur.date.isoformat(),
        "session": cur.session,
        "session_label": SESSION_LABEL[cur.session],
        "g22_now": inr(cur.g22),
        "g22_prev": inr(prev.g22),
        "g22_down_rs": inr(prev.g22 - cur.g22),
        "g22_down_pct": f"{abs(ev.change_pct or 0):.1f}",
        "g24_now": inr(cur.g24),
        "g24_prev": inr(prev.g24),
        "g24_down_rs": inr(prev.g24 - cur.g24),
        "g24_down_pct": f"{abs((cur.g24_10g - prev.g24_10g) / prev.g24_10g * 100):.1f}",
        "previous_rate_label": f"{nice_date(prev.date)} {prev.session}",
        "triggers": list(ev.triggers),
        "labels": [],
    }
    if all_rates is not None:
        facts.update(trend_facts(cur, all_rates))
    if "low_30d" in ev.triggers:
        facts["labels"].append("Lowest in 30 days")
    elif facts.get("d30_is_lowest") and facts.get("d30_label", "").startswith("Since"):
        facts["labels"].append("Lowest s" + facts["d30_label"][1:])  # "Since 6 Nov" -> "Lowest since 6 Nov"
    elif facts.get("d7_is_lowest"):
        facts["labels"].append(facts["d7_label"].replace("Last", "Lowest in the last").replace("Since", "Lowest since"))
    if "below_high" in ev.triggers and ev.window_high is not None and ev.below_high_pct is not None:
        facts["month_high"] = inr(ev.window_high.g22)
        facts["below_high_pct"] = f"{ev.below_high_pct:.1f}"
        facts["labels"].append(f"{facts['below_high_pct']}% below this month's high (Rs {facts['month_high']})")
    if cc.available:
        facts["global_gold_change_pct"] = f"{abs(cc.global_usd_change_pct or 0):.1f}"
        facts["global_gold_direction"] = "fell" if (cc.global_usd_change_pct or 0) < 0 else "rose"
        facts["usd_inr_change_pct"] = f"{abs(cc.usd_inr_change_pct or 0):.1f}"
        facts["rupee_direction"] = "stronger" if (cc.usd_inr_change_pct or 0) < 0 else "weaker"
    return facts


def template_alert(facts: dict) -> str:
    lines = [
        "🟡 *Gold Price Dropped* 📉",
        f"IBJA rate · {facts['rate_date']}, {facts['session_label']}",
        "",
        f"💰 *22K:* Rs {facts['g22_now']}/g",
        f"      🔻 Rs {facts['g22_down_rs']} (-{facts['g22_down_pct']}%) from Rs {facts['g22_prev']}",
        f"💰 *24K:* Rs {facts['g24_now']}/g",
        f"      🔻 Rs {facts['g24_down_rs']} (-{facts['g24_down_pct']}%) from Rs {facts['g24_prev']}",
    ]
    if "d7_low" in facts:
        spark = f"  {facts['d7_spark']}" if facts.get("d7_spark") else ""
        lines += [
            "",
            "📊 *22K trend*",
            f"{facts['d7_label']}: low Rs {facts['d7_low']} · high Rs {facts['d7_high']}{spark}",
        ]
        if facts["d30_label"] != facts["d7_label"]:
            lines.append(f"{facts['d30_label']}: low Rs {facts['d30_low']} · high Rs {facts['d30_high']}")
    if facts["labels"]:
        lines.append("")
        lines.extend(f"⭐ {label}" for label in facts["labels"])
    lines += ["", FOOTER]
    return "\n".join(lines)


def morning_hint(gl_inr_pct: float, gl_usd_pct: float, fx_pct: float, last: Rate | None) -> str:
    """9:30 AM early hint from the global price. Clearly NOT an official rate."""
    rupee = "weaker" if fx_pct > 0 else "stronger"
    lines = [
        "🌅 *Early hint: gold may be cheaper today*",
        "",
        f"🌍 Global gold fell {abs(gl_inr_pct):.1f}% in its latest session (in rupees).",
        f"      In dollars: -{abs(gl_usd_pct):.1f}% · rupee {abs(fx_pct):.1f}% {rupee}",
    ]
    if last is not None:
        lines.append(f"🏷️ Last IBJA 22K: Rs {inr(last.g22)}/g ({nice_date(last.date)[:-5]} {last.session})")
    lines += [
        "",
        "⏰ Official IBJA rate at about 12:05 PM. If it really drops, you get the full alert at 12:20 PM.",
        "",
        "ℹ️ Early hint only, not an official price and not buying advice.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telugu (for elders). Fixed, reviewed templates filled with the same database numbers.
# The AI only writes the English version; Telugu readers never get unchecked AI text.
# ---------------------------------------------------------------------------
TE_MONTHS = ["జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జూలై", "ఆగస్టు",
             "సెప్టెంబర్", "అక్టోబర్", "నవంబర్", "డిసెంబర్"]
TE_SESSION = {"AM": "మధ్యాహ్నం సుమారు 12:05 గంటల ధర", "PM": "సాయంత్రం సుమారు 5:05 గంటల ధర"}
TE_SESSION_SHORT = {"AM": "మధ్యాహ్నం", "PM": "సాయంత్రం"}
TE_FOOTER = (
    "ℹ️ IBJA = భారత బులియన్ మార్కెట్ అధికారిక ధర (GST, తయారీ ఛార్జీలు కలపకుండా).\n"
    "ఇది సమాచారం మాత్రమే, కొనమని సలహా కాదు. కొనే ముందు మీ నగల దుకాణంలో ధర తెలుసుకోండి."
)


def te_date(d: dt.date | str, year: bool = True) -> str:
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    text = f"{d.day} {TE_MONTHS[d.month - 1]}"
    return f"{text} {d.year}" if year else text


def _te_window(facts: dict, key: str, days: int) -> str:
    since = facts.get(f"{key}_since")
    return f"{te_date(since, year=False)} నుండి" if since else f"గత {days} రోజులు"


def te_why(facts: dict) -> str:
    """The 'reason' line, built only from the global cross-check numbers."""
    if "global_gold_change_pct" not in facts:
        return ""
    gold = "తగ్గింది" if facts["global_gold_direction"] == "fell" else "పెరిగింది"
    rupee = "పెరిగింది" if facts["rupee_direction"] == "stronger" else "తగ్గింది"
    return (
        f"💡 కారణం: అంతర్జాతీయ మార్కెట్‌లో బంగారం ధర {facts['global_gold_change_pct']}% {gold}; "
        f"రూపాయి విలువ {facts['usd_inr_change_pct']}% {rupee}."
    )


def te_labels(facts: dict) -> list[str]:
    out = []
    if "low_30d" in facts["triggers"]:
        out.append("⭐ 30 రోజుల్లో అతి తక్కువ ధర")
    elif facts.get("d30_is_lowest") and facts.get("d30_since"):
        out.append(f"⭐ {te_date(facts['d30_since'], year=False)} నుండి అతి తక్కువ ధర")
    elif facts.get("d7_is_lowest"):
        since = facts.get("d7_since")
        out.append(f"⭐ {te_date(since, year=False)} నుండి అతి తక్కువ ధర" if since else "⭐ గత 7 రోజుల్లో అతి తక్కువ ధర")
    if "below_high" in facts["triggers"] and "month_high" in facts:
        out.append(f"⭐ ఈ నెల అత్యధిక ధర ₹{facts['month_high']} కంటే {facts['below_high_pct']}% తక్కువ")
    return out


def template_alert_te(facts: dict) -> str:
    lines = [
        "🟡 *బంగారం ధర తగ్గింది* 📉",
        f"IBJA · {te_date(facts['rate_date_iso'])}, {TE_SESSION[facts['session']]}",
        "",
        f"💰 *22 క్యారెట్:* గ్రాముకు ₹{facts['g22_now']}",
        f"      🔻 ₹{facts['g22_down_rs']} తగ్గింది (-{facts['g22_down_pct']}%) · ముందు ₹{facts['g22_prev']}",
        f"💰 *24 క్యారెట్:* గ్రాముకు ₹{facts['g24_now']}",
        f"      🔻 ₹{facts['g24_down_rs']} తగ్గింది (-{facts['g24_down_pct']}%) · ముందు ₹{facts['g24_prev']}",
    ]
    if "d7_low" in facts:
        spark = f"  {facts['d7_spark']}" if facts.get("d7_spark") else ""
        lines += [
            "",
            "📊 *22 క్యారెట్ ధరల తీరు*",
            f"{_te_window(facts, 'd7', 7)}: అతి తక్కువ ₹{facts['d7_low']} · అతి ఎక్కువ ₹{facts['d7_high']}{spark}",
        ]
        if facts["d30_label"] != facts["d7_label"]:
            lines.append(
                f"{_te_window(facts, 'd30', 30)}: అతి తక్కువ ₹{facts['d30_low']} · అతి ఎక్కువ ₹{facts['d30_high']}"
            )
    labels = te_labels(facts)
    why = te_why(facts)
    if labels or why:
        lines.append("")
        lines.extend(labels)
        if why:
            lines.append(why)
    lines += ["", TE_FOOTER]
    return "\n".join(lines)


def morning_hint_te(gl_inr_pct: float, last: Rate | None) -> str:
    lines = [
        "🌅 *ముందస్తు సూచన: ఈరోజు బంగారం ధర తగ్గే అవకాశం ఉంది*",
        "",
        f"🌍 అంతర్జాతీయ మార్కెట్‌లో బంగారం ధర (రూపాయల్లో) {abs(gl_inr_pct):.1f}% తగ్గింది.",
    ]
    if last is not None:
        lines.append(
            f"🏷️ చివరి IBJA 22 క్యారెట్ ధర: గ్రాముకు ₹{inr(last.g22)} "
            f"({te_date(last.date, year=False)}, {TE_SESSION_SHORT[last.session]})"
        )
    lines += [
        "",
        "⏰ IBJA (భారత బులియన్ మార్కెట్) అధికారిక ధర మధ్యాహ్నం సుమారు 12:05కి వస్తుంది. "
        "నిజంగా తగ్గితే, 12:20కి పూర్తి సమాచారం పంపుతాం.",
        "",
        "ℹ️ ఇది ముందస్తు సూచన మాత్రమే. అధికారిక ధర కాదు, కొనమని సలహా కాదు.",
    ]
    return "\n".join(lines)


def admin_note(now: dt.datetime, problem: str, family_sent: str, next_check: str | None = None) -> str:
    lines = [
        "[Gold bot - admin]",
        f"{now.strftime('%d %b %Y, %I:%M %p')} run",
        f"Problem: {problem}",
        f"Family message: {family_sent}",
    ]
    if next_check:
        lines.append(f"Next check: {next_check}")
    return "\n".join(lines)


def admin_info(now: dt.datetime, text: str) -> str:
    return f"[Gold bot - admin]\n{now.strftime('%d %b %Y, %I:%M %p')}\n{text}"


def next_check_label(now: dt.datetime) -> str:
    if now.weekday() < 5 and now.hour < 12:
        return "12:20 PM today"
    if now.weekday() < 5 and now.hour < 17:
        return "5:20 PM today"
    days = 1
    nxt = now + dt.timedelta(days=days)
    while nxt.weekday() >= 5:
        days += 1
        nxt = now + dt.timedelta(days=days)
    return f"12:20 PM {nxt.strftime('%a %d %b')}"
