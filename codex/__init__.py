"""
CodeX - A faithful Python replica of the CodeWhale architecture.

Core subsystems:
- Tool Dispatch: parallel execution of independent tool calls
- Context Window: prefix-cache-aware context management + compaction
- Approval Gate: mode × policy matrix for tool execution control
- Agent Loop: model → tool dispatch → result feedback cycle
- Sub-Agent: isolated child sessions with independent context
"""

__version__ = "0.1.0"
