"""AC-20: every `examples/*.py` runs, opens with a What / How / Why docstring and is listed in
`examples/README.md`, which lists nothing else."""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.py"))
INDEX = EXAMPLES_DIR / "README.md"
# | n | [file.py](file.py) | concept | one sentence | design section |
_ROW = re.compile(r"^\| (\d+) \| \[(\w+\.py)\]\(\2\) \| [^|]*\w[^|]* \| [^|]*\w[^|]*\. \| [^|]*§[^|]* \|$")


OFFLINE = "import socket\n\ndef _no_network(*args, **kwargs):\n    raise OSError('examples run offline')\n\nsocket.socket.connect = _no_network\n"


def run_offline(script: Path, cwd: Path, site: Path) -> subprocess.CompletedProcess[str]:
    """Run `script` in a fresh Python whose sockets cannot connect (also in the processes it starts)."""
    site.mkdir()
    (site / "sitecustomize.py").write_text(OFFLINE)
    env = {**os.environ, "PYTHONPATH": str(site)}
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
        check=False,
    )


def _docstring(example: Path) -> str:
    return ast.get_docstring(ast.parse(example.read_text(encoding="utf-8"))) or ""


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_ac20_example_runs_offline(example: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    result = run_offline(example, work, tmp_path / "site")
    assert result.returncode == 0, f"{example.name} failed:\n{result.stdout[-3000:]}\n{result.stderr[-5000:]}"
    assert result.stdout.strip(), f"{example.name} printed nothing"
    assert list(work.iterdir()) == [], f"{example.name} wrote into its working directory"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_ac20_example_asserts_its_key_facts(example: Path) -> None:
    asserts = [
        n for n in ast.walk(ast.parse(example.read_text(encoding="utf-8"))) if isinstance(n, ast.Assert)
    ]
    assert len(asserts) >= 2, f"{example.name} checks {len(asserts)} facts; assert the key ones"


def test_ac20_offline_guard_works(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("import socket\nsocket.create_connection(('127.0.0.1', 9))\n")
    result = run_offline(probe, tmp_path, tmp_path / "site")
    assert result.returncode != 0 and "examples run offline" in result.stderr


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_ac20_example_opens_with_what_how_why(example: Path) -> None:
    doc = _docstring(example)
    heads = [line.split(":")[0] for line in doc.splitlines() if re.match(r"^(What|How|Why|Run):", line)]
    assert heads == ["What", "How", "Why", "Run"], f"{example.name}: docstring sections are {heads}"
    assert f"Run: python examples/{example.name}" in doc
    sections = re.split(r"^(?:What|How|Why):", doc, flags=re.MULTILINE)
    assert all(len(part.strip()) > 40 for part in sections[1:]), f"{example.name}: a section is too short"


def test_ac20_readme_indexes_every_example_in_reading_order() -> None:
    lines = [line for line in INDEX.read_text(encoding="utf-8").splitlines() if re.match(r"^\| \d+ \|", line)]
    rows = [m.groups() for line in lines if (m := _ROW.match(line))]
    assert len(rows) == len(lines), "every row needs a file link, a concept, a sentence and a design section"
    assert [int(n) for n, _ in rows] == list(range(1, len(rows) + 1)), "rows must be numbered 1, 2, ..."
    listed = [name for _, name in rows]
    assert sorted(listed) == [p.name for p in EXAMPLES], "every example listed once, nothing else"
    assert listed[0] == "quickstart.py"


def test_ac20_spec_examples_exist() -> None:
    spec = {
        "quickstart.py",
        "ingest_sources.py",
        "flow_run_folders.py",
        "stats_detectors.py",
        "output_clusters.py",
        "describe_with_budget.py",
        "review_with_scripted_io.py",
        "explain_cause.py",
        "replay_test.py",
        "findings_lifecycle.py",
        "reports.py",
        "custom_detector.py",
        "plain_otel.py",
        "step_rerunner.py",
    }  # design/current.md §11
    assert spec <= {p.name for p in EXAMPLES}
