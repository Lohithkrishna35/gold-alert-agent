"""Drop detection. Plain Python: exact maths, no AI involved."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .models import CrossCheck, Evaluation, GlobalQuote, Rate, SESSION_PUBLISH_TIME
from .storage import Storage


def pct_change(new: float, old: float) -> float:
    return (new - old) / old * 100


def evaluate(current: Rate, storage: Storage, rules: dict[str, Any]) -> Evaluation:
    ev = Evaluation(current=current, previous=storage.previous_rate(current))
    if ev.previous is None:
        ev.skip_reason = "no previous rate to compare with yet"
        return ev

    ev.change_pct = pct_change(current.g22_10g, ev.previous.g22_10g)
    if abs(ev.change_pct) > rules["bad_data_move_pct"]:
        ev.bad_data = True
        ev.skip_reason = f"22K moved {ev.change_pct:+.2f}% in one step; treated as bad data"
        return ev
    if ev.change_pct >= 0:
        ev.skip_reason = "price rose or did not change"
        return ev

    window = storage.window(current, rules["low_window_days"])
    ev.window_points = len(window)
    if ev.change_pct <= -rules["daily_drop_pct"]:
        ev.triggers.append("daily_drop")
    if window and len(window) >= rules["min_history_points"]:
        ev.window_low = min(window, key=lambda r: (r.g22_10g, r.sort_key))
        ev.window_high = max(window, key=lambda r: (r.g22_10g, r.sort_key))
        if current.g22_10g < ev.window_low.g22_10g:
            ev.triggers.append("low_30d")
        ev.below_high_pct = -pct_change(current.g22_10g, ev.window_high.g22_10g)
        if ev.below_high_pct >= rules["below_high_pct"]:
            ev.triggers.append("below_high")

    if not ev.triggers:
        ev.skip_reason = f"drop of {ev.change_pct:.2f}% is below the alert thresholds"
        return ev

    # ---- no-spam rules ----
    if storage.alert_exists(current):
        ev.skip_reason = "alert for this rate was already sent"
        return ev
    today = storage.alerts_on(current.date)
    if today:
        first = today[0]
        if len(today) >= 2:
            ev.skip_reason = "already sent 2 alerts today"
            return ev
        needed = first["g22_10g"] * (1 - rules["second_alert_extra_pct"] / 100)
        if current.g22_10g > needed:
            ev.skip_reason = "second alert today needs a further fall"
            return ev
    elif "daily_drop" not in ev.triggers:
        # 30-day triggers can stay true for days in a slow fall; only repeat on a new low since the last alert
        last = storage.last_alert()
        if last is not None:
            last_date = dt.date.fromisoformat(last["date"])
            in_window = (current.date - last_date).days <= rules["low_window_days"]
            if in_window and current.g22_10g >= last["g22_10g"]:
                ev.skip_reason = "no new low since the last alert"
                return ev

    ev.should_alert = True
    return ev


def publish_moment(rate: Rate, tz: dt.tzinfo) -> dt.datetime:
    return dt.datetime.combine(rate.date, SESSION_PUBLISH_TIME[rate.session], tzinfo=tz)


def cross_check(
    current: Rate,
    previous: Rate | None,
    latest_quote: GlobalQuote | None,
    storage: Storage,
    tz: dt.tzinfo,
    max_disagreement_pp: float,
) -> CrossCheck:
    """Compare IBJA's move with the international gold move over the same period."""
    if previous is None:
        return CrossCheck(available=False, note="no previous IBJA rate")
    if latest_quote is None:
        return CrossCheck(available=False, note="global price not available this run")
    # The quote we had stored around the time the previous IBJA rate was published (+30 min of slack)
    before = storage.quote_at_or_before(publish_moment(previous, tz) + dt.timedelta(minutes=30))
    if before is None:
        return CrossCheck(available=False, note="no stored global price from the previous rate's time yet")

    ibja = pct_change(current.g22_10g, previous.g22_10g)
    gl_inr = pct_change(latest_quote.inr_per_g, before.inr_per_g)
    gl_usd = pct_change(latest_quote.usd_per_oz, before.usd_per_oz)
    fx = pct_change(latest_quote.usd_inr, before.usd_inr)
    diff = abs(ibja - gl_inr)
    note = "moves agree" if diff <= max_disagreement_pp else "moves disagree"
    return CrossCheck(
        available=True,
        ibja_change_pct=ibja,
        global_change_pct=gl_inr,
        global_usd_change_pct=gl_usd,
        usd_inr_change_pct=fx,
        disagreement_pp=diff,
        note=note,
    )
