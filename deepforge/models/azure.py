"""
Azure OpenAI API client — OpenAI-compatible chat completion with tool calling.

Wraps openai.AzureOpenAI for Azure Foundry (AI Hub) deployments.
Supports the same interface as DeepSeekClient:
- Synchronous chat completions
- Tool-augmented requests (function calling)
- Streaming responses
- Token usage tracking
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from openai import AzureOpenAI

from deepforge.config import config
from deepforge.types import Message, ToolCall, ToolSchema


class AzureClient:
    """
    Thin wrapper around AzureOpenAI SDK for Azure Foundry deployments.

    Azure differences from standard OpenAI:
    - Uses api-key header instead of Authorization: Bearer
    - Requires api_version query parameter
    - Endpoint format: https://RESOURCE.openai.azure.com/openai/v1/
    - Uses deployment names instead of model names

    The chat() and chat_stream() interfaces are identical to DeepSeekClient
    so the Agent loop works without changes.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        endpoint: Optional[str] = None,
        deployment: Optional[str] = None,
        api_version: Optional[str] = None,
        model: Optional[str] = None,  # model = deployment for Azure
        reasoning_effort: Optional[str] = None,
    ):
        self.api_key = api_key or config.azure_api_key
        self.api_url = api_url or config.azure_api_url
        self.endpoint = endpoint or config.azure_endpoint
        self.deployment = deployment or model or config.azure_deployment or config.azure_model
        self.api_version = api_version or config.azure_api_version
        self.model = self.deployment  # Keep model attr for compat
        self.reasoning_effort = reasoning_effort or config.azure_reasoning_effort
        self.uses_responses_api = bool(self.api_url)

        if not self.api_key:
            raise ValueError(
                "Azure API key not found. Set AZURE_OPENAI_API_KEY environment variable "
                "or configure azure.api_key in env.yaml."
            )
        if self.api_url:
            self.endpoint, self.api_version = self._parse_api_url(self.api_url, self.api_version)
        elif not self.endpoint:
            raise ValueError(
                "Azure endpoint not found. Set AZURE_OPENAI_ENDPOINT environment variable "
                "or configure azure.endpoint in env.yaml. For a full Responses API URL, "
                "configure azure.api_url instead."
            )
        if not self.deployment:
            raise ValueError(
                "Azure model/deployment not found. Set AZURE_OPENAI_DEPLOYMENT or configure "
                "azure.deployment / azure.model in env.yaml."
            )

        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "api_version": self.api_version,
        }
        if self.uses_responses_api:
            client_kwargs["base_url"] = self.endpoint
        else:
            client_kwargs["azure_endpoint"] = self.endpoint

        self._client = AzureOpenAI(**client_kwargs)

        # Statistics (same interface as DeepSeekClient)
        self.total_tokens_used: int = 0
        self.total_requests: int = 0
        # Azure doesn't expose prefix cache metrics — always 0
        self.cache_hit_tokens: int = 0
        self.cache_miss_tokens: int = 0

    @staticmethod
    def _parse_tool_arguments(raw_arguments: Any, function_name: str) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if not raw_arguments:
            return {}
        if not isinstance(raw_arguments, str):
            return {"_tool_parse_error": f"Invalid tool arguments for {function_name}: expected JSON string or object"}

        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return {
                "_tool_parse_error": (
                    f"Invalid JSON tool arguments for {function_name}: {exc.msg} at char {exc.pos}. "
                    "The tool call may have been truncated; retry with valid JSON and split large files into smaller writes."
                ),
                "_raw_arguments_preview": raw_arguments[:500],
                "_raw_arguments_length": len(raw_arguments),
            }
        return parsed if isinstance(parsed, dict) else {"_tool_parse_error": f"Invalid tool arguments for {function_name}: expected JSON object"}

    @staticmethod
    def _parse_api_url(api_url: str, fallback_api_version: str) -> tuple[str, str]:
        """Split a full Azure Responses API URL into SDK base_url + api_version."""
        parsed = urlparse(api_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                "azure.api_url must be a full URL, for example "
                "https://xxx.com/api/.../openai/responses?api-version=2025-04-01-preview"
            )

        path = parsed.path.rstrip("/")
        if path.endswith("/responses"):
            path = path[: -len("/responses")]
        if not path:
            path = "/"

        query = parse_qs(parsed.query)
        api_version = (query.get("api-version") or [fallback_api_version])[0]
        base_url = urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/") + "/", "", "", ""))
        return base_url, api_version

    @staticmethod
    def _responses_input(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert DeepForge's chat-style conversation into Responses API input items."""
        items: list[dict[str, Any]] = []
        for msg in messages:
            api_msg = msg.to_api()
            role = api_msg.get("role")
            content = api_msg.get("content")

            if role in {"user", "system"} and content:
                items.append({"role": role, "content": content})
            elif role == "assistant":
                if content:
                    items.append({"role": "assistant", "content": content})
                for tc in msg.tool_calls:
                    items.append({
                        "type": "function_call",
                        "call_id": tc.id,
                        "name": tc.function_name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        "status": "completed",
                    })
            elif role == "tool" and msg.tool_call_id:
                items.append({
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id,
                    "output": content or "",
                    "status": "completed",
                })
        return items

    @staticmethod
    def _responses_tools(tools: Optional[list[ToolSchema]]) -> Optional[list[dict[str, Any]]]:
        if not tools:
            return None
        api_tools: list[dict[str, Any]] = []
        for tool in tools:
            api_tools.append({
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": False,
            })
        return api_tools

    @staticmethod
    def _response_text(response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    parts.append(text)
        return "".join(parts)

    def _parse_responses_response(self, response: Any, start_time: float) -> dict:
        content = self._response_text(response)
        tool_calls: list[ToolCall] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            function_name = getattr(item, "name", "unknown")
            tool_calls.append(ToolCall(
                id=getattr(item, "call_id", None) or getattr(item, "id", None) or "",
                function_name=function_name,
                arguments=self._parse_tool_arguments(getattr(item, "arguments", "{}"), function_name),
            ))

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
        self.total_tokens_used += total_tokens

        return {
            "content": content or None,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "finish_reason": getattr(response, "status", None) or "stop",
            "latency_ms": (time.time() - start_time) * 1000,
        }

    # ── Core API Call ──────────────────────────────────────────────

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> dict:
        """
        Send a chat completion request to Azure OpenAI.

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

        if self.uses_responses_api:
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "input": self._responses_input(messages),
                    "temperature": temperature,
                    "max_output_tokens": max_tokens or config.max_output_tokens,
                }
                if system_prompt:
                    kwargs["instructions"] = system_prompt
                response_tools = self._responses_tools(tools)
                if response_tools:
                    kwargs["tools"] = response_tools
                if self.reasoning_effort:
                    kwargs["reasoning"] = {"effort": self.reasoning_effort}
                response = self._client.responses.create(**kwargs)
                return self._parse_responses_response(response, start_time)
            except Exception as e:
                return {
                    "content": None,
                    "tool_calls": [],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "finish_reason": "error",
                    "error": str(e),
                    "latency_ms": (time.time() - start_time) * 1000,
                }

        try:
            kwargs: dict[str, Any] = {
                "model": self.deployment,
                "messages": api_messages,
                "tools": api_tools,
                "temperature": temperature,
                "max_tokens": max_tokens or config.max_output_tokens,
                "stream": stream,
            }
            if self.reasoning_effort:
                kwargs["reasoning_effort"] = self.reasoning_effort
            response = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            return {
                "content": None,
                "tool_calls": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "finish_reason": "error",
                "error": str(e),
                "latency_ms": (time.time() - start_time) * 1000,
            }

        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason

        content = message.content

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                function_name = tc.function.name
                tool_calls.append(ToolCall(
                    id=tc.id,
                    function_name=function_name,
                    arguments=self._parse_tool_arguments(tc.function.arguments, function_name),
                ))

        usage = response.usage
        if usage:
            self.total_tokens_used += usage.total_tokens

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
        max_tokens: Optional[int] = None,
    ):
        """
        Stream a chat completion from Azure OpenAI, yielding events.

        Yields dicts:
            {"type": "text", "content": "word"}
            {"type": "text", "content": None}
            {"type": "tool_call", "tool_call": ToolCall}
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

        if self.uses_responses_api:
            response = self.chat(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
            if response.get("error"):
                yield {"type": "error", "error": response["error"]}
                return
            if response.get("content"):
                yield {"type": "text", "content": response["content"]}
            for tool_call in response.get("tool_calls", []):
                yield {"type": "tool_call", "tool_call": tool_call}
            yield {
                "type": "done",
                "content": response.get("content"),
                "usage": response.get("usage", {}),
                "finish_reason": response.get("finish_reason", "stop"),
                "latency_ms": response.get("latency_ms", 0),
            }
            return

        self.total_requests += 1
        start_time = time.time()

        try:
            kwargs: dict[str, Any] = {
                "model": self.deployment,
                "messages": api_messages,
                "tools": api_tools,
                "temperature": temperature,
                "max_tokens": max_tokens or config.max_output_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if self.reasoning_effort:
                kwargs["reasoning_effort"] = self.reasoning_effort
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return

        text_buffer = ""
        tool_call_buffers: dict[int, dict] = {}
        finish_reason = None
        usage_info = {}

        for chunk in stream:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    }
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                text_buffer += delta.content
                yield {"type": "text", "content": delta.content}

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
        import uuid
        for idx in sorted(tool_call_buffers.keys()):
            buf = tool_call_buffers[idx]
            function_name = buf["name"]
            args = self._parse_tool_arguments(buf["arguments"], function_name)
            tc = ToolCall(
                id=buf["id"] or str(uuid.uuid4()),
                function_name=function_name,
                arguments=args,
            )
            yield {"type": "tool_call", "tool_call": tc}

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
        """Cache hit rate — always 0.0 for Azure (not applicable)."""
        return 0.0
