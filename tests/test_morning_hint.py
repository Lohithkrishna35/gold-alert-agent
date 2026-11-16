"""9:30 AM early hint: only on a real global drop, once a day, clearly labelled, errors to admin only."""

import json

import pytest
from conftest import ROOT, ist, rate

import goldbot.main as main_mod
from goldbot.models import GlobalQuote
from goldbot.notifier.senders import ConsoleSender
from goldbot.storage import Storage

ADMIN = {"name": "Lohith", "phone": "+919000000001", "apikey": "k1"}
FAMILY = [{"name": "Amma", "phone": "+919000000002", "apikey": "k2"}]
DAY0 = 1794700000  # an earlier settlement (unix seconds)
DAY1 = DAY0 + 86400


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ADMIN_CONTACT", json.dumps(ADMIN))
    monkeypatch.setenv("FAMILY_CONTACTS", json.dumps(FAMILY))
    monkeypatch.setenv("API_NINJAS_KEY", "ninja")
    sender = ConsoleSender()
    monkeypatch.setattr(main_mod, "build_senders", lambda cfg, secrets: sender)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    db = str(tmp_path / "t.db")
    s = Storage(db)  # yesterday's stored settlement + last IBJA rate
    s.save_quote(GlobalQuote(fetched_at=ist("2026-11-16T17:20"), inr_per_g=12000.0, usd_per_oz=4300.0, updated=DAY0))
    s.add_history([rate("2026-11-16", "PM", 123092, 134380)], ist("2026-11-16T17:20"))
    s.close()
    return {"sender": sender, "db": db, "mp": monkeypatch}


def _global_today(env, inr, usd, updated=DAY1, fail=False):
    def fake(cfg, key, now, timeout=20):
        if fail:
            raise RuntimeError("down")
        return GlobalQuote(fetched_at=now, inr_per_g=inr, usd_per_oz=usd, updated=updated)
    env["mp"].setattr(main_mod.global_price, "fetch_quote", fake)


def _run(env, now="2026-11-17T09:30", config=None):
    return main_mod.main(["--config", str(config or ROOT / "config.yaml"), "--env-file", "/nonexistent",
                          "--db", env["db"], "--now", now])


def test_big_global_drop_sends_hint_to_everyone(env, capsys):
    _global_today(env, inr=11850.0, usd=4250.0)  # -1.25% in rupees
    env["mp"].setattr(main_mod.time, "time", lambda: DAY1 + 3600)
    assert _run(env) == 0
    sent = env["sender"].sent
    assert [c.name for c, _ in sent] == ["Lohith", "Amma"]
    text = sent[0][1]
    assert text.startswith("🌅 *Early hint") and "fell 1.2%" in text and "not an official price" in text
    assert "Last IBJA 22K: Rs 12,309/g (16 Nov PM)" in text


def test_small_global_move_sends_nothing(env, capsys):
    _global_today(env, inr=11950.0, usd=4290.0)  # -0.4%
    assert _run(env) == 0
    assert env["sender"].sent == []


def test_global_rise_sends_nothing(env, capsys):
    _global_today(env, inr=12200.0, usd=4360.0)
    assert _run(env) == 0
    assert env["sender"].sent == []


def test_only_one_hint_per_day(env, capsys):
    _global_today(env, inr=11850.0, usd=4250.0)
    _run(env)
    env["sender"].sent.clear()
    _run(env, now="2026-11-17T09:45")
    assert env["sender"].sent == []


def test_same_settlement_as_yesterday_sends_nothing(env, capsys):
    _global_today(env, inr=11850.0, usd=4250.0, updated=DAY0)  # provider has no new session yet
    assert _run(env) == 0
    assert env["sender"].sent == []


def test_global_api_failure_goes_to_admin_only(env, capsys):
    _global_today(env, 0, 0, fail=True)
    assert _run(env) == 0
    assert [c.name for c, _ in env["sender"].sent] == ["Lohith"]
    assert "[Gold bot - admin]" in env["sender"].sent[0][1]


def test_family_can_be_excluded(env, tmp_path, capsys):
    cfg_text = (ROOT / "config.yaml").read_text().replace("send_to_family: true", "send_to_family: false")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(cfg_text)
    _global_today(env, inr=11850.0, usd=4250.0)
    _run(env, config=cfg)
    assert [c.name for c, _ in env["sender"].sent] == ["Lohith"]


def test_no_hint_on_weekends(env, capsys):
    _global_today(env, inr=11000.0, usd=4000.0)
    assert _run(env, now="2026-11-21T09:30") == 0  # Saturday
    assert env["sender"].sent == []


def test_hint_never_triggers_ibja_check(env, monkeypatch, capsys):
    _global_today(env, inr=11850.0, usd=4250.0)
    monkeypatch.setattr(main_mod.ibja, "fetch_html", lambda *a, **k: pytest.fail("IBJA must not be read at 9:30"))
    assert _run(env) == 0
