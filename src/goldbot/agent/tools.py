"""Read-only tools the agent can call. None of them can send messages or see secrets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ..models import CrossCheck, Rate
from ..storage import Storage

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_rate_freshness",
            "description": "Date and session (AM/PM) of the current IBJA rate, the previous rate, and whether the current rate is new.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": "Past official IBJA rates (rupees per gram, 22K and 24K), oldest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How many calendar days back (1-60)."}
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_global_cross_check",
            "description": "Compares the IBJA move with the international gold price move over the same period.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


@dataclass
class ToolContext:
    storage: Storage
    current: Rate
    previous: Rate | None
    is_new: bool
    cross_check: CrossCheck


def _rate_dict(r: Rate) -> dict[str, Any]:
    return {"date": r.date.isoformat(), "session": r.session, "g22_per_g": r.g22, "g24_per_g": r.g24}


def make_executor(ctx: ToolContext) -> Callable[[str, str], str]:
    def get_rate_freshness() -> dict:
        return {
            "current": {"date": ctx.current.date.isoformat(), "session": ctx.current.session},
            "previous": (
                {"date": ctx.previous.date.isoformat(), "session": ctx.previous.session} if ctx.previous else None
            ),
            "is_new": ctx.is_new,
        }

    def get_price_history(days: int = 30) -> dict:
        days = max(1, min(int(days), 60))
        return {"rates": [_rate_dict(r) for r in ctx.storage.history(days, until=ctx.current)]}

    def get_global_cross_check() -> dict:
        return ctx.cross_check.as_dict()

    registry: dict[str, Callable[..., dict]] = {
        "get_rate_freshness": get_rate_freshness,
        "get_price_history": get_price_history,
        "get_global_cross_check": get_global_cross_check,
    }

    def execute(name: str, arguments_json: str) -> str:
        fn = registry.get(name)
        if fn is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict):
                args = {}
            if name != "get_price_history":
                args = {}
            return json.dumps(fn(**args))
        except Exception as e:  # tool errors go back to the model, never crash the run
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    return execute
