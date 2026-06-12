"""
Integration tests for CodeX.

Tests the full architecture:
- Configuration and types
- Tool system and registry
- Approval gate
- Context window
- Tool dispatcher (parallel execution)
- DeepSeek API client (mock)
- Agent core loop (optional, requires API key)
- Session lifecycle
- Sub-agent system

Run with:
    python -m pytest tests/ -v
    DEEPSEEK_API_KEY=xxx python -m pytest tests/ -v --run-live
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codex.config import ApprovalPolicy, Config, Mode, config
from codex.constitution import HierarchyResolver, Tier, enforce_verification
from codex.types import Message, Role, ToolCall, ToolResult, ToolSchema, Turn
from codex.models.tokenizer import TokenBudget, count_tokens
from codex.tools.base import BaseTool, ToolRegistry
from codex.approval.gate import ApprovalGate, GateDecision, GateResult
from codex.context.window import ContextWindow
from codex.dispatch.dispatcher import ToolDispatcher


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def registry():
    """Create a fresh tool registry."""
    return ToolRegistry()


@pytest.fixture
def mock_tool():
    """Create a simple mock tool."""
    class EchoTool(BaseTool):
        name = "echo"
        description = "Echo back the message"
        is_read = True
        parameters = {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"},
            },
            "required": ["message"],
        }

        def execute(self, tool_call: ToolCall) -> ToolResult:
            msg = tool_call.arguments.get("message", "")
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Echo: {msg}",
                success=True,
            )

    return EchoTool()


@pytest.fixture
def write_tool():
    """Create a mock write tool."""
    class FakeWriteTool(BaseTool):
        name = "fake_write"
        description = "Fake write tool for testing"
        is_read = False
        is_write = True
        parameters = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }

        def execute(self, tool_call: ToolCall) -> ToolResult:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Written",
                success=True,
            )

    return FakeWriteTool()


# ═══════════════════════════════════════════════════════════════════
# Config & Types
# ═══════════════════════════════════════════════════════════════════

class TestConfig:
    def test_default_config(self):
        cfg = Config()
        assert cfg.model == "deepseek-chat"
        assert cfg.mode == Mode.AGENT
        assert cfg.max_context_tokens == 1_000_000

    def test_mode_enum(self):
        assert Mode("agent") == Mode.AGENT
        assert Mode("plan") == Mode.PLAN
        assert Mode("yolo") == Mode.YOLO

    def test_policy_enum(self):
        assert ApprovalPolicy("auto") == ApprovalPolicy.AUTO
        assert ApprovalPolicy("suggest") == ApprovalPolicy.SUGGEST
        assert ApprovalPolicy("never") == ApprovalPolicy.NEVER


class TestTypes:
    def test_message_creation(self):
        msg = Message.user("Hello")
        assert msg.role == Role.USER
        assert msg.content == "Hello"

    def test_message_to_api(self):
        msg = Message.system("You are helpful")
        api = msg.to_api()
        assert api["role"] == "system"
        assert api["content"] == "You are helpful"

    def test_tool_call_from_api(self):
        api_data = {
            "id": "call_123",
            "function": {"name": "read_file", "arguments": '{"path": "/tmp/test"}'},
        }
        tc = ToolCall.from_api(api_data)
        assert tc.id == "call_123"
        assert tc.function_name == "read_file"
        assert tc.arguments == {"path": "/tmp/test"}

    def test_tool_schema_to_openai(self):
        schema = ToolSchema(
            name="test",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
        )
        api = schema.to_openai_schema()
        assert api["type"] == "function"
        assert api["function"]["name"] == "test"

    def test_turn_tokens(self):
        turn = Turn(
            user_message=Message.user("Hello world"),
            assistant_message=Message.assistant("Hi there"),
        )
        assert turn.total_tokens > 0


# ═══════════════════════════════════════════════════════════════════
# Constitution
# ═══════════════════════════════════════════════════════════════════

class TestConstitution:
    def test_hierarchy_resolution(self):
        resolver = HierarchyResolver()
        result = resolver.resolve(
            (Tier.MEMORY, "Be concise"),
            (Tier.CASE_COMMAND, "Give full details"),
        )
        assert result == "Give full details"

    def test_tier_override(self):
        assert HierarchyResolver.is_overridable(Tier.CASE_COMMAND, Tier.MEMORY)
        assert not HierarchyResolver.is_overridable(Tier.MEMORY, Tier.CASE_COMMAND)

    def test_truth_enforcement(self):
        assert enforce_truth("hello", "hello") is True
        assert enforce_truth("hello", "world") is False


# ═══════════════════════════════════════════════════════════════════
# Tokenizer
# ═══════════════════════════════════════════════════════════════════

class TestTokenizer:
    def test_count_tokens(self):
        tokens = count_tokens("Hello world")
        assert tokens > 0

    def test_count_empty(self):
        tokens = count_tokens("")
        assert tokens >= 0

    def test_token_budget(self):
        budget = TokenBudget(1000)
        budget.add(500)
        assert budget.usage_ratio == 0.5
        assert not budget.needs_compaction

        budget.add(200)
        assert budget.needs_compaction  # 700/1000 = 70% > 60%

    def test_token_budget_critical(self):
        budget = TokenBudget(1000)
        budget.add(900)
        assert budget.is_critical
        assert budget.needs_compaction


# ═══════════════════════════════════════════════════════════════════
# Tool System
# ═══════════════════════════════════════════════════════════════════

class TestToolRegistry:
    def test_register_tool(self, registry, mock_tool):
        registry.register(mock_tool)
        assert "echo" in registry
        assert registry.count == 1

    def test_execute_tool(self, registry, mock_tool):
        registry.register(mock_tool)
        tc = ToolCall(id="1", function_name="echo", arguments={"message": "hi"})
        result = registry.execute(tc)
        assert result.success
        assert "Echo: hi" in result.content

    def test_execute_unknown_tool(self, registry):
        tc = ToolCall(id="1", function_name="nonexistent", arguments={})
        result = registry.execute(tc)
        assert not result.success
        assert "Unknown tool" in result.content

    def test_get_schemas(self, registry, mock_tool):
        registry.register(mock_tool)
        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0].name == "echo"

    def test_filter_read_write(self, registry, mock_tool, write_tool):
        registry.register(mock_tool)
        registry.register(write_tool)
        assert len(registry.get_read_tools()) == 1  # echo
        assert len(registry.get_write_tools()) == 1  # fake_write

    def test_tool_requires_approval(self, mock_tool, write_tool):
        assert not mock_tool.requires_approval
        assert write_tool.requires_approval


# ═══════════════════════════════════════════════════════════════════
# Approval Gate
# ═══════════════════════════════════════════════════════════════════

class TestApprovalGate:
    def test_agent_suggest_read(self, mock_tool):
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.SUGGEST)
        result = gate.check(mock_tool)
        assert result.decision == GateDecision.ALLOW

    def test_agent_suggest_write(self, write_tool):
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.SUGGEST)
        result = gate.check(write_tool)
        assert result.decision == GateDecision.PROMPT

    def test_yolo_auto(self, write_tool):
        gate = ApprovalGate(mode=Mode.YOLO, policy=ApprovalPolicy.AUTO)
        result = gate.check(write_tool)
        assert result.decision == GateDecision.ALLOW

    def test_plan_blocks_write(self, write_tool):
        gate = ApprovalGate(mode=Mode.PLAN, policy=ApprovalPolicy.SUGGEST)
        result = gate.check(write_tool)
        assert result.decision == GateDecision.BLOCK

    def test_never_blocks_write(self, write_tool):
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.NEVER)
        result = gate.check(write_tool)
        assert result.decision == GateDecision.BLOCK

    def test_never_allows_read(self, mock_tool):
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.NEVER)
        result = gate.check(mock_tool)
        assert result.decision == GateDecision.ALLOW


# ═══════════════════════════════════════════════════════════════════
# Context Window
# ═══════════════════════════════════════════════════════════════════

class TestContextWindow:
    def test_initial_state(self):
        ctx = ContextWindow()
        assert ctx.usage_ratio == 0.0
        assert not ctx.needs_compaction
        assert not ctx.is_critical
        assert ctx.context_pressure == "low"

    def test_system_prompt(self):
        ctx = ContextWindow()
        ctx.set_system_prompt("You are helpful")
        assert ctx.system_prompt_tokens > 0

    def test_add_turn(self):
        ctx = ContextWindow()
        turn = Turn(
            user_message=Message.user("Hello"),
            assistant_message=Message.assistant("Hi"),
        )
        ctx.add_turn(turn)
        assert ctx.total_turns == 1
        assert ctx.usage_ratio > 0.0

    def test_compaction_not_needed(self):
        ctx = ContextWindow(max_tokens=1_000_000)
        assert not ctx.needs_compaction

    def test_stats(self):
        ctx = ContextWindow()
        stats = ctx.stats()
        assert "used_tokens" in stats
        assert "pressure" in stats


# ═══════════════════════════════════════════════════════════════════
# Tool Dispatcher
# ═══════════════════════════════════════════════════════════════════

class TestDispatcher:
    def test_dispatch_single(self, registry, mock_tool):
        registry.register(mock_tool)
        dispatcher = ToolDispatcher(registry)
        tc = ToolCall(id="1", function_name="echo", arguments={"message": "test"})
        result = dispatcher.dispatch([tc])
        assert len(result.tool_results) == 1
        assert result.tool_results[0].success

    def test_dispatch_parallel(self, registry, mock_tool):
        registry.register(mock_tool)
        dispatcher = ToolDispatcher(registry)
        calls = [
            ToolCall(id=str(i), function_name="echo", arguments={"message": f"msg{i}"})
            for i in range(5)
        ]
        result = dispatcher.dispatch(calls)
        assert len(result.tool_results) == 5
        assert all(r.success for r in result.tool_results)

    def test_dispatch_empty(self, registry):
        dispatcher = ToolDispatcher(registry)
        result = dispatcher.dispatch([])
        assert len(result.tool_results) == 0

    def test_dispatch_unknown_tool(self, registry):
        dispatcher = ToolDispatcher(registry)
        tc = ToolCall(id="1", function_name="ghost", arguments={})
        result = dispatcher.dispatch([tc])
        assert not result.tool_results[0].success

    def test_dependency_key(self, registry):
        dispatcher = ToolDispatcher(registry)
        tc1 = ToolCall(id="1", function_name="read_file", arguments={"path": "/a"})
        tc2 = ToolCall(id="2", function_name="read_file", arguments={"path": "/b"})
        assert dispatcher._dependency_key(tc1) != dispatcher._dependency_key(tc2)

        tc3 = ToolCall(id="3", function_name="read_file", arguments={"path": "/a"})
        assert dispatcher._dependency_key(tc1) == dispatcher._dependency_key(tc3)


# ═══════════════════════════════════════════════════════════════════
# Sub-Agent (unit tests, no API calls needed)
# ═══════════════════════════════════════════════════════════════════

class TestSubAgent:
    def test_sub_agent_config(self):
        from codex.sub_agent import SubAgentConfig
        cfg = SubAgentConfig(
            name="test",
            prompt="Do something",
            allowed_tools=["read_file"],
        )
        assert cfg.name == "test"
        assert cfg.allowed_tools == ["read_file"]


# ═══════════════════════════════════════════════════════════════════
# Live Tests (requires DEEPSEEK_API_KEY)
# ═══════════════════════════════════════════════════════════════════

live = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — set it to run live tests",
)


class TestLiveAgent:
    @live
    def test_session_lifecycle(self, temp_workspace):
        """Test full session lifecycle with real API."""
        from codex.session import Session, SessionConfig

        session = Session(SessionConfig(
            mode=Mode.AGENT,
            approval_policy=ApprovalPolicy.SUGGEST,
            workspace=temp_workspace,
        ))
        session.initialize()

        # Simple query
        response = session.send("What is 2+2? Answer with just the number.")
        assert response.success
        assert "4" in response.content

    @live
    def test_tool_calling(self, temp_workspace):
        """Test that the agent can use tools."""
        from codex.session import Session, SessionConfig

        # Create a test file
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Hello from CodeX!")

        session = Session(SessionConfig(
            mode=Mode.AGENT,
            approval_policy=ApprovalPolicy.AUTO,
            workspace=temp_workspace,
        ))
        session.initialize()

        response = session.send(
            "Read the file test.txt and tell me what it says. "
            "Start your answer with 'FILE_CONTENT:'."
        )
        assert response.success
        assert "Hello from CodeX" in response.content
        assert len(response.tool_results) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
