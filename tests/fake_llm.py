"""A scripted stand-in for an OpenAI-compatible client, so agent logic is tested without API keys."""

import json
from types import SimpleNamespace


class _Msg(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        d = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in self.tool_calls
            ]
        return {k: v for k, v in d.items() if v is not None or not exclude_none}


def tool_call(name, args=None, i=0):
    return SimpleNamespace(id=f"call_{name}_{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args or {})))


def final(send, reason, message=""):
    return {"content": json.dumps({"send": send, "reason": reason, "message": message})}


class FakeClient:
    """`script` is a list of steps; each step is {"tool_calls": [...]} or {"content": "..."} or an Exception."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        step = self.script.pop(0) if self.script else {"content": ""}
        if callable(step):  # a step can be built from the request (e.g. reuse the template it was given)
            step = step(kwargs)
        if isinstance(step, Exception):
            raise step
        msg = _Msg(content=step.get("content"), tool_calls=step.get("tool_calls"))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
