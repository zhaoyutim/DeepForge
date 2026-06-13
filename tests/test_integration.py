"""
Integration tests for DeepForge.

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

from deepforge.config import ApprovalPolicy, Config, Mode, config
from deepforge.constitution import HierarchyResolver, Tier, enforce_truth, enforce_verification
from deepforge.types import Message, Role, ToolCall, ToolResult, ToolSchema, Turn
from deepforge.models.tokenizer import TokenBudget, count_tokens
from deepforge.tools.base import BaseTool, ToolRegistry
from deepforge.tools.browser_tools import BrowserEvalTool, BrowserOpenTool, BrowserSnapshotTool
from deepforge.computer.browser import BrowserElement, BrowserSnapshot
from deepforge.approval.gate import ApprovalGate, GateDecision, GateResult
from deepforge.context.window import ContextWindow
from deepforge.dispatch.dispatcher import ToolDispatcher
from deepforge.mcp.config import MCPConfig, MCPToolOverride
from deepforge.mcp.tools import MCPRemoteTool, mcp_tool_name


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

    def test_deepforge_env_prefix(self, monkeypatch):
        monkeypatch.setenv("DEEPFORGE_API_KEY", "new-key")
        monkeypatch.setenv("DEEPFORGE_MODE", "yolo")
        monkeypatch.setenv("DEEPFORGE_APPROVAL", "auto")
        monkeypatch.setenv("DEEPFORGE_BROWSER_HEADLESS", "1")

        cfg = Config.from_env()

        assert cfg.api_key == "new-key"
        assert cfg.mode == Mode.YOLO
        assert cfg.approval_policy == ApprovalPolicy.AUTO
        assert cfg.browser_headless is True

    def test_legacy_codex_env_fallback(self, monkeypatch):
        monkeypatch.delenv("DEEPFORGE_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPFORGE_MODE", raising=False)
        monkeypatch.delenv("DEEPFORGE_APPROVAL", raising=False)
        monkeypatch.setenv("CODEX_API_KEY", "legacy-key")
        monkeypatch.setenv("CODEX_MODE", "plan")
        monkeypatch.setenv("CODEX_APPROVAL", "never")

        cfg = Config.from_env()

        assert cfg.api_key == "legacy-key"
        assert cfg.mode == Mode.PLAN
        assert cfg.approval_policy == ApprovalPolicy.NEVER

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

    def test_clone_filtered_preserves_stateful_tools(self, registry, mock_tool):
        registry.register(mock_tool)
        cloned = registry.clone_filtered(["echo"])
        assert cloned.get("echo") is not mock_tool
        assert cloned.get("echo").name == "echo"


class TestBrowserTools:
    class FakeRuntime:
        def __init__(self):
            self.opened = None

        def _snapshot(self):
            return BrowserSnapshot(
                url="https://example.test/",
                title="Example",
                body_text="Example page text",
                elements=[
                    BrowserElement(
                        ref="e0",
                        tag="button",
                        role="button",
                        name="Continue",
                        selector='[data-deepforge-ref="e0"]',
                    )
                ],
            )

        def open(self, url, *, new_page=False):
            self.opened = (url, new_page)
            return self._snapshot()

        def snapshot(self, *, max_elements=None):
            return self._snapshot()

    def test_browser_open_tool_uses_runtime_and_formats_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "audit_dir", tmp_path)
        runtime = self.FakeRuntime()
        tool = BrowserOpenTool(runtime)
        tc = ToolCall(id="1", function_name="browser_open", arguments={"url": "example.test"})

        result = tool.execute(tc)

        assert result.success
        assert runtime.opened == ("example.test", False)
        assert "URL: https://example.test/" in result.content
        assert "ref=e0" in result.content
        assert "Continue" in result.content

    def test_browser_snapshot_tool_is_read_only(self):
        tool = BrowserSnapshotTool(self.FakeRuntime())
        tc = ToolCall(id="1", function_name="browser_snapshot", arguments={})

        result = tool.execute(tc)

        assert tool.is_read
        assert not tool.requires_approval
        assert result.success
        assert "Interactive elements" in result.content

    def test_browser_eval_requires_approval(self):
        tool = BrowserEvalTool(self.FakeRuntime())
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.SUGGEST)

        result = gate.check(tool)

        assert tool.requires_approval
        assert result.decision == GateDecision.PROMPT


# ═══════════════════════════════════════════════════════════════════
# MCP Integration (unit tests, no MCP SDK required)
# ═══════════════════════════════════════════════════════════════════

class TestMCPConfig:
    def test_load_mcp_config_from_custom_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
        cfg_path = tmp_path / "mcp.yaml"
        cfg_path.write_text(
            """
mcp:
  enabled: true
  servers:
    github:
      transport: streamable-http
      url: https://example.test/mcp
      headers:
        Authorization: env:GITHUB_TOKEN
      retry_attempts: 2
      tool_overrides:
        create_issue:
          is_write: true
          requires_approval: true
""",
            encoding="utf-8",
        )

        cfg = MCPConfig.load(cfg_path)
        assert cfg.enabled
        assert cfg.path == cfg_path
        assert len(cfg.servers) == 1
        server = cfg.servers[0]
        assert server.name == "github"
        assert server.transport == "streamable_http"
        assert server.headers["Authorization"] == "secret-token"
        assert server.retry_attempts == 2
        assert server.tool_overrides["create_issue"].is_write is True


class TestMCPTools:
    def test_mcp_tool_name_is_function_safe(self):
        assert mcp_tool_name("GitHub API", "create-issue") == "mcp__github_api__create_issue"

    def test_unknown_mcp_tool_requires_approval(self):
        remote_tool = {
            "name": "create_issue",
            "description": "Create an issue",
            "inputSchema": {"type": "object", "properties": {}},
        }
        tool = MCPRemoteTool(MagicMock(), "github", remote_tool, MCPToolOverride())
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.SUGGEST)
        result = gate.check(tool)
        assert tool.requires_approval
        assert result.decision == GateDecision.PROMPT

    def test_readonly_mcp_tool_override_can_skip_approval(self):
        remote_tool = {
            "name": "search",
            "description": "Search GitHub",
            "inputSchema": {"type": "object", "properties": {}},
        }
        tool = MCPRemoteTool(
            MagicMock(),
            "github",
            remote_tool,
            MCPToolOverride(is_read=True, is_write=False, requires_approval=False),
        )
        gate = ApprovalGate(mode=Mode.AGENT, policy=ApprovalPolicy.SUGGEST)
        result = gate.check(tool)
        assert not tool.requires_approval
        assert result.decision == GateDecision.ALLOW


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
        from deepforge.sub_agent import SubAgentConfig
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
    not (
        os.environ.get("DEEPFORGE_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("CODEX_API_KEY")
    ),
    reason="DeepSeek API key not set — set it to run live tests",
)


class TestLiveAgent:
    @live
    def test_session_lifecycle(self, temp_workspace):
        """Test full session lifecycle with real API."""
        from deepforge.session import Session, SessionConfig

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
        from deepforge.session import Session, SessionConfig

        # Create a test file
        test_file = temp_workspace / "test.txt"
        test_file.write_text("Hello from DeepForge!")

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
        assert "Hello from DeepForge" in response.content
        assert len(response.tool_results) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
