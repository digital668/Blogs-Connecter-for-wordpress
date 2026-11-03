"""Thin wrapper around the Claude API that returns structured JSON via a tool call."""
from __future__ import annotations

import os

from .config import CLAUDE_MODEL


class LLMUnavailable(RuntimeError):
    pass


_client = None


def client():
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMUnavailable("ANTHROPIC_API_KEY secret not set")
        import anthropic
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def structured(*, system: str, prompt: str, tool_name: str, schema: dict,
               web_search: bool = False, max_searches: int = 5,
               max_tokens: int = 8000, country: str | None = None) -> tuple[dict, dict]:
    """Ask Claude to answer by calling `tool_name` with JSON matching `schema`.

    Returns (tool_input, usage_dict). With web_search=True Claude may search
    Google-style results first (billed per search on the same API key).
    """
    tools: list[dict] = [{"name": tool_name, "description": "Submit the final result.",
                          "input_schema": schema}]
    if web_search:
        ws = {"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}
        if country:
            ws["user_location"] = {"type": "approximate", "country": country.upper()}
        tools.append(ws)

    messages: list[dict] = [{"role": "user", "content": prompt}]
    usage = {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    for _ in range(6):
        kwargs = dict(model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
                      tools=tools, messages=messages)
        if not web_search:
            kwargs["tool_choice"] = {"type": "tool", "name": tool_name}
        resp = client().messages.create(**kwargs)
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        stu = getattr(resp.usage, "server_tool_use", None)
        if stu is not None:
            usage["searches"] += getattr(stu, "web_search_requests", 0) or 0
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                return dict(block.input), usage
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Claude ran out of tokens before finishing — raise max_tokens")
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "pause_turn":
            messages.append({"role": "user", "content": f"Now call the {tool_name} tool with the final result."})
    raise RuntimeError(f"Claude did not call {tool_name}")
