"""Wheel smoke test: run the README quickstart, exactly as written, with only the wheel installed.

scripts/check.sh runs this with a fresh venv's python (no extras, no pytest). It executes the first
```python block under "## Quickstart" in README.md in an empty temporary folder and checks the outcome.
"""

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

README = Path(__file__).resolve().parents[1] / "README.md"


def quickstart_code() -> str:
    text = README.read_text(encoding="utf-8")
    section = text.split("## Quickstart", 1)[1]
    match = re.search(r"```python\n(.*?)```", section, re.DOTALL)
    if match is None:
        sys.exit("README.md has no ```python block under ## Quickstart")
    return match.group(1)


def main() -> None:
    code = quickstart_code()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        namespace: dict[str, Any] = {"__name__": "__main__"}
        exec(compile(code, "README.md#quickstart", "exec"), namespace)  # noqa: S102 - our own README
        report, t, f = namespace["report"], namespace["t"], namespace["f"]
        findings = report.findings
        assert len(findings) == 4, [x.title for x in findings]
        assert f.cause.kind == "prompt_section" and f.cause.target == "format_example"
        assert t.confirmed and t.metric_after < t.metric_before
        html = Path("report.html").read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>") and f'id="{f.id}"' in html
        os.chdir(README.parent)
    print("README quickstart ok:", len(findings), "findings; replay test confirmed")


if __name__ == "__main__":
    main()
