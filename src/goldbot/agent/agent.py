"""Single tool-calling agent over any OpenAI-compatible API (Gemini first, Groq as backup).

Guardrails: limited tool calls, timeout, temperature 0, strict JSON output,
and the caller validates the message before anything is sent.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from ..models import AgentResult
from .prompts import SYSTEM_PROMPT, USER_PROMPT
from .tools import TOOL_SCHEMAS

log = logging.getLogger(__name__)

ClientFactory = Callable[[str, str, float], Any]


def default_client_factory(base_url: str, api_key: str, timeout: float) -> Any:
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)


class AgentError(Exception):
    pass


def _parse_final(content: str | None) -> dict[str, Any]:
    if not content:
        raise AgentError("empty final answer")
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise AgentError("final answer is not JSON")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("send"), bool):
        raise AgentError("final JSON must have a boolean 'send'")
    data.setdefault("reason", "")
    data.setdefault("message", "")
    if not isinstance(data["message"], str) or not isinstance(data["reason"], str):
        raise AgentError("'message' and 'reason' must be strings")
    return data


def _assistant_dict(msg: Any) -> dict[str, Any]:
    # model_dump keeps provider extras (e.g. Gemini thought signatures) needed on the next turn
    if hasattr(msg, "model_dump"):
        d = msg.model_dump(exclude_none=True)
    else:
        d = dict(msg)
    d["role"] = "assistant"
    d.setdefault("content", "")
    return d


def run_with_provider(
    client: Any,
    model: str,
    facts: dict,
    template: str,
    execute_tool: Callable[[str, str], str],
    agent_cfg: dict,
    max_disagreement_pp: float,
    provider_cfg: dict | None = None,
) -> AgentResult:
    provider_cfg = provider_cfg or {}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(max_disagreement_pp=max_disagreement_pp)},
        {"role": "user", "content": USER_PROMPT.format(facts_json=json.dumps(facts, indent=2), template=template)},
    ]
    max_calls = int(agent_cfg.get("max_tool_calls", 5))
    used = 0
    # one extra round so the model can answer after its last allowed tool call
    for _ in range(max_calls + 2):
        allow_tools = used < max_calls
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        # a provider can override the temperature, or set it to null to use the model's default
        temperature = provider_cfg.get("temperature", agent_cfg.get("temperature", 0))
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs.update(provider_cfg.get("extra") or {})  # e.g. reasoning_effort: low
        if allow_tools:
            kwargs["tools"] = TOOL_SCHEMAS
            kwargs["tool_choice"] = "auto"
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if calls and allow_tools:
            messages.append(_assistant_dict(msg))
            for call in calls:
                used += 1
                name = call.function.name
                log.info("agent tool call %d: %s", used, name)
                result = (
                    execute_tool(name, call.function.arguments)
                    if used <= max_calls
                    else json.dumps({"error": "tool call limit reached; answer now"})
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            continue
        data = _parse_final(getattr(msg, "content", None))
        return AgentResult(
            send=data["send"], reason=data["reason"].strip(), message=data["message"].strip(), tool_calls=used
        )
    raise AgentError("agent did not finish within the step limit")


def run_agent(
    facts: dict,
    template: str,
    execute_tool: Callable[[str, str], str],
    agent_cfg: dict,
    llm_keys: dict[str, str],
    max_disagreement_pp: float,
    client_factory: ClientFactory = default_client_factory,
) -> AgentResult:
    """Try each configured provider in order. Raises AgentError if all fail."""
    errors: list[str] = []
    for p in agent_cfg.get("providers", []):
        key = llm_keys.get(p["name"])
        if not key:
            errors.append(f"{p['name']}: no API key")
            continue
        try:
            client = client_factory(p["base_url"], key, float(agent_cfg.get("timeout_seconds", 60)))
            log.info("agent: asking %s (%s)", p["name"], p["model"])
            result = run_with_provider(
                client, p["model"], facts, template, execute_tool, agent_cfg, max_disagreement_pp, p
            )
            result.provider = p["name"]
            result.errors = errors
            return result
        except Exception as e:  # any provider failure -> try the next one
            log.warning("agent provider %s failed: %s", p["name"], type(e).__name__)
            errors.append(f"{p['name']}: {type(e).__name__}: {str(e)[:150]}")
    raise AgentError("; ".join(errors) or "no providers configured")
