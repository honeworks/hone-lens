"""The `hone-lens` command: the same funnel from a shell or a CI job.

What: the CLI (`pip install "hone-lens[cli]"`): `ingest`, `analyze`, `findings`, `explain`,
`set-status` and `report`, each on a workspace folder (`-w`), with `--json` output for scripts and exit
codes 0 (ok), 1 (a hone-lens error, message on stderr) and 2 (usage).

How: each command is one `Workspace` call. Ports are chosen by name from entry points
(`--llm openai:gpt-4o-mini`, `--embedder NAME[:ARG]`, `--replayer NAME[:ARG]`) and passed to every command
that needs them; this example uses only the stages that need no model (`review`, `label` and `test` work
the same way with `--llm` / `--replayer`; see docs/cli.md). It runs the installed command with
`subprocess`, exactly as a shell would.

Why: analysis often runs unattended (nightly, after a deploy) and its results feed other tools. `--json`
and exit codes make that possible without writing Python.

Run: python examples/command_line.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hone_lens.testing import synthetic_runs

# the command installed with hone-lens, next to this Python (or anywhere on PATH)
HONE_LENS = shutil.which("hone-lens", path=str(Path(sys.executable).parent)) or "hone-lens"


def hone_lens(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("$ hone-lens", " ".join(args))
    return subprocess.run([HONE_LENS, *args], capture_output=True, text=True, check=check)  # noqa: S603 - our CLI


with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=300, plant=["truncation", "latency_regression_after_v4"])
    ws = str(Path(tmp) / "lens")

    print(hone_lens("ingest", f"hone:{runs.root}", f"otlp:{runs.otlp_path}", "-w", ws).stdout)

    out = hone_lens("analyze", "song_ideas", "--stages", "stats", "--json", "-w", ws).stdout
    findings = json.loads(out)
    print([(f["id"], f["detector"]) for f in findings], "\n")
    regression = next(f["id"] for f in findings if f["detector"] == "regression")

    print(hone_lens("explain", regression, "-w", ws).stdout)
    note = "prompt v4 is slower"
    print(hone_lens("set-status", regression, "confirmed_by_human", "--note", note, "-w", ws).stdout)
    listed = json.loads(hone_lens("findings", "--status", "confirmed_by_human", "--json", "-w", ws).stdout)
    assert [f["id"] for f in listed] == [regression]

    html = Path(tmp) / "report.html"
    print(hone_lens("report", "song_ideas", "--html", str(html), "-w", ws).stdout)
    assert html.exists()

    # Errors: exit code 1 and a message that says what to do.
    failed = hone_lens("explain", "F-9999", "-w", ws, check=False)
    print("exit code", failed.returncode, "|", failed.stderr.strip())
    assert failed.returncode == 1
