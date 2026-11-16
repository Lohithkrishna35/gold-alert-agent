"""Telugu messages for elders: same numbers as English, reason line, IBJA explained, chosen per person."""

import json

import pytest
from conftest import ist, rate

from goldbot import messages as M
from goldbot.config import ConfigError, Contact, load_secrets
from goldbot.models import CrossCheck, Rate
from goldbot.notifier.router import Router
from goldbot.notifier.senders import ConsoleSender
from goldbot.rules import evaluate

AGREE = CrossCheck(True, -1.05, -1.0, -0.8, -0.2, 0.05, "moves agree")


def _facts(storage, cfg, cc=AGREE):
    storage.add_history([rate("2026-11-06", "PM", 129570), rate("2026-11-12", "PM", 126126),
                         rate("2026-11-13", "PM", 124401, 135809)], ist("2026-11-16T12:20"))
    ev = evaluate(rate("2026-11-16", "AM", 123092, 134380), storage, cfg["rules"])
    return M.build_facts(ev, cc, storage.all_rates())


def test_telugu_alert_has_same_numbers_as_english(storage, cfg):
    facts = _facts(storage, cfg)
    te, en = M.template_alert_te(facts), M.template_alert(facts)
    for value in ("12,309", "12,440", "131", "13,438", "13,581", "143", "1.1"):
        assert value in te and value in en
    assert "బంగారం ధర తగ్గింది" in te
    assert "16 నవంబర్ 2026" in te and "మధ్యాహ్నం" in te


def test_telugu_alert_explains_ibja_and_reason(storage, cfg):
    te = M.template_alert_te(_facts(storage, cfg))
    assert "IBJA = భారత బులియన్ మార్కెట్ అధికారిక ధర" in te
    assert "💡 కారణం: అంతర్జాతీయ మార్కెట్‌లో బంగారం ధర 0.8% తగ్గింది" in te
    assert "రూపాయి విలువ 0.2% పెరిగింది" in te  # rupee stronger = its value went up
    assert "కొనమని సలహా కాదు" in te  # "not buying advice"


def test_telugu_reason_is_left_out_without_global_data(storage, cfg):
    te = M.template_alert_te(_facts(storage, cfg, CrossCheck(available=False)))
    assert "కారణం" not in te


def test_telugu_trend_and_labels(storage, cfg):
    te = M.template_alert_te(_facts(storage, cfg))
    assert "📊 *22 క్యారెట్ ధరల తీరు*" in te
    assert "6 నవంబర్ నుండి అతి తక్కువ ధర" in te


def test_telugu_morning_hint():
    te = M.morning_hint_te(-1.25, Rate(dt_date("2026-11-16"), "PM", 134380, 123092))
    assert "ముందస్తు సూచన" in te and "1.2%" in te and "₹12,309" in te
    assert "అధికారిక ధర కాదు" in te  # "not an official price"


def test_each_person_gets_their_language(capsys):
    s = ConsoleSender()
    admin = Contact("Lohith", "+919000000001", "k1")
    amma = Contact("Amma", "+919000000002", "k2", language="te")
    r = Router(admin, [amma], s, retry_waits=[], gap_seconds=0, sleep=lambda x: None)
    r.send_drop_alert({"en": "ENGLISH", "te": "TELUGU"})
    r.send_hint({"en": "HINT-EN", "te": "HINT-TE"}, include_family=True)
    r.send_admin("ERROR")
    assert [(c.name, t) for c, t in s.sent] == [
        ("Lohith", "ENGLISH"), ("Amma", "TELUGU"), ("Lohith", "HINT-EN"), ("Amma", "HINT-TE"), ("Lohith", "ERROR"),
    ]


def test_language_in_contacts(monkeypatch, cfg):
    monkeypatch.setenv("ADMIN_CONTACT", json.dumps({"name": "L", "phone": "+919000000001", "apikey": "k"}))
    monkeypatch.setenv("FAMILY_CONTACTS", json.dumps([
        {"name": "Amma", "phone": "+919000000002", "apikey": "k2", "language": "te"}]))
    s = load_secrets(cfg)
    assert s.admin.language == "en" and s.family[0].language == "te"
    monkeypatch.setenv("FAMILY_CONTACTS", json.dumps([
        {"name": "Amma", "phone": "+919000000002", "apikey": "k2", "language": "hindi"}]))
    with pytest.raises(ConfigError, match="te = Telugu"):
        load_secrets(cfg)


def dt_date(s):
    import datetime as dt
    return dt.date.fromisoformat(s)



def test_preview_goes_to_admin_only_and_is_marked(monkeypatch, tmp_path, capsys):
    import goldbot.main as main_mod
    from conftest import ROOT

    monkeypatch.setenv("ADMIN_CONTACT", json.dumps({"name": "Lohith", "phone": "+919000000001", "apikey": "k"}))
    monkeypatch.setenv("FAMILY_CONTACTS", json.dumps([{"name": "Amma", "phone": "+919000000002", "apikey": "k2",
                                                       "language": "te"}]))
    sender = ConsoleSender()
    monkeypatch.setattr(main_mod, "build_senders", lambda cfg, secrets: sender)
    assert main_mod.main(["--config", str(ROOT / "config.yaml"), "--env-file", "/nonexistent",
                          "--db", str(tmp_path / "x.db"), "--preview", "te"]) == 0
    assert [c.name for c, _ in sender.sent] == ["Lohith", "Lohith"]
    alert, hint = sender.sent[0][1], sender.sent[1][1]
    assert alert.startswith("🧪 TEST PREVIEW") and "బంగారం ధర తగ్గింది" in alert and "💡 కారణం" in alert
    assert hint.startswith("🧪 TEST PREVIEW") and "ముందస్తు సూచన" in hint
