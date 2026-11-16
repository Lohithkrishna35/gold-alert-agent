"""End-to-end runs of goldbot.main with the saved IBJA page, fake senders and a fake LLM."""

import json

import pytest
from conftest import FIXTURE, ROOT
from fake_llm import FakeClient, final, tool_call

import goldbot.agent.agent as agent_mod
import goldbot.main as main_mod
from goldbot.notifier.senders import ConsoleSender

ADMIN = {"name": "Lohith", "phone": "+919000000001", "apikey": "k1"}
FAMILY = [{"name": "Amma", "phone": "+919000000002", "apikey": "k2"},
          {"name": "Nanna", "phone": "+919000000003", "apikey": "k3"}]


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "API_NINJAS_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ADMIN_CONTACT", json.dumps(ADMIN))
    monkeypatch.setenv("FAMILY_CONTACTS", json.dumps(FAMILY))
    sender = ConsoleSender()
    monkeypatch.setattr(main_mod, "build_senders", lambda cfg, secrets: sender)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    return {"sender": sender, "db": str(tmp_path / "t.db"), "tmp": tmp_path}


def _run(env, *extra, html=FIXTURE, now="2026-11-16T12:20"):
    args = ["--config", str(ROOT / "config.yaml"), "--env-file", "/nonexistent", "--db", env["db"],
            "--html-file", str(html), "--now", now, *extra]
    return main_mod.main(args)


def _who(env):
    return [(c.name, text.splitlines()[0]) for c, text in env["sender"].sent]


def test_real_drop_reaches_family_and_admin_notes_stay_with_admin(env, capsys):
    assert _run(env) == 0
    who = _who(env)
    alerts = [n for n, first in who if "*Gold Price Dropped*" in first]
    admin_notes = [n for n, first in who if first.startswith("[Gold bot - admin]")]
    assert alerts == ["Lohith", "Amma", "Nanna"]
    assert admin_notes == ["Lohith"]  # "no LLM key / not cross-checked" note: admin only


def test_second_run_same_rate_sends_nothing(env, capsys):
    _run(env)
    env["sender"].sent.clear()
    assert _run(env, now="2026-11-16T12:50") == 0
    assert env["sender"].sent == []


def test_weekend_does_nothing(env, capsys):
    assert _run(env, now="2026-11-15T12:20") == 0
    assert env["sender"].sent == []


def test_ibja_layout_change_goes_to_admin_only(env, capsys):
    broken = env["tmp"] / "broken.html"
    broken.write_text(FIXTURE.read_text().replace("lblGold916_AM", "x"))
    assert _run(env, html=broken) == 1
    assert [n for n, _ in _who(env)] == ["Lohith"]


def test_pm_not_published_goes_to_admin_only(env, capsys):
    assert _run(env, now="2026-11-16T17:20") == 0  # fixture has no PM rate
    who = _who(env)
    assert [n for n, _ in who] == ["Lohith"]
    assert "not published" in env["sender"].sent[0][1]


def test_agent_written_message_is_used(env, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "g")

    def write_from_template(kw):
        # build a valid message from the template the agent was given
        template = kw["messages"][1]["content"].split("TEMPLATE")[1].split(":\n", 1)[1].strip()
        return final(True, "fresh and plausible", template.replace("*Gold Price Dropped*", "*Gold Price Dropped Today*"))

    factory = lambda b, k, t: FakeClient([{"tool_calls": [tool_call("get_rate_freshness")]}, write_from_template])  # noqa: E731
    monkeypatch.setattr(main_mod, "run_agent",
                        lambda *a, **k: agent_mod.run_agent(*a, **k, client_factory=factory))
    assert _run(env) == 0
    first_lines = [t.splitlines()[0] for c, t in env["sender"].sent if c.name == "Amma"]
    assert first_lines == ["🟡 *Gold Price Dropped Today* 📉"]


def test_agent_invented_number_falls_back_to_template(env, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    bad = "*Gold Price Dropped* (IBJA rate)\n22K: Rs 12,309/g (previous Rs 12,440)\n24K: Rs 13,438/g\nSaves you Rs 5,000!\nIBJA. Info only, not buying advice."
    factory = lambda b, k, t: FakeClient([final(True, "ok", bad)])  # noqa: E731
    monkeypatch.setattr(main_mod, "run_agent", lambda *a, **k: agent_mod.run_agent(*a, **k, client_factory=factory))
    assert _run(env) == 0
    family_text = [t for c, t in env["sender"].sent if c.name == "Amma"][0]
    assert "5,000" not in family_text and "🔻 Rs 131" in family_text
    admin_texts = [t for c, t in env["sender"].sent if c.name == "Lohith" and t.startswith("[Gold bot")]
    assert "failed checks" in admin_texts[0]


def test_agent_hold_sends_nothing_to_family(env, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    factory = lambda b, k, t: FakeClient([final(False, "global price contradicts IBJA")])  # noqa: E731
    monkeypatch.setattr(main_mod, "run_agent", lambda *a, **k: agent_mod.run_agent(*a, **k, client_factory=factory))
    assert _run(env) == 0
    assert [n for n, _ in _who(env)] == ["Lohith"]


def test_missing_admin_secret_stops_safely(env, monkeypatch, capsys):
    monkeypatch.delenv("ADMIN_CONTACT")
    assert _run(env) == 2
    assert env["sender"].sent == []


def test_admin_in_family_list_is_rejected(env, monkeypatch, capsys):
    monkeypatch.setenv("FAMILY_CONTACTS", json.dumps(FAMILY + [ADMIN]))
    assert _run(env) == 2
