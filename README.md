# CodeX

**CodeWhale Architecture fully implemented in Python, powered by DeepSeek API.**

CodeX is a faithful replica of the CodeWhale architecture — the AI coding agent that manages tool calling, context windows, approval gates, and sub-agents — all in pure Python.

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
- 11 built-in tools: `read_file`, `write_file`, `edit_file`, `list_dir`, `grep_files`, `file_search`, `web_search`, `fetch_url`, `exec_shell`, `git_status`, `git_diff`, `git_log`
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

### 5. Constitutional Rule Engine
- 8-tier hierarchy resolver (Article VII)
- Truth enforcement (Article II)
- Verification mandate (Article V)

## Quick Start

### 1. Set your DeepSeek API key
```bash
export DEEPSEEK_API_KEY="sk-your-key-here"
```

### 2. Install dependencies
```bash
cd codex_custom
pip install -e .
```

### 3. Run
```bash
# Interactive mode
python cli.py

# Offline visualization of the TUI core flow (no API key required)
python visualize_tui.py

# With specific mode
python cli.py --mode yolo
python cli.py --mode plan

# One-shot command
python cli.py -c "read README.md and summarize"

# Custom workspace
python cli.py --workspace /path/to/project
```

### 4. In-session commands
```
/mode agent|plan|yolo    Switch operating mode
/policy auto|suggest     Change approval policy
/tools                   List available tools
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
from codex.session import Session, SessionConfig
from codex.config import Mode, ApprovalPolicy

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
from codex.sub_agent import parallel_investigate

# Run 3 investigations in parallel
results = parallel_investigate([
    ("read-config", "Read pyproject.toml and summarize dependencies", ["read_file"]),
    ("check-structure", "List the top-level directory structure", ["list_dir"]),
    ("recent-commits", "Show the last 5 git commits", ["git_log"]),
])

for r in results:
    print(f"{r.name}: {r.content[:200]}...")
```

## Requirements

- Python >= 3.10
- DeepSeek API key ([get one here](https://platform.deepseek.com))

## License

MIT
