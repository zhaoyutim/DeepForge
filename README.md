# DeepForge

**CodeWhale Architecture fully implemented in Python, powered by DeepSeek API.**

DeepForge is a faithful replica of the CodeWhale architecture — the AI coding agent that manages tool calling, context windows, approval gates, and sub-agents — all in pure Python.

## Architecture

```
┌──────────────────────────────────────────┐
│  CLI (cli.py)          Interactive REPL  │
├──────────────────────────────────────────┤
│  Session (session.py)  Lifecycle manager │
├──────────────────────────────────────────┤
│  Agent (agent.py)      Core loop         │
│    Model → Dispatch → Results → Repeat   │
├──────────────────────────────────────────┤
│  Dispatcher            Parallel executor │
│  Approval Gate         Mode × Policy     │
│  Context Window        Token + Compaction│
├──────────────────────────────────────────┤
│  Tools                 File, Search, Git │
│  DeepSeek Client       API wrapper       │
└──────────────────────────────────────────┘
```

## Core Subsystems

### 1. Tool Calling with Parallel Dispatch
- 12 built-in tools: `read_file`, `write_file`, `edit_file`, `list_dir`, `grep_files`, `file_search`, `web_search`, `fetch_url`, `exec_shell`, `git_status`, `git_diff`, `git_log`
- Independent tool calls execute in parallel via `ThreadPoolExecutor`
- Dependency-aware partitioning prevents conflicts

