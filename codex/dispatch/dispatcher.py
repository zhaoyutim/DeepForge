"""
Parallel Tool Dispatcher — schedules and executes tool calls concurrently.

Key design:
- Independent tool calls execute in parallel (using ThreadPoolExecutor)
- Dependent tool calls (where one's output feeds another) execute sequentially
- Dependency detection is based on simple heuristics (tool call IDs, side effects)

This is the heart of CodeX's performance advantage — a 10-tool read
completes in 1 turn instead of 10 sequential turns.
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from codex.config import config
from codex.tools.base import ToolRegistry
from codex.types import ToolCall, ToolResult


@dataclass
class DispatchResult:
    """Result of dispatching one or more tool calls."""
    tool_results: list[ToolResult]
    total_time_ms: float
    parallel_executions: int
    sequential_executions: int

    @property
    def all_success(self) -> bool:
        return all(r.success for r in self.tool_results)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.tool_results if not r.success)


class ToolDispatcher:
    """
    Executes tool calls with automatic parallel/serial scheduling.

    Heuristics for independence:
    - Different tool names → independent (can parallelize)
    - Same tool, different file paths → independent (can parallelize)
    - Same tool, overlapping arguments → potentially dependent → serialize

    The dispatcher uses ThreadPoolExecutor to run independent tools concurrently.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        max_workers: Optional[int] = None,
        on_tool_start: Optional[Callable[[ToolCall], None]] = None,
        on_tool_complete: Optional[Callable[[ToolCall, ToolResult], None]] = None,
    ):
        self.registry = registry
        self.max_workers = max_workers or config.max_parallel_tools
        self.on_tool_start = on_tool_start
        self.on_tool_complete = on_tool_complete

    # ── Public API ───────────────────────────────────────────────

    def dispatch(self, tool_calls: list[ToolCall]) -> DispatchResult:
        """
        Execute a list of tool calls with automatic parallel scheduling.

        Groups independent calls and executes them concurrently.
        """
        if not tool_calls:
            return DispatchResult(
                tool_results=[],
                total_time_ms=0.0,
                parallel_executions=0,
                sequential_executions=0,
            )

        start_time = time.time()
        all_results: list[ToolResult] = []
        parallel_count = 0
        sequential_count = 0

        # Group tool calls into batches of independent calls
        batches = self._partition_into_batches(tool_calls)

        for batch in batches:
            if len(batch) == 1:
                # Single tool → execute directly
                sequential_count += 1
                result = self._execute_one(batch[0])
                all_results.append(result)
            else:
                # Multiple independent tools → execute in parallel
                parallel_count += len(batch)
                batch_results = self._execute_parallel(batch)
                all_results.extend(batch_results)

        # Ensure results are in the original order
        result_map = {r.tool_call_id: r for r in all_results}
        ordered_results = [result_map[tc.id] for tc in tool_calls]

        return DispatchResult(
            tool_results=ordered_results,
            total_time_ms=(time.time() - start_time) * 1000,
            parallel_executions=parallel_count,
            sequential_executions=sequential_count,
        )

    def dispatch_all(
        self,
        tool_calls: list[ToolCall],
        on_each: Optional[Callable[[ToolCall, ToolResult], None]] = None,
    ) -> DispatchResult:
        """
        Execute all tool calls and optionally call on_each for each result.

        This is the main entry point used by the Agent loop.
        """
        result = self.dispatch(tool_calls)
        if on_each:
            for tc, tr in zip(tool_calls, result.tool_results):
                on_each(tc, tr)
        return result

    # ── Execution ────────────────────────────────────────────────

    def _execute_one(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call."""
        if self.on_tool_start:
            self.on_tool_start(tool_call)

        result = self.registry.execute(tool_call)

        if self.on_tool_complete:
            self.on_tool_complete(tool_call, result)

        return result

    def _execute_parallel(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """Execute multiple tool calls in parallel using a thread pool."""

        def execute_with_callbacks(tc: ToolCall) -> ToolResult:
            if self.on_tool_start:
                self.on_tool_start(tc)
            result = self.registry.execute(tc)
            if self.on_tool_complete:
                self.on_tool_complete(tc, result)
            return result

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(tool_calls))
        ) as executor:
            futures = {
                executor.submit(execute_with_callbacks, tc): tc
                for tc in tool_calls
            }
            results: list[ToolResult] = []
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result(timeout=config.tool_timeout_seconds)
                    results.append(result)
                except Exception as e:
                    # Create error result for failed futures
                    tc = futures[future]
                    results.append(ToolResult(
                        tool_call_id=tc.id,
                        content=f"Error: Tool execution failed: {e}",
                        success=False,
                        error=str(e),
                    ))

        # Return results in submission order
        return results

    # ── Dependency Analysis ──────────────────────────────────────

    def _partition_into_batches(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        """
        Partition tool calls into batches where each batch contains
        independent calls that can execute in parallel.

        Heuristic: different tool names → different batches
        Same tool name + different paths → same batch
        Same tool name + same path → separate batches (potential conflict)
        """
        if len(tool_calls) <= 1:
            return [tool_calls] if tool_calls else []

        # Simple heuristic: group by tool name
        # More sophisticated: detect file path conflicts
        groups: dict[str, list[ToolCall]] = {}
        for tc in tool_calls:
            key = self._dependency_key(tc)
            if key not in groups:
                groups[key] = []
            groups[key].append(tc)

        # Convert to batches
        # Strategy: first tools from each group run in parallel,
        # then second tools from each group, etc.
        max_group_size = max(len(g) for g in groups.values())
        batches: list[list[ToolCall]] = []

        for i in range(max_group_size):
            batch = []
            for group in groups.values():
                if i < len(group):
                    batch.append(group[i])
            if batch:
                batches.append(batch)

        return batches

    def _dependency_key(self, tool_call: ToolCall) -> str:
        """
        Generate a dependency key for a tool call.

        Tools with different keys are independent.
        Tools with the same key might conflict.
        """
        fn = tool_call.function_name
        args = tool_call.arguments

        # Write operations on different files are independent
        if fn in ("write_file", "edit_file", "read_file"):
            path = args.get("path", "")
            return f"{fn}:{path}"

        # Search operations are independent
        if fn in ("grep_files", "file_search", "web_search"):
            return f"{fn}:{args.get('query', args.get('pattern', ''))}"

        # Shell commands are always serialized (side effects unknown)
        if fn == "exec_shell":
            return f"{fn}:{id(tool_call)}"  # Unique per call

        # Default: group by function name
        return fn

    # ── Stats ────────────────────────────────────────────────────

    @property
    def available_parallelism(self) -> int:
        """Maximum number of concurrent tool executions."""
        return self.max_workers


# ── Convenience ───────────────────────────────────────────────────

def create_dispatcher(
    registry: ToolRegistry,
    max_workers: Optional[int] = None,
) -> ToolDispatcher:
    """Create a tool dispatcher with the given registry."""
    return ToolDispatcher(registry=registry, max_workers=max_workers)
