"""Checks the agent's message before it can reach the family.

Rules: every number must come from the facts, the key prices must be present,
no buy/sell advice, no links, and a sensible length. Any failure means the agent's
text is thrown away and the fixed template is sent instead.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ALWAYS_ALLOWED = {"22", "24", "999", "916", "3"}  # karats, purities, "3% GST"


def _norm(token: str) -> str:
    token = token.replace(",", "")
    if "." in token:
        token = token.rstrip("0").rstrip(".") or "0"
    return token


def _numbers_in(value: Any) -> set[str]:
    if isinstance(value, (list, tuple)):
        out: set[str] = set()
        for v in value:
            out |= _numbers_in(v)
        return out
    return {_norm(m) for m in _NUMBER.findall(str(value))}


def allowed_numbers(facts: dict, extra_text: str = "") -> set[str]:
    allowed = set(_ALWAYS_ALLOWED)
    for value in facts.values():
        allowed |= _numbers_in(value)
    allowed |= _numbers_in(extra_text)
    return allowed


def validate(message: str, facts: dict, msg_cfg: dict, footer: str) -> list[str]:
    """Returns a list of problems. Empty list = safe to send."""
    problems: list[str] = []
    text = (message or "").strip()
    if not text:
        return ["message is empty"]
    if len(text) > msg_cfg.get("max_length", 700):
        problems.append(f"message too long ({len(text)} chars)")
    lowered = text.lower()
    for phrase in msg_cfg.get("banned_phrases", []):
        if phrase.lower() in lowered:
            problems.append(f"contains banned phrase: {phrase!r}")
    if "http" in lowered or "www." in lowered:
        problems.append("contains a link")
    for key in ("g22_now", "g22_prev", "g24_now"):
        if facts[key] not in text:
            problems.append(f"missing required value {key}={facts[key]}")
    if "IBJA" not in text:
        problems.append("does not name IBJA as the source")
    if "not buying advice" not in lowered:
        problems.append("missing the 'not buying advice' line")
    allowed = allowed_numbers(facts, extra_text=footer)
    unknown = sorted({_norm(t) for t in _NUMBER.findall(text)} - allowed)
    if unknown:
        problems.append(f"numbers not in the facts: {', '.join(unknown)}")
    return problems
