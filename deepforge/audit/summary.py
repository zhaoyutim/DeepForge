"""Small summary-only audit log.

The audit log intentionally stores concise event summaries instead of raw tool
payloads, screenshots, page contents, or command output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from deepforge.config import config


def _limit(value: str, max_chars: int = 500) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15] + "... (truncated)"


@dataclass
class SummaryAuditLog:
    """Append-only JSONL audit log that stores only concise summaries."""

    directory: Optional[Path] = None

    def __post_init__(self) -> None:
        self.directory = self.directory or config.audit_dir

    def record(
        self,
        event_type: str,
        summary: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if not config.audit_enabled:
            return

        directory = Path(self.directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / time.strftime("%Y-%m-%d.jsonl")
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": _limit(event_type, 80),
            "summary": _limit(summary, 500),
            "metadata": {
                str(key): _limit(str(value), 200)
                for key, value in (metadata or {}).items()
            },
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


_audit_log: Optional[SummaryAuditLog] = None


def get_audit_log() -> SummaryAuditLog:
    """Return the process-wide summary audit logger."""
    global _audit_log
    if _audit_log is None:
        _audit_log = SummaryAuditLog()
    return _audit_log