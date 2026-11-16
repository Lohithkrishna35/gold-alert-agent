from conftest import ist, rate

from goldbot import messages as M
from goldbot.models import CrossCheck
from goldbot.rules import evaluate
from goldbot.validator import validate


def _facts(storage, cfg, cc=None):
    storage.add_history([rate("2026-11-13", "PM", 124401, 135809)], ist("2026-11-16T12:20"))
    ev = evaluate(rate("2026-11-16", "AM", 123092, 134380), storage, cfg["rules"])
    cc = cc or CrossCheck(available=False, note="test")
    return M.build_facts(ev, cc)


def test_indian_number_format():
    assert M.inr(12309.2) == "12,309"
    assert M.inr(123092) == "1,23,092"
    assert M.inr(999) == "999"
    assert M.inr(10000000) == "1,00,00,000"


def test_template_numbers_match_ibja(storage, cfg):
    facts = _facts(storage, cfg)
    text = M.template_alert(facts)
    assert "💰 *22K:* Rs 12,309/g" in text
    assert "🔻 Rs 131 (-1.1%) from Rs 12,440" in text
    assert "💰 *24K:* Rs 13,438/g" in text
    assert "not buying advice" in text


def test_template_always_passes_validation(storage, cfg):
    facts = _facts(storage, cfg)
    assert validate(M.template_alert(facts), facts, cfg["messages"], M.FOOTER) == []


def test_invented_number_is_rejected(storage, cfg):
    facts = _facts(storage, cfg)
    bad = M.template_alert(facts).replace("🔻 Rs 131", "🔻 Rs 150")
    problems = validate(bad, facts, cfg["messages"], M.FOOTER)
    assert any("150" in p for p in problems)


def test_buying_advice_is_rejected(storage, cfg):
    facts = _facts(storage, cfg)
    bad = M.template_alert(facts) + "\nBuy now before it rises!"
    assert any("banned" in p for p in validate(bad, facts, cfg["messages"], M.FOOTER))


def test_link_is_rejected(storage, cfg):
    facts = _facts(storage, cfg)
    bad = M.template_alert(facts) + "\nDetails: https://example.com"
    assert any("link" in p for p in validate(bad, facts, cfg["messages"], M.FOOTER))


def test_missing_price_is_rejected(storage, cfg):
    facts = _facts(storage, cfg)
    bad = M.template_alert(facts).replace("12,309", "")
    assert any("g22_now" in p for p in validate(bad, facts, cfg["messages"], M.FOOTER))


def test_why_line_with_fact_numbers_is_allowed(storage, cfg):
    cc = CrossCheck(available=True, ibja_change_pct=-1.05, global_change_pct=-1.0,
                    global_usd_change_pct=-0.8, usd_inr_change_pct=-0.2, disagreement_pp=0.05)
    facts = _facts(storage, cfg, cc)
    text = M.template_alert(facts).replace(
        "\n\nℹ️ IBJA", "\n💡 Why: international gold fell 0.8%; rupee 0.2% stronger.\n\nℹ️ IBJA"
    )
    assert "💡 Why" in text
    assert validate(text, facts, cfg["messages"], M.FOOTER) == []



def _rates(storage):
    from conftest import rate as r
    days = ["2026-10-19", "2026-10-27", "2026-11-03", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
    vals = [129000, 128000, 127500, 130457, 128792, 126126, 124401]
    rows = [r(d, "PM", v) for d, v in zip(days, vals)]
    storage.add_history(rows, ist("2026-11-16T12:20"))
    return storage.all_rates()


def test_trend_section_full_history(storage, cfg):
    all_rates = _rates(storage)
    ev = evaluate(rate("2026-11-16", "AM", 123092, 134380), storage, cfg["rules"])
    facts = M.build_facts(ev, CrossCheck(available=False), all_rates)
    assert facts["d7_label"] == "Last 7 days" and facts["d30_label"] == "Last 30 days"
    assert facts["d7_low"] == "12,309" and facts["d7_high"] == "13,046"
    assert facts["d30_high"] == "13,046"
    assert len(facts["d7_spark"]) == 5 and facts["d7_spark"][-1] == "▁"
    text = M.template_alert(facts)
    assert "📊 *22K trend*" in text and "Last 30 days: low Rs 12,309 · high Rs 13,046" in text
    assert "⭐ Lowest in the last 7 days" in text
    assert validate(text, facts, cfg["messages"], M.FOOTER) == []


def test_trend_is_honest_about_short_history(storage, cfg):
    storage.add_history([rate("2026-11-12", "PM", 126126), rate("2026-11-13", "PM", 124401)], ist("2026-11-16T12:20"))
    ev = evaluate(rate("2026-11-16", "AM", 123092, 134380), storage, cfg["rules"])
    facts = M.build_facts(ev, CrossCheck(available=False), storage.all_rates())
    assert facts["d30_label"] == "Since 12 Nov" and facts["d7_label"] == "Since 12 Nov"  # 10-11 Nov missing
    text = M.template_alert(facts)
    assert "⭐ Lowest since 12 Nov" in text
    assert text.count("Since 12 Nov:") == 1  # same window: shown once, not twice
