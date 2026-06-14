"""Persistent JSONL session logging for DeepForge."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from deepforge.config import config
from deepforge.types import Message, ToolResult


@dataclass
class SessionLog:
    """Append-only session log that stores messages and compact tool metadata."""

    directory: Optional[Path] = None
    session_id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + str(uuid.uuid4())[:8])

    def __post_init__(self) -> None:
        self.directory = Path(self.directory or config.session_log_dir).expanduser()

    @property
    def path(self) -> Path:
        return self.directory / f"{self.session_id}.jsonl"

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        if not config.session_log_enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": event_type,
            "payload": payload,
        }
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_user(self, content: str) -> None:
        self.record("user", {"content": content})

    def record_response(
        self,
        content: str,
        tool_results: list[ToolResult],
        *,
        latency_ms: float,
        tokens: int,
        changed_files: Optional[list[str]] = None,
    ) -> None:
        self.record("assistant", {
            "content": content,
            "latency_ms": latency_ms,
            "tokens": tokens,
            "changed_files": changed_files or [],
            "tools": [
                {
                    "name": result.tool_name,
                    "success": result.success,
                    "error": result.error,
                    "content_preview": result.content[:500],
                }
                for result in tool_results
            ],
        })

    def record_compaction(self, summary: str) -> None:
        self.record("compaction", {"summary": summary})

    @classmethod
    def load_messages(cls, path: Path) -> list[Message]:
        messages: list[Message] = []
        if not path.exists():
            raise FileNotFoundError(path)
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = entry.get("payload") or {}
                event_type = entry.get("type")
                if event_type == "user":
                    messages.append(Message.user(str(payload.get("content", ""))))
                elif event_type == "assistant":
                    messages.append(Message.assistant(str(payload.get("content", ""))))
                elif event_type == "compaction":
                    messages.append(Message.system(str(payload.get("summary", ""))))
        return messages


def latest_session_log(directory: Optional[Path] = None) -> Optional[Path]:
    root = Path(directory or config.session_log_dir).expanduser()
    if not root.exists():
        return None
    logs = sorted(root.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    return logs[0] if logs else None
