"""The data of stage 4: trace notes, the failure-mode taxonomy a person confirmed, and labeling results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TraceNote:
    """One open-coding note on a sampled output: written by the AI, accepted or edited by a person."""

    span_id: str
    output: str
    note: str
    edited: bool = False
    label: str | None = None  # the failure mode the person gave this output (None: fits none)


@dataclass
class Mode:
    """One failure mode of the taxonomy."""

    name: str
    description: str


@dataclass
class Taxonomy:
    """The failure modes of a workflow, confirmed by a person in `Workspace.review`, and the notes and human
    labels of the reviewed sample (used for the agreement check of `label_all`)."""

    workflow: str | None
    modes: list[Mode] = field(default_factory=list[Mode])
    notes: list[TraceNote] = field(default_factory=list[TraceNote])


@dataclass
class LabelReport:
    """What `label_all` did: the estimate shown first, the agreement on the reviewed sample, the label
    counts, and what it spent. `refused` says why nothing was labeled, if so."""

    workflow: str | None
    outputs: int
    estimate_tokens: int
    estimate_usd: float
    agreement: float | None = None
    counts: dict[str, int] = field(default_factory=dict[str, int])
    labeled: int = 0
    refused: str = ""
    cost_usd: float = 0.0
    llm_calls: int = 0
    budget_exhausted: bool = False
    notes: list[str] = field(default_factory=list[str])
