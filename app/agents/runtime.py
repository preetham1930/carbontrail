"""Run ADK agents and translate their events into a UI-friendly trace."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Awaitable, Callable, Optional

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

Emit = Callable[[dict], Awaitable[None]]


def _short(v: Any, n: int = 400) -> Any:
    try:
        s = json.dumps(v, default=str)
    except Exception:
        s = str(v)
    return v if len(s) <= n else s[:n] + "…"


async def run_agent(agent, parts: list[types.Part], emit: Optional[Emit] = None,
                    state: Optional[dict] = None, label: str = "") -> dict:
    """Run an agent to completion. Returns {'text': final text of last agent, 'texts': {agent: text}, 'state': state}."""
    svc = InMemorySessionService()
    app_name = "carbontrail"
    user_id = "u"
    sid = uuid.uuid4().hex
    await svc.create_session(app_name=app_name, user_id=user_id, session_id=sid, state=state or {})
    runner = Runner(app_name=app_name, agent=agent, session_service=svc)
    msg = types.Content(role="user", parts=parts)
    texts: dict[str, str] = {}
    last_text = ""
    async for ev in runner.run_async(user_id=user_id, session_id=sid, new_message=msg):
        author = getattr(ev, "author", "") or ""
        if getattr(ev, "partial", False):
            continue
        for fc in ev.get_function_calls() or []:
            if emit:
                await emit({"type": "tool_call", "agent": author, "tool": fc.name, "args": _short(dict(fc.args or {}), 300), "label": label})
        for fr in ev.get_function_responses() or []:
            if emit:
                await emit({"type": "tool_result", "agent": author, "tool": fr.name, "result": _short(fr.response, 600), "label": label})
        content = getattr(ev, "content", None)
        if content and content.parts:
            t = "".join(p.text for p in content.parts if getattr(p, "text", None) and not getattr(p, "thought", False))
            if t.strip() and not (ev.get_function_calls() or ev.get_function_responses()):
                texts[author] = t
                last_text = t
                if emit and author != "document_intake_agent":
                    await emit({"type": "agent_message", "agent": author, "text": t, "label": label})
    session = await svc.get_session(app_name=app_name, user_id=user_id, session_id=sid)
    return {"text": last_text, "texts": texts, "state": dict(session.state) if session else {}}


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def parse_json(text: str) -> Any:
    t = _FENCE.sub("", (text or "").strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise
