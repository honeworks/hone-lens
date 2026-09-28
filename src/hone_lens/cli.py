"""`hone-lens` command line (`pip install hone-lens[cli]`).

Ports are named as `NAME[:ARG]` and resolved through entry points: `--llm openai:gpt-4o-mini` loads the
`openai` entry of group `hone.text_clients` and calls it with `gpt-4o-mini`; `--embedder` uses
`hone.embedders`, `--replayer` uses `hone.replayers` (installed packages such as hone-models add theirs).
Exit codes: 0 success, 1 a hone-lens error (message on stderr), 2 a usage error.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Annotated, Any

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError as e:  # pragma: no cover - the script is installed without the extra
    raise SystemExit("the hone-lens command needs the cli extra: pip install 'hone-lens[cli]'") from e

from hone_lens.errors import HoneLensError
from hone_lens.findings import check_status
from hone_lens.report import terminal
from hone_lens.report.json import finding_json
from hone_lens.workspace import STAGES, Workspace

app = typer.Typer(
    help="Analyze AI workflow runs: findings, causes and replay-tested fixes.", no_args_is_help=True
)
console = Console()

WorkspaceOpt = Annotated[Path, typer.Option("--workspace", "-w", help="Workspace folder.")]
LlmOpt = Annotated[
    str | None, typer.Option(help="Analysis LLM: NAME[:MODEL] from entry points hone.text_clients.")
]
EmbedderOpt = Annotated[
    str | None, typer.Option(help="Embedder: NAME[:MODEL] from entry points hone.embedders.")
]
BudgetOpt = Annotated[str | None, typer.Option(help="LLM budget, e.g. 2usd, '5000 tokens', '40 calls'.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON.")]
DEFAULT_WORKSPACE = Path(".hone/lens")


def load_port(group: str, spec: str | None) -> Any:
    """The object an entry point `NAME[:ARG]` of `group` makes (None when `spec` is None)."""
    if spec is None:
        return None
    name, _, arg = spec.partition(":")
    found = {ep.name: ep for ep in entry_points(group=group)}
    if name not in found:
        raise HoneLensError(f"no {group} entry point {name!r}; installed: {sorted(found) or 'none'}")
    factory: Callable[..., Any] = found[name].load()
    try:
        return factory(arg) if arg else factory()
    except TypeError as e:
        raise HoneLensError(
            f"{group} {name!r} could not be made from {spec!r}; try {name}:MODEL ({e})"
        ) from e


def _workspace(
    path: Path, *, llm: str | None = None, embedder: str | None = None, replayer: str | None = None
) -> Workspace:
    return Workspace(
        path,
        llm=load_port("hone.text_clients", llm),
        embedder=load_port("hone.embedders", embedder),
        replayer=load_port("hone.replayers", replayer),
    )


def _run(action: Callable[[], None]) -> None:
    try:
        action()
    except HoneLensError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from None


@app.command()
def ingest(sources: list[str], workspace: WorkspaceOpt = DEFAULT_WORKSPACE) -> None:
    """Read new spans: hone:<path>, otlp:<glob>, phoenix:<file>, langfuse:<file>."""

    def action() -> None:
        for r in _workspace(workspace).ingest(*sources):
            typer.echo(f"{r.source}: read {r.read} spans, {r.added} new")

    _run(action)


@app.command()
def analyze(
    workflow: Annotated[str | None, typer.Argument()] = None,
    since: Annotated[str | None, typer.Option(help="ISO time or duration back from now, e.g. 30d.")] = None,
    stages: Annotated[str, typer.Option(help="Comma-separated stages.")] = ",".join(STAGES),
    budget: BudgetOpt = None,
    llm: LlmOpt = None,
    embedder: EmbedderOpt = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
    as_json: JsonOpt = False,
) -> None:
    """Run the analysis stages and print the findings."""

    def action() -> None:
        ws = _workspace(workspace, llm=llm, embedder=embedder)
        report = ws.analyze(
            workflow, since=since, stages=tuple(s for s in stages.split(",") if s), budget=budget
        )
        if as_json:
            typer.echo(json.dumps([finding_json(f) for f in report.findings], indent=2))
        else:
            typer.echo(terminal.render(report), nl=False)

    _run(action)


@app.command()
def review(
    workflow: str,
    sample: int = 40,
    budget: BudgetOpt = None,
    llm: LlmOpt = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Review a sample with the AI: notes, failure modes, labels (needs a terminal)."""

    def action() -> None:
        taxonomy = _workspace(workspace, llm=llm).review(workflow, sample=sample, budget=budget)
        typer.echo(f"{len(taxonomy.modes)} failure modes: {', '.join(m.name for m in taxonomy.modes)}")

    _run(action)


