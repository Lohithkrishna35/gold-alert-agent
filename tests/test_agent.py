"""Agent behaviour with a scripted fake LLM: tool use, decisions, limits, and provider fallback."""

import json

import pytest
from conftest import ist, rate
from fake_llm import FakeClient, final, tool_call

from goldbot import messages as M
from goldbot.agent.agent import AgentError, run_agent
from goldbot.agent.tools import ToolContext, make_executor
from goldbot.models import CrossCheck
from goldbot.rules import evaluate

KEYS = {"gemini": "g-key", "groq": "q-key"}
FIRST, SECOND = "groq", "gemini"  # order in config.yaml: Groq is the main AI, Gemini the backup


def _setup(storage, cfg, cc=None):
    storage.add_history([rate("2026-11-13", "PM", 124401, 135809)], ist("2026-11-16T12:20"))
    cur = rate("2026-11-16", "AM", 123092, 134380)
    ev = evaluate(cur, storage, cfg["rules"])
    cc = cc or CrossCheck(available=True, ibja_change_pct=-1.05, global_change_pct=-1.0,
                          global_usd_change_pct=-0.8, usd_inr_change_pct=-0.2, disagreement_pp=0.05, note="moves agree")
    facts = M.build_facts(ev, cc)
    ctx = ToolContext(storage=storage, current=cur, previous=ev.previous, is_new=True, cross_check=cc)
    return facts, M.template_alert(facts), make_executor(ctx)


def _factory(clients):
    """Hands out one fake client per provider, in order."""
    it = iter(clients)
    return lambda base_url, key, timeout: next(it)


def test_agent_verifies_then_writes(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    msg = template.replace("\n\nℹ️ IBJA", "\n💡 Why: international gold fell 0.8%; rupee 0.2% stronger.\n\nℹ️ IBJA")
    client = FakeClient([
        {"tool_calls": [tool_call("get_rate_freshness"), tool_call("get_global_cross_check", i=1)]},
        final(True, "fresh rate, global agrees", msg),
    ])
    res = run_agent(facts, template, execute, cfg["agent"], KEYS, 2.0, _factory([client]))
    assert res.send and res.provider == FIRST and res.tool_calls == 2
    assert "Why:" in res.message
    # the tool results the model saw were real data
    tool_msgs = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["is_new"] is True
    assert json.loads(tool_msgs[1]["content"])["note"] == "moves agree"


def test_agent_can_hold_the_alert(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    client = FakeClient([{"tool_calls": [tool_call("get_global_cross_check")]}, final(False, "global price rose")])
    res = run_agent(facts, template, execute, cfg["agent"], KEYS, 2.0, _factory([client]))
    assert res.send is False and "rose" in res.reason


def test_tool_call_limit_is_enforced(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    looping = [{"tool_calls": [tool_call("get_price_history", {"days": 30}, i)]} for i in range(10)]
    client = FakeClient(looping[:5] + [final(True, "ok", template)])
    res = run_agent(facts, template, execute, cfg["agent"], KEYS, 2.0, _factory([client]))
    assert res.tool_calls == cfg["agent"]["max_tool_calls"]
    assert "tools" not in client.requests[-1]  # last request offered no tools


def test_falls_back_to_second_provider(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    broken = FakeClient([RuntimeError("429 rate limited")])
    backup = FakeClient([final(True, "ok", template)])
    res = run_agent(facts, template, execute, cfg["agent"], KEYS, 2.0, _factory([broken, backup]))
    assert res.provider == SECOND and res.errors and FIRST in res.errors[0]


def test_bad_json_counts_as_failure(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    a = FakeClient([{"content": "Sure! The price dropped."}])
    b = FakeClient([{"content": "```json\n{\"send\": true, \"reason\": \"ok\", \"message\": \"hi\"}\n```"}])
    res = run_agent(facts, template, execute, cfg["agent"], KEYS, 2.0, _factory([a, b]))
    assert res.provider == SECOND and res.message == "hi"


def test_all_providers_down_raises(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    with pytest.raises(AgentError):
        run_agent(facts, template, execute, cfg["agent"], KEYS, 2.0,
                  _factory([FakeClient([TimeoutError()]), FakeClient([TimeoutError()])]))


def test_missing_keys_skip_provider(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    res = run_agent(facts, template, execute, cfg["agent"], {SECOND: "k"}, 2.0,
                    _factory([FakeClient([final(True, "ok", template)])]))
    assert res.provider == SECOND and f"{FIRST}: no API key" in res.errors[0]


def test_tools_are_read_only_and_bounded(storage, cfg):
    _, _, execute = _setup(storage, cfg)
    assert "error" in json.loads(execute("send_whatsapp", "{}"))
    hist = json.loads(execute("get_price_history", json.dumps({"days": 9999})))
    assert isinstance(hist["rates"], list)
    assert "error" in json.loads(execute("get_price_history", "{not json"))


def test_provider_settings_reach_the_api(storage, cfg):
    facts, template, execute = _setup(storage, cfg)
    client = FakeClient([final(True, "ok", template)])
    run_agent(facts, template, execute, cfg["agent"], {"gemini": "g"}, 2.0, _factory([client]))
    sent = client.requests[0]
    assert sent["reasoning_effort"] == "low"
    assert "temperature" not in sent  # gemini: temperature null -> model default
