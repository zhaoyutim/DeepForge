"""Workspace sandbox and output helpers for DeepForge tools."""

from __future__ import annotations

import fnmatch
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from deepforge.config import config


class SandboxError(ValueError):
    """Raised when a tool attempts to access a disallowed path."""


def workspace_root() -> Path:
    return Path(config.workspace).expanduser().resolve()


def display_path(path: Path) -> str:
    root = workspace_root()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def resolve_workspace_path(path_value: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
    """Resolve a user-provided path and require it to stay inside the workspace."""
    if path_value is None or str(path_value).strip() == "":
        raise SandboxError("Path is required")

    root = workspace_root()
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        resolved = candidate.parent.resolve() / candidate.name

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SandboxError(f"Path is outside the workspace: {path_value}") from exc

    _check_ignored(resolved)
    return resolved


def resolve_workspace_dir(path_value: str | os.PathLike[str] | None = None, *, must_exist: bool = True) -> Path:
    value = "." if path_value in (None, "") else path_value
    path = resolve_workspace_path(value, must_exist=must_exist)
    if must_exist and not path.is_dir():
        raise SandboxError(f"Not a directory: {value}")
    return path


def _check_ignored(path: Path) -> None:
    ignored_patterns = list(config.tool_ignored_paths or [])
    if not ignored_patterns:
        return

    root = workspace_root()
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()

    for pattern in ignored_patterns:
        normalized = str(pattern).strip().strip("/")
        if not normalized:
            continue
        if fnmatch.fnmatch(rel, normalized) or fnmatch.fnmatch(rel, f"{normalized}/**"):
            raise SandboxError(f"Path is ignored by DeepForge config: {rel}")


def atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically in the target directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    except Exception:
        with contextlib_suppress_file(temp_name):
            pass
        raise


@contextmanager
def contextlib_suppress_file(path: str) -> Iterator[None]:
    try:
        yield
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def limit_text(text: str, *, max_chars: int | None = None, label: str = "output") -> str:
    """Clamp large tool outputs while preserving the beginning and end."""
    max_chars = max_chars if max_chars is not None else config.max_tool_output_chars
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = f"\n... ({label} truncated: {len(text) - max_chars} chars omitted) ...\n"
    head_len = max(1, (max_chars - len(marker)) // 2)
    tail_len = max(1, max_chars - len(marker) - head_len)
    return text[:head_len] + marker + text[-tail_len:]