@app.command()
def label(
    workflow: str,
    budget: BudgetOpt = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Label even when agreement is low.")] = False,
    llm: LlmOpt = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Label every output with the reviewed taxonomy (shows a cost estimate first)."""

    def action() -> None:
        r = _workspace(workspace, llm=llm).label_all(workflow, budget=budget, yes=yes, force=force)
        if r.refused:
            raise HoneLensError(r.refused)
        typer.echo(f"labeled {r.labeled} of {r.outputs} (agreement {r.agreement}): {r.counts}")

    _run(action)


@app.command()
def explain(
    finding_id: str, llm: LlmOpt = None, workspace: WorkspaceOpt = DEFAULT_WORKSPACE, as_json: JsonOpt = False
) -> None:
    """Rank the recorded inputs that explain a finding."""

    def action() -> None:
        f = _workspace(workspace, llm=llm).explain(finding_id)
        if as_json:
            typer.echo(json.dumps(finding_json(f), indent=2))
        elif f.cause:
            typer.echo(f"{f.id}: {f.cause.kind} {f.cause.target} ({f.cause.status})\n{f.cause.hypothesis}")

    _run(action)


@app.command(name="test")
def replay_finding(
    finding_id: str,
    variants: int = 3,
    samples: int = 50,
    budget: BudgetOpt = None,
    replayer: Annotated[
        str | None, typer.Option(help="Replayer: NAME[:ARG] from entry points hone.replayers.")
    ] = None,
    llm: LlmOpt = None,
    embedder: EmbedderOpt = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
    as_json: JsonOpt = False,
) -> None:
    """Replay-test a finding's suspected cause."""

    def action() -> None:
        ws = _workspace(workspace, llm=llm, embedder=embedder, replayer=replayer)
        t = ws.test(finding_id, variants=variants, samples=samples, budget=budget)
        if as_json:
            typer.echo(json.dumps(finding_json(ws.finding(finding_id))["test"], indent=2))
        else:
            verdict = "confirmed" if t.confirmed else "not confirmed"
            change = f"{t.metric_name} {t.metric_before} -> {t.metric_after}"
            typer.echo(f"{finding_id}: {verdict}; {change} ({t.variant})")

    _run(action)


@app.command()
def report(
    workflow: Annotated[str | None, typer.Argument()] = None,
    html: Annotated[Path | None, typer.Option(help="Write a self-contained HTML report here.")] = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
    as_json: JsonOpt = False,
) -> None:
    """Print the findings (text, or JSON with --json), or write them as one HTML file."""

    def action() -> None:
        ws = _workspace(workspace)
        if html:
            ws.report(workflow, fmt="html", path=html)
            typer.echo(f"wrote {html}")
        else:
            typer.echo(ws.report(workflow, fmt="json" if as_json else "terminal"), nl=False)

    _run(action)


def _status(value: str | None) -> str | None:
    try:
        return check_status(value)
    except HoneLensError as e:
        raise typer.BadParameter(str(e)) from None


@app.command()
def findings(
    status: Annotated[
        str | None, typer.Option(help="Only findings with this status.", callback=_status)
    ] = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
    as_json: JsonOpt = False,
) -> None:
    """List stored findings."""

    def action() -> None:
        found = _workspace(workspace).findings(status)
        if as_json:
            typer.echo(json.dumps([finding_json(f) for f in found], indent=2))
            return
        table = Table("id", "status", "severity", "category", "finding")
        for f in found:
            table.add_row(f.id, f.status, f.severity, f.category, f.title)
        console.print(table)

    _run(action)


@app.command(name="set-status")
def set_status(
    finding_id: str,
    status: Annotated[str, typer.Argument(callback=_status)],
    note: str = "",
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Set a finding's status: new, confirmed_by_human, dismissed, fixed, regressed."""
    _run(
        lambda: typer.echo(
            f"{finding_id}: {_workspace(workspace).set_status(finding_id, status, note).status}"
        )
    )


def main() -> None:
    app()