### 2. Context Window Management
- Token counting via `tiktoken` (cl100k_base encoding)
- Compaction trigger at 60% usage
- Prefix-cache-aware architecture (append, don't rewrite)
- 1M token window tracking

### 3. Approval Gate (Mode × Policy Matrix)
| Mode  | AUTO      | SUGGEST       | NEVER |
|-------|-----------|---------------|-------|
| AGENT | Allowed   | Reads:Allow, Writes:Prompt | Blocked |
| PLAN  | Blocked   | Blocked       | Blocked |
| YOLO  | Allowed   | Allowed       | Blocked |

### 4. Sub-Agent System
- Isolated context windows (no parent bloat)
- Tool restriction (allowlist)
- Parallel execution via thread pool
- Depth limits for recursion

### 5. MCP Client Integration
- DeepForge can connect to external MCP servers and register their tools as local DeepForge tools
- Supports `stdio`, `streamable_http`, and legacy `sse` transports
- Discovers MCP tools, resources, resource templates, and prompts
- Exposes resources/prompts through helper tools such as `mcp__github__read_resource` and `mcp__github__get_prompt`
- Unknown or destructive MCP tools require approval in `suggest` policy

### 6. Browser Computer Use
- Optional Playwright/CDP browser runtime for structured browser automation
- Tools: `browser_open`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_select`, `browser_wait`, `browser_screenshot`, `browser_eval`, `browser_close`
- Uses an isolated browser profile under `~/.deepforge/browser-profile` by default
- Returns DOM-derived page snapshots with stable refs like `e0` instead of relying on raw screen coordinates
- Writes summary-only audit events to `~/.deepforge/audit` by default

### 7. Constitutional Rule Engine
- 8-tier hierarchy resolver (Article VII)
- Truth enforcement (Article II)
- Verification mandate (Article V)

## Quick Start

### 1. Set your DeepSeek API key
```bash
export DEEPSEEK_API_KEY="sk-your-key-here"
```

`DEEPFORGE_API_KEY` is also supported if you prefer an app-specific variable name.

### 2. Install dependencies
```bash
cd /path/to/deepforge
pip install -e .
```

For browser computer use, install the optional Playwright extra and Chromium:

```bash
pip install -e '.[browser]'
python -m playwright install chromium
```

### 3. Run
```bash
# Interactive CLI (terminal REPL)
deepforge

# Interactive TUI (Rich terminal interface)
python tui.py

# Offline architecture visualization (no API key required)
python visualize_tui.py

# With specific mode
deepforge --mode yolo
deepforge --mode plan

# One-shot command
deepforge -c "read README.md and summarize"

# Custom workspace
deepforge --workspace /path/to/project

# Custom model
deepforge --model deepseek-reasoner
```

### 4. In-session commands
```
/mode agent|plan|yolo    Switch operating mode
/policy auto|suggest|never  Change approval policy
/tools                   List available tools
/mcp status              Show MCP server status
/mcp tools               List registered MCP tools
/mcp reload              Reload ~/.deepforge/mcp.yaml
/stats                   Show session statistics
/compact                 Compact context window
/help                    Show help
/exit                    Exit
```

## TUI Visualization

For a local, API-free view of how the Rich TUI connects to the core architecture, run:

```bash
python visualize_tui.py --mode agent --policy suggest
```

The visualizer renders the session surface, context pressure, TUI pipeline, approval-gate decisions, registered tools, and a live dispatcher demo using the real built-in file/search tools against the current workspace.

## Programmatic Usage

```python
from deepforge.session import Session, SessionConfig
from deepforge.config import Mode, ApprovalPolicy

# Create session
session = Session(SessionConfig(
    mode=Mode.AGENT,
    approval_policy=ApprovalPolicy.SUGGEST,
))
session.initialize()

# Send a message
response = session.send("Read the README file and tell me what this project does")
print(response.content)
print(f"Tools used: {len(response.tool_results)}")
print(f"Latency: {response.latency_ms:.0f}ms")
```

## Sub-Agent Example

```python
from deepforge.sub_agent import parallel_investigate

# Run 3 investigations in parallel
results = parallel_investigate([
    ("read-config", "Read pyproject.toml and summarize dependencies", ["read_file"]),
    ("check-structure", "List the top-level directory structure", ["list_dir"]),
    ("recent-commits", "Show the last 5 git commits", ["git_log"]),
])

for r in results:
    print(f"{r.name}: {r.content[:200]}...")
```

## MCP Configuration

MCP config is loaded from `~/.deepforge/mcp.yaml` by default. You can override it with `--mcp-config /path/to/mcp.yaml` or disable MCP for a session with `--no-mcp`.

Create the config directory:

```bash
mkdir -p ~/.deepforge
```

Example config for browser and GitHub MCP servers:

```yaml
mcp:
    enabled: true
    servers:
        browser:
            transport: stdio
            command: npx
            args: ["-y", "@playwright/mcp@latest"]
            retry_attempts: 2

        github:
            transport: stdio
            command: npx
            args: ["-y", "@modelcontextprotocol/server-github"]
            env:
                GITHUB_PERSONAL_ACCESS_TOKEN: env:GITHUB_PERSONAL_ACCESS_TOKEN
            retry_attempts: 2
            tool_overrides:
                search_repositories:
                    is_read: true
                    is_write: false
                    is_network: true
                    requires_approval: false
                create_issue:
                    is_read: false
                    is_write: true
                    is_network: true
                    requires_approval: true
```

Remote HTTP example:

```yaml
mcp:
    enabled: true
    servers:
        remote_github:
            transport: streamable_http
            url: https://example.com/mcp
            headers:
                Authorization: env:GITHUB_MCP_BEARER_TOKEN
            timeout_seconds: 30
            retry_attempts: 3
            retry_backoff_seconds: 1.5
```

MCP tools are registered as `mcp__<server>__<tool>`, for example `mcp__browser__browser_navigate`. Each connected server also gets helper tools:

```text
mcp__<server>__list_resources
mcp__<server>__read_resource
mcp__<server>__list_resource_templates
mcp__<server>__list_prompts
mcp__<server>__get_prompt
```

In `agent` mode with `suggest` policy, write/shell/unknown MCP tools ask for approval before execution. `plan` mode blocks approval-required tools, and `yolo` or `auto` pre-approves them.

## Browser Computer Use Configuration

Browser tools are registered by default, but Playwright is loaded only when a browser tool runs. Useful environment variables:

```bash
DEEPFORGE_BROWSER_ENABLED=1
DEEPFORGE_BROWSER_HEADLESS=0
DEEPFORGE_BROWSER_PROFILE_DIR=~/.deepforge/browser-profile
DEEPFORGE_BROWSER_SCREENSHOT_DIR=~/.deepforge/browser-screenshots
DEEPFORGE_BROWSER_TIMEOUT_SECONDS=15
DEEPFORGE_BROWSER_MAX_ELEMENTS=80
DEEPFORGE_AUDIT_ENABLED=1
DEEPFORGE_AUDIT_DIR=~/.deepforge/audit
```

Legacy `CODEX_*` variables are still read as fallbacks during the rename transition.

The browser runtime is intended for browser-first computer use through Playwright/CDP. macOS desktop-level Screen Recording and Accessibility permissions are not required for this browser path.

## Requirements

- Python >= 3.10
- DeepSeek API key ([get one here](https://platform.deepseek.com))
- MCP Python SDK `mcp>=1.27,<2` for MCP integration
- Optional: Playwright and Chromium for browser computer use

## License

MIT
