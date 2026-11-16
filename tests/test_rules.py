import datetime as dt

from conftest import ist, rate

from goldbot.models import GlobalQuote
from goldbot.rules import cross_check, evaluate

NOW = ist("2026-11-16T12:20")


def _fill(storage, start="2026-10-20", days=20, g22=125000):
    d = dt.date.fromisoformat(start)
    rates = []
    for i in range(days):
        day = d + dt.timedelta(days=i)
        if day.weekday() < 5:
            rates += [rate(day.isoformat(), "AM", g22), rate(day.isoformat(), "PM", g22)]
    storage.add_history(rates, NOW)


def test_one_percent_drop_triggers(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 123092), storage, cfg["rules"])
    assert ev.should_alert and ev.triggers == ["daily_drop"]
    assert round(ev.change_pct, 2) == -1.05


def test_small_drop_does_not_trigger(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 123900), storage, cfg["rules"])  # -0.4%
    assert not ev.should_alert and "below the alert thresholds" in ev.skip_reason


def test_rise_never_alerts(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 126000), storage, cfg["rules"])
    assert not ev.should_alert and ev.triggers == []


def test_bad_data_move(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 110000), storage, cfg["rules"])  # -11.6%
    assert ev.bad_data and not ev.should_alert


def test_no_previous_rate(storage, cfg):
    ev = evaluate(rate("2026-11-16", "AM", 123092), storage, cfg["rules"])
    assert not ev.should_alert and "no previous" in ev.skip_reason


def test_thirty_day_low_needs_enough_history(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 124300), storage, cfg["rules"])
    assert "low_30d" not in ev.triggers  # only 1 point of history


def test_thirty_day_low_and_below_high(storage, cfg):
    _fill(storage, g22=125000)
    storage.add_history([rate("2026-11-10", "AM", 129000), rate("2026-11-13", "PM", 121700)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 121500), storage, cfg["rules"])  # tiny fall, but new low
    assert "low_30d" in ev.triggers and "below_high" in ev.triggers and "daily_drop" not in ev.triggers
    assert ev.should_alert
    assert round(ev.below_high_pct, 1) == 5.8


def test_same_rate_is_never_sent_twice(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    cur = rate("2026-11-16", "AM", 123092)
    storage.record_alert(cur, ["daily_drop"], NOW)
    ev = evaluate(cur, storage, cfg["rules"])
    assert not ev.should_alert and "already sent" in ev.skip_reason


def test_second_alert_same_day_needs_further_fall(storage, cfg):
    storage.add_history([rate("2026-11-13", "PM", 124401)], NOW)
    am = rate("2026-11-16", "AM", 123092)
    storage.save_today(am, NOW)
    storage.record_alert(am, ["daily_drop"], NOW)
    # PM falls 1.2% vs AM, but needs 1% below the AM alert price: 123092 * 0.99 = 121861
    pm_small = rate("2026-11-16", "PM", 121900)
    assert not evaluate(pm_small, storage, cfg["rules"]).should_alert
    pm_big = rate("2026-11-16", "PM", 121700)
    assert evaluate(pm_big, storage, cfg["rules"]).should_alert


def test_slow_fall_does_not_repeat_without_new_low(storage, cfg):
    _fill(storage, g22=125000)
    storage.add_history([rate("2026-11-12", "PM", 121000)], NOW)
    storage.record_alert(rate("2026-11-12", "PM", 121000), ["low_30d"], NOW)
    storage.add_history([rate("2026-11-13", "PM", 121400)], NOW)
    ev = evaluate(rate("2026-11-16", "AM", 121200), storage, cfg["rules"])  # 3.04% below high, no new low
    assert ev.triggers == ["below_high"]
    assert not ev.should_alert and "no new low" in ev.skip_reason


def _quote(when, inr, usd):
    return GlobalQuote(fetched_at=ist(when), inr_per_g=inr, usd_per_oz=usd, updated=0)


def test_cross_check_agrees(storage):
    prev, cur = rate("2026-11-13", "PM", 124401), rate("2026-11-16", "AM", 123092)
    storage.save_quote(_quote("2026-11-13T17:20", 12000.0, 4300.0))
    latest = _quote("2026-11-16T12:20", 11880.0, 4257.0)  # global -1.0%
    cc = cross_check(cur, prev, latest, storage, cur_tz(), 2.0)
    assert cc.available and cc.disagreement_pp < 0.2


def test_cross_check_disagrees(storage):
    prev, cur = rate("2026-11-13", "PM", 124401), rate("2026-11-16", "AM", 123092)
    storage.save_quote(_quote("2026-11-13T17:20", 12000.0, 4300.0))
    latest = _quote("2026-11-16T12:20", 12240.0, 4386.0)  # global +2%, IBJA -1.05%
    cc = cross_check(cur, prev, latest, storage, cur_tz(), 2.0)
    assert cc.available and cc.disagreement_pp > 2.0


def test_cross_check_unavailable_without_old_quote(storage):
    prev, cur = rate("2026-11-13", "PM", 124401), rate("2026-11-16", "AM", 123092)
    cc = cross_check(cur, prev, _quote("2026-11-16T12:20", 11880.0, 4257.0), storage, cur_tz(), 2.0)
    assert not cc.available


def cur_tz():
    from conftest import IST

    return IST
