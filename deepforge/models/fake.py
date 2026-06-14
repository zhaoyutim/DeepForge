"""Fake model client for deterministic agent tests."""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from deepforge.types import ToolCall


class FakeDeepSeekClient:
    """Small DeepSeekClient-compatible fake for unit tests.

    Responses are dictionaries shaped like DeepSeekClient.tool_chat output:
    {"content": str | None, "tool_calls": [ToolCall], "usage": {...}}
    """

    def __init__(self, responses: Iterable[dict[str, Any]]):
        self.responses = deque(responses)
        self.total_tokens_used = 0
        self.total_requests = 0

    def _next(self) -> dict[str, Any]:
        self.total_requests += 1
        if not self.responses:
            return {"content": "", "tool_calls": [], "usage": {"total_tokens": 0}}
        response = self.responses.popleft()
        usage = response.get("usage") or {}
        self.total_tokens_used += int(usage.get("total_tokens", 0) or 0)
        return response

    def tool_chat(self, messages, tools, system_prompt=None) -> dict[str, Any]:
        return self._next()

    def chat_stream(self, messages, tools=None, system_prompt=None, temperature=0.0, max_tokens=None):
        response = self._next()
        content = response.get("content") or ""
        if content:
            yield {"type": "text", "content": content}
        for tool_call in response.get("tool_calls", []) or []:
            if isinstance(tool_call, ToolCall):
                yield {"type": "tool_call", "tool_call": tool_call}
            else:
                yield {"type": "tool_call", "tool_call": ToolCall.from_api(tool_call)}
        yield {
            "type": "done",
            "content": content or None,
            "usage": response.get("usage") or {"total_tokens": 0},
            "finish_reason": response.get("finish_reason", "stop"),
        }
