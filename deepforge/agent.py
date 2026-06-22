"""
Agent — the core reasoning loop that drives DeepForge.

The Agent is the central orchestrator that:
1. Receives a user message
2. Calls the DeepSeek API with available tools
3. Dispatches tool calls through the ToolDispatcher
4. Feeds tool results back to the model
5. Repeats until the model produces a final text response (no more tool calls)

This implements the full CodeWhale tool-calling architecture:
  User → Agent → DeepSeek API → Tool Calls → Dispatcher → Results → DeepSeek API → ... → Final Response
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from deepforge.approval.gate import ApprovalGate, GateDecision
from deepforge.context.window import ContextWindow
from deepforge.dispatch.dispatcher import ToolDispatcher
from deepforge.models.azure import AzureClient
from deepforge.models.deepseek import DeepSeekClient, get_client
from deepforge.tools.base import ToolRegistry, get_registry
from deepforge.tools.base import BaseTool
from deepforge.types import Message, ToolCall, ToolResult, Turn


ApprovalCallback = Callable[[BaseTool, ToolCall, object], bool]


@dataclass
class AgentResponse:
    """The result of an agent processing cycle."""
    content: str
    tool_results: list[ToolResult] = field(default_factory=list)
    turns_completed: int = 0
    total_tokens_used: int = 0
    latency_ms: float = 0.0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


class Agent:
    """
    The core agent loop.

    Orchestrates the cycle:
      Model → Tool Dispatch → Result Injection → Model → ... → Final Answer
    """

    MAX_TOOL_ROUNDS = 10  # Safety limit: max turns of tool calling per user message

    def __init__(
        self,
        client: Optional[Union[DeepSeekClient, AzureClient]] = None,
        registry: Optional[ToolRegistry] = None,
        context: Optional[ContextWindow] = None,
        gate: Optional[ApprovalGate] = None,
        approval_callback: Optional[ApprovalCallback] = None,
    ):
        self.client = client or get_client()
        self.registry = registry or get_registry()
        self.context = context or ContextWindow()
        self.gate = gate or ApprovalGate()
        self.approval_callback = approval_callback

        # Create dispatcher
        self.dispatcher = ToolDispatcher(
            registry=self.registry,
            on_tool_start=self._on_tool_start,
            on_tool_complete=self._on_tool_complete,
        )

        # State
        self.conversation: list[Message] = []
        self.system_prompt: Optional[str] = None
        self.total_turns: int = 0

    # ── Main Entry Point ────────────────────────────────────────

    def process(
        self,
        user_input: str,
        system_prompt: Optional[str] = None,
    ) -> AgentResponse:
        """
        Process a user message through the full agent loop.

        Args:
            user_input: The user's message
            system_prompt: Optional system prompt override

        Returns:
            AgentResponse with the final content and metadata
        """
        start_time = time.time()
        self.total_turns += 1

        # Set system prompt if provided
        if system_prompt:
            self.system_prompt = system_prompt

        # Add user message
        user_msg = Message.user(user_input)
        self.conversation.append(user_msg)

        # Build the current turn
        turn = Turn(user_message=user_msg)

        # Run the agent loop
        try:
            final_content, tool_results = self._run_loop(turn)
            latency_ms = (time.time() - start_time) * 1000

            return AgentResponse(
                content=final_content,
                tool_results=tool_results,
                turns_completed=self.total_turns,
                total_tokens_used=self.client.total_tokens_used,
                latency_ms=latency_ms,
            )
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            return AgentResponse(
                content=f"Error: {e}",
                turns_completed=self.total_turns,
                total_tokens_used=self.client.total_tokens_used,
                latency_ms=latency_ms,
                error=str(e),
            )

    # ── Core Loop ───────────────────────────────────────────────

    def _run_loop(self, turn: Turn) -> tuple[str, list[ToolResult]]:
        """
        The main reasoning loop.

        1. Call the model
        2. If tool calls: dispatch, inject results, go to 1
        3. If text response: return it

        Returns (final_text, all_tool_results)
        """
        all_tool_results: list[ToolResult] = []
        tool_rounds = 0

        while tool_rounds < self.MAX_TOOL_ROUNDS:
            tool_rounds += 1

            # Get tool schemas for this call
            tool_schemas = self.registry.get_schemas()

            # Call the model
            response = self.client.tool_chat(
                messages=self.conversation,
                tools=tool_schemas,
                system_prompt=self.system_prompt,
            )

            # Check for errors
            if "error" in response:
                error_msg = response.get("error", "Unknown API error")
                return f"API Error: {error_msg}", all_tool_results

            content = response.get("content")
            tool_calls = response.get("tool_calls", [])

            # If the model returned tool calls, execute them
            if tool_calls:
                # Create assistant message with tool calls
                assistant_msg = Message.assistant(
                    content=content,
                    tool_calls=tool_calls,
                )
                self.conversation.append(assistant_msg)

                # Filter tool calls through approval gate
                executable_calls, blocked_info = self._filter_through_gate(tool_calls)

                if not executable_calls:
                    # All calls blocked
                    all_tool_results.extend(blocked_info)
                    for result in blocked_info:
                        self.conversation.append(Message.tool_result(result))
                    continue

                # Execute tool calls via dispatcher
                dispatch_result = self.dispatcher.dispatch_all(executable_calls)
                results = dispatch_result.tool_results + blocked_info
                all_tool_results.extend(results)

                # Inject tool results into conversation
                for result in results:
                    self.conversation.append(Message.tool_result(result))

                # Store in turn
                turn.assistant_message = assistant_msg
                turn.tool_results = results

                # Continue loop — model will see tool results in next call
                continue

            # No tool calls — this is the final response
            if content:
                assistant_msg = Message.assistant(content=content)
                self.conversation.append(assistant_msg)
                turn.assistant_message = assistant_msg
                return content, all_tool_results

            # Empty response — shouldn't happen, but handle gracefully
            return "(no response)", all_tool_results

        # Exceeded max tool rounds
        return (
            f"(Reached maximum tool calling rounds ({self.MAX_TOOL_ROUNDS}). "
            f"Please simplify your request.)",
            all_tool_results,
        )

    # ── Approval Gate Filtering ──────────────────────────────────

    def _filter_through_gate(
        self,
        tool_calls: list[ToolCall],
    ) -> tuple[list[ToolCall], list[ToolResult]]:
        """
        Filter tool calls through the approval gate.

        Returns:
            (executable_calls, blocked_results) — blocked results are ToolResults
            that can be injected into the conversation as errors.
        """
        executable: list[ToolCall] = []
        blocked_results: list[ToolResult] = []

        for tc in tool_calls:
            tool = self.registry.get(tc.function_name)
            if tool is None:
                # Unknown tool
                blocked_results.append(ToolResult(
                    tool_call_id=tc.id,
                    content=f"Error: Unknown tool '{tc.function_name}'",
                    success=False,
                    error=f"Unknown tool: {tc.function_name}",
                    tool_name=tc.function_name,
                ))
                continue

            # Check approval
            gate_result = self.gate.check(tool, tc)

            if gate_result.decision == GateDecision.BLOCK:
                blocked_results.append(ToolResult(
                    tool_call_id=tc.id,
                    content=f"Blocked: {gate_result.reason}",
                    success=False,
                    error=gate_result.reason,
                    tool_name=tc.function_name,
                ))
            elif gate_result.decision == GateDecision.PROMPT:
                approved = False
                if self.approval_callback:
                    approved = bool(self.approval_callback(tool, tc, gate_result))

                if approved:
                    executable.append(tc)
                else:
                    reason = "User denied approval." if self.approval_callback else "Approval required but no approval handler is configured."
                    blocked_results.append(ToolResult(
                        tool_call_id=tc.id,
                        content=f"Blocked: {reason}",
                        success=False,
                        error=reason,
                        tool_name=tc.function_name,
                    ))
            else:
                executable.append(tc)

        return executable, blocked_results

    # ── Streaming Process ────────────────────────────────────────

    def process_stream(self, user_input: str, system_prompt: Optional[str] = None):
        """
        Process a user message with streaming output.

        Yields events:
            {"type": "text", "content": "word"}            — text chunk from model
            {"type": "tool_start", "name": str, "args": {}} — tool started
            {"type": "tool_end", "name": str, "success": bool, "output": str} — tool done
            {"type": "done", "content": str, "tool_count": int, "tokens": int, "ms": float}
            {"type": "error", "error": str}
        """
        start_time = time.time()
        self.total_turns += 1

        if system_prompt:
            self.system_prompt = system_prompt

        user_msg = Message.user(user_input)
        self.conversation.append(user_msg)
        turn = Turn(user_message=user_msg)

        all_tool_results: list[ToolResult] = []
        tool_rounds = 0

        while tool_rounds < self.MAX_TOOL_ROUNDS:
            tool_rounds += 1
            tool_schemas = self.registry.get_schemas()

            # Accumulate streaming response
            text_content = ""
            tool_calls: list[ToolCall] = []
            usage_info = {}

            try:
                for event in self.client.chat_stream(
                    messages=self.conversation,
                    tools=tool_schemas,
                    system_prompt=self.system_prompt,
                ):
                    if event["type"] == "text":
                        text_content += event["content"]
                        yield {"type": "text", "content": event["content"]}

                    elif event["type"] == "tool_call":
                        tool_calls.append(event["tool_call"])

                    elif event["type"] == "error":
                        yield {"type": "error", "error": event["error"]}
                        return

                    elif event["type"] == "done":
                        usage_info = event.get("usage", {})

            except Exception as e:
                yield {"type": "error", "error": str(e)}
                return

            # If model returned tool calls, execute them
            if tool_calls:
                assistant_msg = Message.assistant(content=text_content or None, tool_calls=tool_calls)
                self.conversation.append(assistant_msg)

                executable_calls, blocked_info = self._filter_through_gate(tool_calls)

                if not executable_calls:
                    for result in blocked_info:
                        self.conversation.append(Message.tool_result(result))
                        yield {
                            "type": "tool_end",
                            "name": "gate",
                            "success": False,
                            "output": result.content,
                        }
                    continue

                # Execute tools one by one, yielding progress
                for tc in executable_calls:
                    yield {
                        "type": "tool_start",
                        "name": tc.function_name,
                        "args": tc.arguments,
                    }

                for tc in executable_calls:
                    result = self.registry.execute(tc)
                    all_tool_results.append(result)
                    self.conversation.append(Message.tool_result(result))
                    yield {
                        "type": "tool_end",
                        "name": tc.function_name,
                        "success": result.success,
                        "output": result.content[:500],
                    }

                # Also inject blocked results
                for result in blocked_info:
                    self.conversation.append(Message.tool_result(result))

                turn.assistant_message = assistant_msg
                turn.tool_results = all_tool_results
                continue  # Another loop iteration

            # No tool calls — final response
            if text_content:
                assistant_msg = Message.assistant(content=text_content)
                self.conversation.append(assistant_msg)
                turn.assistant_message = assistant_msg

            yield {
                "type": "done",
                "content": text_content or "(no response)",
                "tool_count": len(all_tool_results),
                "tokens": usage_info.get("total_tokens", 0),
                "ms": (time.time() - start_time) * 1000,
            }
            return

        # Max rounds exceeded
        yield {
            "type": "done",
            "content": f"(Reached max tool rounds: {self.MAX_TOOL_ROUNDS})",
            "tool_count": len(all_tool_results),
            "tokens": 0,
            "ms": (time.time() - start_time) * 1000,
        }

    # ── Callbacks ───────────────────────────────────────────────

    def _on_tool_start(self, tool_call: ToolCall) -> None:
        """Called when a tool starts executing."""
        pass

    def _on_tool_complete(self, tool_call: ToolCall, result: ToolResult) -> None:
        """Called when a tool completes."""
        pass

    # ── Utility ─────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset the conversation history."""
        self.conversation = []

    @property
    def message_count(self) -> int:
        return len(self.conversation)
