"""The CLI, as a user runs it: ingest, analyze, findings, explain, set-status, report, errors, exit codes."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hone_lens.cli import app
from hone_lens.testing import synthetic_runs

pytestmark = pytest.mark.e2e
runner = CliRunner()


def run(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def test_cli_flow(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=300, plant=["truncation", "latency_regression_after_v4"])
    ws = str(tmp_path / "lens")
    code, out = run("ingest", f"hone:{runs.root}", f"otlp:{runs.otlp_path}", "-w", ws)
    assert code == 0 and "2160 new" in out and "600 new" in out  # 7 spans a run, 60 resume calls

    code, out = run("analyze", "song_ideas", "--stages", "stats", "-w", ws)
    assert code == 0 and out.startswith("hone-lens: song_ideas") and "cut off" in out and "v3 -> v4" in out

    code, out = run(
        "analyze", "song_ideas", "--stages", "stats", "--since", "2026-08-01T00:30:00Z", "--json", "-w", ws
    )
    data = json.loads(out)
    assert code == 0 and {f["detector"] for f in data} >= {"truncation", "regression"}
    regression = next(f["id"] for f in data if f["detector"] == "regression")

    code, out = run("findings", "--json", "-w", ws)
    assert code == 0 and all(f["status"] == "new" for f in json.loads(out))
    code, out = run("findings", "-w", ws)
    assert code == 0 and regression in out

    code, out = run("explain", regression, "-w", ws)
    assert code == 0 and out.startswith(f"{regression}: prompt_section task (suspected)")
    code, out = run("explain", regression, "--json", "-w", ws)
    assert json.loads(out)["cause"]["target"] == "task"

    code, out = run("set-status", regression, "dismissed", "--note", "expected: v4 is bigger", "-w", ws)
    assert code == 0 and out.strip() == f"{regression}: dismissed"
    code, out = run("findings", "--status", "dismissed", "--json", "-w", ws)
    assert [f["id"] for f in json.loads(out)] == [regression]

    html = tmp_path / "r.html"
    code, out = run("report", "song_ideas", "--html", str(html), "-w", ws)
    assert code == 0 and out.strip() == f"wrote {html}" and html.read_text().startswith("<!doctype html>")
    code, out = run("report", "song_ideas", "--json", "-w", ws)
    assert code == 0 and json.loads(out)["findings"]
    code, out = run("report", "song_ideas", "-w", ws)
    assert code == 0 and regression not in out  # dismissed findings are left out


def test_cli_errors_and_exit_codes(tmp_path: Path) -> None:
    ws = str(tmp_path / "lens")
    code, out = run("ingest", "nope:x", "-w", ws)
    assert code == 1 and "error: unknown source 'nope:x'" in out
    code, out = run("explain", "F-0404", "-w", ws)
    assert code == 1 and "no finding 'F-0404'" in out
    code, out = run("analyze", "--llm", "missing:model", "-w", ws)
    assert code == 1 and "no hone.text_clients entry point 'missing'" in out
    code, out = run("test", "F-0001", "-w", ws)
    assert code == 1 and "test needs a Replayer" in out
    code, out = run("review", "song_ideas", "-w", ws)
    assert code == 1 and "review needs a person at a terminal" in out  # never waits without a TTY
    code, out = run("label", "song_ideas", "--yes", "-w", ws)
    assert code == 1 and "no taxonomy for 'song_ideas'" in out
    code, _ = run("analyze", "--no-such-option")
    assert code == 2
    code, out = run("findings", "--status", "open", "-w", ws)
    assert code == 2 and "unknown status" in out


def test_installed_script() -> None:
    script = shutil.which("hone-lens") or str(Path(sys.executable).parent / "hone-lens")
    out = subprocess.run([script, "--help"], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and "analyze" in out.stdout
