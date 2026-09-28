"""`Workspace`: one folder holding everything hone-lens knows about your runs; the entry point to the API."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hone_lens import report
from hone_lens._tracing import trace_for_finding
from hone_lens.budget import Budget, as_budget
from hone_lens.cause import explain
from hone_lens.coding import ConsoleIO, LabelReport, ReviewIO, Taxonomy
from hone_lens.coding.label import label_all
from hone_lens.coding.review import review
from hone_lens.describe import attach_descriptions, describe_clusters
from hone_lens.detectors import run_detectors
from hone_lens.errors import HoneLensError
from hone_lens.findings import Finding, Report, TestResult, carry_over, check_status, shown
from hone_lens.mapping import since_time
from hone_lens.outputs import analyze_outputs
from hone_lens.ports import Embedder, RecordSource, Replayer, StepRerunner, TextClient, TraceContext
from hone_lens.replay import Quality, replay_test
from hone_lens.sources import open_source, read_new
from hone_lens.store import AnalyticsDB

STAGES = ("stats", "outputs", "describe")


@dataclass(frozen=True)
class Ingested:
    """What one `ingest` did for one source: spans read from it, and how many of those were new."""

    source: str
    read: int
    added: int


class Workspace:
    """Analysis workspace at `path` (a folder; the database is `path/lens.db`).

    >>> ws = Workspace(".hone/lens")  # doctest: +SKIP
    >>> ws.ingest("hone:.hone", "otlp:traces/*.json")  # doctest: +SKIP

    The ports are optional and only needed by the stages that use them: `embedder` (output analysis),
    `llm` (descriptions, review, causes), `replayer` (replay tests), `step_rerunner` (reserved for v2).
    """

    def __init__(
        self,
        path: str | Path = ".hone/lens",
        *,
        embedder: Embedder | None = None,
        llm: TextClient | None = None,
        replayer: Replayer | None = None,
        step_rerunner: StepRerunner | None = None,
    ) -> None:
        self.path = Path(path)
        self.db = AnalyticsDB(self.path / "lens.db")
        self.embedder = embedder
        self.llm = llm
        self.replayer = replayer
        self.step_rerunner = step_rerunner

    def ingest(self, *sources: RecordSource | str) -> list[Ingested]:
        """Read new spans from each source (`"hone:<path>"`, `"otlp:<glob>"`, `"phoenix:<file>"`,
        `"langfuse:<file>"`, `"flow:<storage>/<workflow>"` or a `RecordSource`).

        Incremental: each source's position is remembered, so a second ingest reads only what was added
        since; spans already stored (same `span_id`) are skipped. A source with a `step_records(since=)`
        method (hone-flow's `FlowRuns`) also fills `flow_steps`, replacing the rows of every run it returns.
        """
        results: list[Ingested] = []
        for spec in sources:
            source = open_source(spec) if isinstance(spec, str) else spec
            previous = self.db.cursor(source.name)
            spans, cursor = read_new(source, previous)
            step_records = getattr(source, "step_records", None)
            records: list[Mapping[str, Any]] = (
                [] if step_records is None else step_records(since=previous.get("since"))
            )
            added = self.db.add(source.name, spans, cursor, records)
            results.append(Ingested(source.name, len(spans), added))
        return results

    def analyze(
        self,
        workflow: str | None = None,
        *,
        since: str | datetime | None = None,
        stages: tuple[str, ...] = STAGES,
        budget: Budget | float | str | None = None,
    ) -> Report:
        """Run the analysis stages over the ingested runs of `workflow` (all workflows when None) that start
        at or after `since` (ISO time, datetime, or a duration back from now such as `"30d"`, `"12h"`).

        `budget` limits the LLM stage (USD number, `"2usd"`, `"40 calls"` or a `Budget`); at the limit it
        stops with partial results, and the report says what was spent. Findings keep their ids and statuses
        across analyses (matched by content key); dismissed findings are left out of the report.
        """
        unknown = sorted(set(stages) - set(STAGES))
        if unknown:
            raise HoneLensError(f"unknown stage(s) {unknown}; choose from {list(STAGES)}")
        report = Report(workflow, [])
        spend = as_budget(budget)
        mark = spend.mark()
        found: list[Finding] = []
        with self.db.scoped(workflow, since_time(since)):
            if "stats" in stages:
                found += run_detectors(self.db, report.notes)
            if "outputs" in stages:
                found += self._outputs(report.notes)
        if "describe" in stages:
            self._describe(workflow, spend, report)
        attach_descriptions(self.db, found)
        report.findings = shown(self._remember(found))
        report.cost_usd, report.llm_calls = spend.since(mark)
        return report

    def _outputs(self, notes: list[str]) -> list[Finding]:
        if self.embedder is None:
            notes.append("outputs stage skipped: no embedder; pass Workspace(embedder=...)")
            return []
        try:
            return analyze_outputs(self.db, self.embedder)
        except HoneLensError as e:  # keep the stage 1 findings; say why stage 2 is missing
            notes.append(f"outputs stage failed: {e}")
            return []

    def _describe(self, workflow: str | None, spend: Budget, report: Report) -> None:
        if self.llm is None:
            report.notes.append("describe stage skipped: no analysis LLM; pass Workspace(llm=...)")
            return
        done = describe_clusters(self.db, self.llm, spend, workflow)
        report.notes += done.notes
        report.budget_exhausted = done.stopped

    def review(
        self,
        workflow: str,
        *,
        io: ReviewIO | None = None,
        sample: int = 40,
        budget: Budget | float | str | None = None,
    ) -> Taxonomy:
        """Stage 4 with a person: the AI notes a stratified sample of outputs (the person accepts or edits
        each note), proposes failure modes from the notes (the person keeps, renames, drops or merges them)
        and suggests a mode for each sampled output (the person confirms). `io` defaults to `ConsoleIO()`.
        The confirmed taxonomy is stored in the workspace and returned."""
        if sample < 1:
            raise HoneLensError(f"sample must be at least 1, got {sample}")
        if io is None and not sys.stdin.isatty():
            raise HoneLensError("review needs a person at a terminal; run it in one, or pass io=...")
        llm = self._llm("review")
        taxonomy = review(self.db, llm, io or ConsoleIO(), workflow, sample, as_budget(budget))
        self.db.save_taxonomy(taxonomy)
        return taxonomy

    def label_all(
        self,
        workflow: str,
        taxonomy: Taxonomy | None = None,
        *,
        budget: Budget | float | str | None,
        io: ReviewIO | None = None,
        yes: bool = False,
        force: bool = False,
    ) -> LabelReport:
        """Opt-in: the LLM labels every output of `workflow` with the taxonomy (default: the last one
        reviewed). Shows a cost estimate and asks for confirmation first (`yes=True` skips the question),
        then refuses when the LLM agrees with the reviewed labels on less than 70% (unless `force=True`).
        Stops at `budget` with partial counts."""
        taxonomy = taxonomy or self.db.taxonomy(workflow)
        if taxonomy is None:
            raise HoneLensError(f"no taxonomy for {workflow!r}; run review({workflow!r}) first")
        llm = self._llm("label_all")
        return label_all(self.db, llm, io or ConsoleIO(), taxonomy, as_budget(budget), yes=yes, force=force)

    def explain(self, finding_id: str, *, trace: TraceContext | None = None) -> Finding:
        """Stage 5: rank the recorded inputs that separate the finding's affected runs from the others and
        set its suspected `cause` (with a hypothesis from the LLM when one is configured) and `fix`. A cause
        that was already tested keeps its status and test result."""
        f = self.finding(finding_id)
        before = f.cause
        explain(self.db, f, self.llm, trace_for_finding(f.id, trace))
        if before and f.cause and (before.kind, before.target) == (f.cause.kind, f.cause.target):
            f.cause.status = before.status
        else:
            f.test = None
        self.db.save_findings([f])
        return f

    def test(
        self,
        finding_id: str,
        *,
        variants: int = 3,
        samples: int = 50,
        budget: Budget | float | str | None = None,
        quality: Quality | None = None,
        trace: TraceContext | None = None,
    ) -> TestResult:
        """Stage 6: replay `samples` calls that have the suspected cause with `variants` versions of it
        (through the `Replayer`, everything else fixed) and measure the finding's metric and, with a
        `quality(text) -> float | None` scorer, quality. Confirms, refutes or keeps the cause suspected;
        never changes the workflow or the finding's status. Runs `explain` first when there is no cause."""
        if self.replayer is None:
            raise HoneLensError(
                "test needs a Replayer: pass Workspace(replayer=...) or --replayer NAME (e.g. hone-models')"
            )
        if variants < 1 or samples < 1:
            raise HoneLensError(f"variants and samples must be at least 1, got {variants} and {samples}")
        f = self.finding(finding_id)
        context = trace_for_finding(f.id, trace)
        if f.cause is None:
            explain(self.db, f, self.llm, context)
        f.test = replay_test(
            self.db,
            f,
            self.replayer,
            embedder=self.embedder,
            llm=self.llm,
            quality=quality,
            variants=variants,
            samples=samples,
            budget=as_budget(budget),
            trace=context,
        )
        self.db.save_findings([f])
        return f.test

    def _llm(self, stage: str) -> TextClient:
        if self.llm is None:
            raise HoneLensError(f"{stage} needs an analysis LLM; pass Workspace(llm=...)")
        return self.llm

    def _remember(self, found: list[Finding]) -> list[Finding]:
        findings = carry_over(found, self.db.findings())
        self.db.save_findings(findings)
        return findings

    def findings(self, status: str | None = None) -> list[Finding]:
        """Every finding stored in the workspace (optionally only those with `status`), by id."""
        check_status(status)
        return [f for f in self.db.findings() if status is None or f.status == status]

    def finding(self, finding_id: str) -> Finding:
        """One stored finding; raises `HoneLensError` for an unknown id."""
        for f in self.db.findings():
            if f.id == finding_id:
                return f
        raise HoneLensError(f"no finding {finding_id!r} in {self.path}; run analyze() first or check the id")

    def set_status(self, finding_id: str, status: str, note: str = "") -> Finding:
        """Set a finding's status (`new`, `confirmed_by_human`, `dismissed`, `fixed`, `regressed`)."""
        check_status(status)
        f = self.finding(finding_id)
        f.status = status
        if note:
            f.details["note"] = note
        if status == "fixed":  # re-analysis flags it `regressed` when it shows up in runs after this
            f.details["fixed_at"] = self.db.query("SELECT max(start_time) AS t FROM spans")[0]["t"]
        self.db.save_findings([f])
        return f

    def report(
        self, workflow: str | None = None, *, fmt: str = "terminal", path: str | Path | None = None
    ) -> str:
        """The stored findings of `workflow` (all when None; dismissed ones left out) as a `terminal`, `json`
        or `html` report (one self-contained file whose findings link to views of their evidence traces and
        clusters). Returned, and written to `path` when given."""
        text = report.render(self.db, workflow, shown(self.db.findings(), workflow), fmt)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text
