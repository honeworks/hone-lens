"""Machine-readable report: findings and clusters as one JSON document."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter

from hone_lens.findings import Finding

_FINDING = TypeAdapter(Finding)


def finding_json(f: Finding) -> dict[str, Any]:
    """One finding as JSON-ready data (without its full list of affected span ids)."""
    return _FINDING.dump_python(f, mode="json", exclude={"affected_ids"})


def render(workflow: str | None, findings: Sequence[Finding], clusters: Sequence[dict[str, Any]]) -> str:
    """`{"workflow", "findings": [...], "clusters": [...]}`, without cluster members and affected span ids."""
    data = {
        "workflow": workflow,
        "findings": [finding_json(f) for f in findings],
        "clusters": [{k: v for k, v in c.items() if k != "members"} for c in clusters],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
