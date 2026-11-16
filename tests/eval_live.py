"""Live agent evaluation: runs the REAL LLM (your Gemini/Groq keys) on fixed scenarios
and checks its send/don't-send decision and message safety.

    PYTHONPATH=src python tests/eval_live.py

Uses about 10-20 free-tier LLM calls. Not part of pytest (needs keys and network).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot import messages as M  # noqa: E402
from goldbot.agent.agent import AgentError, run_agent  # noqa: E402
from goldbot.agent.tools import ToolContext, make_executor  # noqa: E402
from goldbot.config import load_config, load_dotenv  # noqa: E402
from goldbot.masking import register_secret, setup_logging  # noqa: E402
from goldbot.models import CrossCheck, Rate  # noqa: E402
from goldbot.rules import evaluate  # noqa: E402
from goldbot.storage import Storage  # noqa: E402
from goldbot.validator import validate  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
AGREE = CrossCheck(True, -1.05, -1.0, -0.8, -0.2, 0.05, "moves agree")
DISAGREE = CrossCheck(True, -1.05, 2.1, 2.3, -0.2, 3.15, "moves disagree")
UNAVAILABLE = CrossCheck(False, note="no stored global price from the previous rate's time yet")

SCENARIOS = [
    # name, cross-check, rate is new?, expected send
    ("real drop, global agrees", AGREE, True, True),
    ("global price contradicts IBJA", DISAGREE, True, False),
    ("cross-check unavailable", UNAVAILABLE, True, True),
    ("rate is a stale repeat", AGREE, False, False),
]


def main() -> int:
    setup_logging()  # shows each provider call and tool call as it happens
    load_dotenv(ROOT / ".env")
    cfg = load_config(ROOT / "config.yaml")
    # Only the LLM keys matter here; contacts are not needed, so they are not parsed
    llm_keys = {p["name"]: os.environ[p["api_key_env"]].strip()
                for p in cfg["agent"]["providers"] if os.environ.get(p["api_key_env"], "").strip()}
    for key in llm_keys.values():
        register_secret(key)
    if not llm_keys:
        print("Set GEMINI_API_KEY and/or GROQ_API_KEY in .env first.")
        return 2
    passed = 0
    print(f"Running {len(SCENARIOS)} scenarios with the real LLM. Each can take 10-60 seconds...", flush=True)
    for i, (name, cc, is_new, expect_send) in enumerate(SCENARIOS, 1):
        print(f"\n[{i}/{len(SCENARIOS)}] {name}", flush=True)
        storage = Storage(":memory:")
        now = dt.datetime(2026, 11, 16, 12, 20, tzinfo=IST)
        storage.add_history([Rate(dt.date(2026, 11, 13), "PM", 135809, 124401)], now)
        cur = Rate(dt.date(2026, 11, 16), "AM", 134380, 123092)
        ev = evaluate(cur, storage, cfg["rules"])
        facts = M.build_facts(ev, cc)
        template = M.template_alert(facts)
        ctx = ToolContext(storage=storage, current=cur, previous=ev.previous, is_new=is_new, cross_check=cc)
        try:
            res = run_agent(facts, template, make_executor(ctx), cfg["agent"], llm_keys,
                            cfg["cross_check"]["max_disagreement_pp"])
        except AgentError as e:
            print(f"FAIL  {name}: all providers failed ({e})")
            continue
        problems = validate(res.message, facts, cfg["messages"], M.FOOTER) if res.send else []
        ok = res.send == expect_send and not problems
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: send={res.send} (expected {expect_send}), "
              f"{res.tool_calls} tool calls via {res.provider}; reason: {res.reason}")
        if problems:
            print("      validator:", "; ".join(problems))
        if res.send:
            print("      " + res.message.replace("\n", "\n      "))
    print(f"\n{passed}/{len(SCENARIOS)} scenarios passed")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    sys.exit(main())
