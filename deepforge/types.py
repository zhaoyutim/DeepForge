"""
Core type definitions for DeepForge.

Defines the message protocol between:
- User ↔ Agent (chat messages)
- Agent ↔ DeepSeek API (tool-augmented completions)
- Agent ↔ Tools (tool calls and results)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ─── Message Types ──────────────────────────────────────────────────

class Role(str, Enum):
    """Message role in the conversation."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""
    id: str
    function_name: str
    arguments: dict[str, Any]

    @classmethod
    def from_api(cls, api_tool_call: dict) -> "ToolCall":
        """Parse from DeepSeek/OpenAI API format."""
        fn = api_tool_call.get("function", {})
        args_str = fn.get("arguments", "{}")
        try:
            arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            arguments = {}
        return cls(
            id=api_tool_call.get("id", str(uuid.uuid4())),
            function_name=fn.get("name", "unknown"),
            arguments=arguments,
        )

    def to_api(self) -> dict:
        """Convert to API format for the next request."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.function_name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class ToolResult:
    """The result of executing a tool call."""
    tool_call_id: str
    content: str
    success: bool = True
    error: Optional[str] = None
    tool_name: Optional[str] = None  # Human-readable tool name for display

    def to_message(self) -> dict:
        """Convert to a 'tool' role message for the API."""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content if self.success else f"Error: {self.error}",
        }


@dataclass
class Message:
    """A single message in the conversation history."""
    role: Role
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None  # For tool result messages
    name: Optional[str] = None  # Optional author name

    def to_api(self) -> dict:
        """Convert to API-compatible dict."""
        msg: dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: Optional[str] = None, tool_calls: Optional[list[ToolCall]] = None) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, tool_result: ToolResult) -> "Message":
        return cls(role=Role.TOOL, content=tool_result.content, tool_call_id=tool_result.tool_call_id)


@dataclass
class ToolSchema:
    """JSON Schema definition for a tool."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the parameters

    # Tool categories for approval gating
    is_read: bool = True      # Read-only (always silent)
    is_write: bool = False    # Modifies filesystem
    is_shell: bool = False    # Executes shell commands
    is_network: bool = False  # Makes network calls

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Turn:
    """A single turn in the conversation (user message + assistant response + tool results)."""
    user_message: Message
    assistant_message: Optional[Message] = None
    tool_results: list[ToolResult] = field(default_factory=list)
    thinking_tokens: int = 0
    cache_hit: bool = False

    @property
    def total_tokens(self) -> int:
        """Rough token estimate for this turn."""
        content = self.user_message.content or ""
        assistant_content = self.assistant_message.content if self.assistant_message else ""
        return len(content) // 3 + len(assistant_content) // 3 + self.thinking_tokens
