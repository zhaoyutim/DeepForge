"""
DeepSeek API client — OpenAI-compatible chat completion with tool calling.

Supports:
- Synchronous chat completions
- Tool-augmented requests (function calling)
- Token usage tracking
- Prefix cache awareness
"""

from __future__ import annotations

import json
import time
from typing import Optional

from openai import OpenAI

from deepforge.config import config
from deepforge.types import Message, Role, ToolCall, ToolSchema


class DeepSeekClient:
    """
    Thin wrapper around OpenAI SDK pointed at DeepSeek's API.

    DeepSeek's API is OpenAI-compatible:
      base_url = "https://api.deepseek.com/v1"
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or config.api_key
        self.base_url = base_url or config.api_base_url
        self.model = model or config.model

        if not self.api_key:
            raise ValueError(
                "DeepSeek API key not found. Set DEEPFORGE_API_KEY or DEEPSEEK_API_KEY "
                "environment variable."
            )

        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        # Statistics
        self.total_tokens_used: int = 0
        self.total_requests: int = 0
        self.cache_hit_tokens: int = 0
        self.cache_miss_tokens: int = 0

    # ── Core API Call ──────────────────────────────────────────────

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> dict:
        """
        Send a chat completion request to DeepSeek.

        Args:
            messages: Conversation history
            tools: Available tool schemas (for function calling)
            system_prompt: Optional system prompt (prepended)
            temperature: Sampling temperature (0 = deterministic)
            max_tokens: Max tokens in response
            stream: Whether to stream the response

        Returns:
            {
                "content": str | None,
                "tool_calls": list[ToolCall],
                "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
                "finish_reason": str,
            }
        """
        # Build API messages
        api_messages: list[dict] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        for msg in messages:
            api_dict = msg.to_api()
            # Filter out empty content (API requirement)
            if api_dict.get("content") or api_dict.get("tool_calls") or api_dict.get("tool_call_id"):
                api_messages.append(api_dict)

        # Build tool schemas
        api_tools = None
        if tools:
            api_tools = [t.to_openai_schema() for t in tools]

        # Make the API call
        self.total_requests += 1
        start_time = time.time()

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                tools=api_tools,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream,
            )
        except Exception as e:
            return {
                "content": None,
                "tool_calls": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "finish_reason": "error",
                "error": str(e),
                "latency_ms": (time.time() - start_time) * 1000,
            }

        # Parse response
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason

        # Extract content
        content = message.content

        # Extract tool calls
        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall.from_api({
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }))

        # Track usage
        usage = response.usage
        if usage:
            self.total_tokens_used += usage.total_tokens
            # Track cache hits if available
            cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
            cache_miss = getattr(usage, "prompt_cache_miss_tokens", None)
            if cache_hit is not None:
                self.cache_hit_tokens += cache_hit
            if cache_miss is not None:
                self.cache_miss_tokens += cache_miss

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
            },
            "finish_reason": finish_reason,
            "latency_ms": (time.time() - start_time) * 1000,
        }

    # ── Streaming API ──────────────────────────────────────────────

    def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        """
        Stream a chat completion from DeepSeek, yielding events.

        Yields dicts:
            {"type": "text", "content": "word"}         — text chunk
            {"type": "text", "content": None}            — text stream ended
            {"type": "tool_call", "tool_call": ToolCall} — complete tool call
            {"type": "done", "usage": {...}, "finish_reason": str}
            {"type": "error", "error": str}
        """
        api_messages: list[dict] = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        for msg in messages:
            api_dict = msg.to_api()
            if api_dict.get("content") or api_dict.get("tool_calls") or api_dict.get("tool_call_id"):
                api_messages.append(api_dict)

        api_tools = None
        if tools:
            api_tools = [t.to_openai_schema() for t in tools]

        self.total_requests += 1
        start_time = time.time()

        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                tools=api_tools,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return

        # Accumulate text and tool calls across chunks
        text_buffer = ""
        tool_call_buffers: dict[int, dict] = {}  # index → {id, name, arguments}
        finish_reason = None
        usage_info = {}

        for chunk in stream:
            if not chunk.choices:
                # Usage chunk (final)
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    }
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # Text content
            if delta.content:
                text_buffer += delta.content
                yield {"type": "text", "content": delta.content}

            # Tool calls (may be fragmented across chunks)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_buffers:
                        tool_call_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    buf = tool_call_buffers[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            buf["arguments"] += tc_delta.function.arguments

        # Finalize tool calls
        for idx in sorted(tool_call_buffers.keys()):
            buf = tool_call_buffers[idx]
            try:
                args = json.loads(buf["arguments"]) if buf["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tc = ToolCall(
                id=buf["id"] or str(uuid.uuid4()),
                function_name=buf["name"],
                arguments=args,
            )
            yield {"type": "tool_call", "tool_call": tc}

        # Track usage
        if usage_info:
            self.total_tokens_used += usage_info.get("total_tokens", 0)

        yield {
            "type": "done",
            "content": text_buffer or None,
            "usage": usage_info,
            "finish_reason": finish_reason or "stop",
            "latency_ms": (time.time() - start_time) * 1000,
        }

    # ── Convenience Methods ─────────────────────────────────────────

    def plain_chat(self, messages: list[Message], system_prompt: Optional[str] = None) -> str:
        """Simple chat without tool calling. Returns text content."""
        result = self.chat(messages=messages, system_prompt=system_prompt)
        return result.get("content", "") or ""

    def tool_chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        system_prompt: Optional[str] = None,
    ) -> dict:
        """Chat with tool calling enabled. Returns content + tool_calls."""
        return self.chat(messages=messages, tools=tools, system_prompt=system_prompt)

    # ── Health Check ────────────────────────────────────────────────

    def ping(self) -> bool:
        """Quick API health check."""
        try:
            result = self.chat(messages=[Message.user("ping")], max_tokens=10)
            return "error" not in result
        except Exception:
            return False

    # ── Stats ───────────────────────────────────────────────────────

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0)."""
        total_cache = self.cache_hit_tokens + self.cache_miss_tokens
        if total_cache == 0:
            return 0.0
        return self.cache_hit_tokens / total_cache


# Singleton client (can create new instances for sub-agents)
_client: Optional[DeepSeekClient] = None


def get_client() -> DeepSeekClient:
    """Get or create the global DeepSeek client."""
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client
