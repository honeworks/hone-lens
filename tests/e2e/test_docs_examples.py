"""The documentation runs: every ```python block of README.md and docs/*.md (examples/*.py: AC-20).

The blocks of one Markdown file run in order as one script (later blocks may use names from earlier ones),
in a fresh subprocess with an empty temporary folder as the working directory. A block right after an
`<!-- not-run -->` line is illustrative only (it needs a real model server) and is skipped; keep those rare.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
NOT_RUN = "<!-- not-run -->"
MAX_NOT_RUN = 4
_FENCE = re.compile(r"^```(\w*)\s*$")
_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
_REPO = re.compile(r"https://github\.com/honeworks/hone-lens/(?:blob|tree)/main/(.+)")


def python_blocks(path: Path) -> tuple[list[tuple[int, str]], int]:
    """(line number, code) of each runnable ```python block, and how many were marked not-run."""
    blocks: list[tuple[int, str]] = []
    skipped = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        match = _FENCE.match(lines[i])
        if not match or not match.group(1):
            i += 1
            continue
        end = next(j for j in range(i + 1, len(lines)) if lines[j].strip() == "```")
        before = next((lines[j].strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
        if match.group(1) == "python":
            if before == NOT_RUN:
                skipped += 1
            else:
                blocks.append((i + 1, "\n".join(lines[i + 1 : end])))
        i = end + 1
    return blocks, skipped


def run_python(script: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)], cwd=cwd, capture_output=True, text=True, timeout=110, check=False
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_doc_code_blocks_run(doc: Path, tmp_path: Path) -> None:
    blocks, _ = python_blocks(doc)
    if not blocks:
        pytest.skip(f"{doc.name} has no runnable python blocks")
    script = tmp_path / "doc_blocks.py"
    script.write_text(
        "\n\n".join(f"# --- {doc.name} line {line}\n{code}" for line, code in blocks), encoding="utf-8"
    )
    work = tmp_path / "work"
    work.mkdir()
    result = run_python(script, work)
    assert result.returncode == 0, f"{doc.name} failed:\n{result.stdout[-3000:]}\n{result.stderr[-5000:]}"


def test_docs_and_examples_exist() -> None:
    names = {p.name for p in DOCS}
    expected = {"concepts.md", "sources.md", "detectors.md", "analysis.md", "reports.md", "adapters.md"}
    assert names >= {"README.md", "cli.md", "records.md", *expected}
    assert (ROOT / "examples" / "README.md").exists()


def test_not_run_blocks_are_rare() -> None:
    skipped = sum(python_blocks(doc)[1] for doc in DOCS)
    assert skipped <= MAX_NOT_RUN, f"{skipped} python blocks are marked not-run; make them runnable"


def test_relative_links_resolve() -> None:
    """Relative links, and the README's absolute links into this repository (PyPI shows the README, where
    relative links break), point at files that exist."""
    broken = [
        f"{doc.name}: {target}"
        for doc in DOCS
        for target in _LINK.findall(doc.read_text(encoding="utf-8"))
        if not _REPO.match(target) and "://" not in target and not (doc.parent / target).exists()
    ]
    readme = _LINK.findall((ROOT / "README.md").read_text(encoding="utf-8"))
    broken += [t for t in readme if (m := _REPO.match(t)) and not (ROOT / m.group(1)).exists()]
    broken += [f"README.md: relative link {t}" for t in readme if "://" not in t]
    assert broken == []
