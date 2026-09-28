"""Change detection for file-based sources: a file is re-read when its mtime or size changed."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from hone_lens.errors import SourceError


def changed_files(paths: Iterable[Path], cursor: Mapping[str, Any]) -> tuple[list[Path], dict[str, Any]]:
    """The files whose `[mtime_ns, size]` differs from `cursor`, and the cursor to store afterwards."""
    changed: list[Path] = []
    marks: dict[str, Any] = {}
    for path in paths:
        stat = path.stat()
        marks[str(path)] = [stat.st_mtime_ns, stat.st_size]
        if cursor.get(str(path)) != marks[str(path)]:
            changed.append(path)
    return changed, marks


def read_json_or_lines(path: Path, what: str) -> list[Any]:
    """The JSON documents in `path`: the whole file as one document, else one document per line."""
    text = path.read_text(encoding="utf-8")
    try:
        return [json.loads(text)]
    except ValueError:
        try:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError as e:
            raise SourceError(f"{path} is not {what} ({e})") from e
