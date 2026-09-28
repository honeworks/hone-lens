"""Stage 4 review: AI open-coding notes on a stratified sample, accepted or edited by a person; failure
modes proposed by the AI from the notes, confirmed / renamed / merged by the person; human labels of the
sample (the reference for `label_all`'s agreement check)."""

from __future__ import annotations

from typing import Any

from hone_lens.budget import Budget
from hone_lens.coding.io import ReviewIO
from hone_lens.coding.sample import stratified_sample
from hone_lens.coding.taxonomy import Mode, Taxonomy, TraceNote
from hone_lens.errors import BudgetExceeded, HoneLensError
from hone_lens.llm import ask
from hone_lens.mapping import as_dict, as_list
from hone_lens.ports import TextClient
from hone_lens.store import AnalyticsDB

NOTE = (
    "You review one output of an AI workflow. Write a one-line open-coding note (`note`): what, if anything, "
    "is wrong or notable about it. Say 'no obvious problem' when nothing is."
)
TAXONOMY = (
    "The user message holds open-coding notes on outputs of one AI workflow. Group them into 5-8 failure "
    "modes (`modes`: name in snake_case, one-sentence description). Leave out notes that report no problem."
)
LABEL = (
    "Assign the output to the one failure mode (`mode`, by name) that fits it best, or null when none fits."
)
_MODE = {"mode": {"type": ["string", "null"]}}
MODES_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
        "required": ["name", "description"],
    },
}


def review(
    db: AnalyticsDB, llm: TextClient, io: ReviewIO, workflow: str, sample: int, budget: Budget
) -> Taxonomy:
    rows = stratified_sample(db, workflow, sample)
    if not rows:
        raise HoneLensError(f"no outputs of workflow {workflow!r} to review; ingest runs first")
    notes = [_note(llm, io, row, budget, f"{i}/{len(rows)}") for i, row in enumerate(rows, 1)]
    modes = _confirm_modes(io, _proposed_modes(llm, notes, budget))
    for note in notes:
        note.label = _human_label(llm, io, note, modes, budget)
    return Taxonomy(workflow, modes, notes)


def _ai(
    llm: TextClient,
    task: str,
    instruction: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    budget: Budget,
) -> dict[str, Any]:
    """The AI's answer, or `{}` when it failed or the budget is spent (the person decides alone then)."""
    try:
        answer, _ = ask(llm, task, instruction, payload, schema, budget)
    except BudgetExceeded:
        return {}
    return answer or {}


def _note(llm: TextClient, io: ReviewIO, row: dict[str, Any], budget: Budget, position: str) -> TraceNote:
    ai_note = str(
        _ai(llm, "trace_note", NOTE, {"output": row["text"]}, {"note": {"type": "string"}}, budget).get(
            "note"
        )
        or ""
    )
    io.show(f"--- output {position} ({row['span_id']})\n{row['text']}\nAI note: {ai_note or '(none)'}")
    reply = io.ask("Enter to accept the note, or type a better one:")
    return TraceNote(row["span_id"], row["text"], reply or ai_note, edited=bool(reply))


def _proposed_modes(llm: TextClient, notes: list[TraceNote], budget: Budget) -> list[Mode]:
    answer = _ai(
        llm, "taxonomy", TAXONOMY, {"notes": [n.note for n in notes]}, {"modes": MODES_SCHEMA}, budget
    )
    modes = [as_dict(m) for m in as_list(answer.get("modes"))]
    return [Mode(str(m["name"]), str(m.get("description", ""))) for m in modes if m.get("name")]


def _confirm_modes(io: ReviewIO, proposed: list[Mode]) -> list[Mode]:
    """Keep, rename (`new_name`), drop (`-`) or merge (`=other_name`: its description joins the other mode's)
    each proposed mode, then add modes (`name: description`) until an empty answer."""
    modes: dict[str, Mode] = {}
    merges: list[tuple[Mode, str]] = []
    for mode in proposed:
        io.show(f"Proposed failure mode {mode.name}: {mode.description}")
        reply = io.ask("Enter to keep, a new name to rename, '-' to drop, '=name' to merge into another:")
        if reply.startswith("="):
            merges.append((mode, reply[1:].strip()))
        elif reply != "-":
            modes.setdefault(reply or mode.name, Mode(reply or mode.name, mode.description))
    for mode, target in merges:
        if target in modes:
            modes[target].description += f"; also: {mode.description}"
        else:
            io.show(f"no mode {target!r} to merge {mode.name} into; kept {mode.name}")
            modes.setdefault(mode.name, mode)
    while reply := io.ask("Add a failure mode as 'name: description' (Enter when done):"):
        name, _, description = (part.strip() for part in reply.partition(":"))
        if name and name not in modes:
            modes[name] = Mode(name, description)
    return list(modes.values())


def suggest_mode(llm: TextClient, modes: list[Mode], payload: dict[str, Any], budget: Budget) -> str | None:
    """The LLM's failure mode for one output (`payload`: output, maybe its note), or None. Raises
    `BudgetExceeded` when the budget is spent."""
    answer, _ = ask(llm, "label", LABEL, {**payload, "modes": [vars(m) for m in modes]}, _MODE, budget)
    mode = (answer or {}).get("mode")
    return mode if mode in {m.name for m in modes} else None


def _human_label(
    llm: TextClient, io: ReviewIO, note: TraceNote, modes: list[Mode], budget: Budget
) -> str | None:
    names = [m.name for m in modes]
    try:
        suggestion = suggest_mode(llm, modes, {"output": note.output, "note": note.note}, budget)
    except BudgetExceeded:
        suggestion = None
    reply = io.ask(
        f"Failure mode of {note.span_id}? [{suggestion or '-'}] (one of {', '.join(names)}; '-': none):"
    )
    if not reply:
        return suggestion
    if reply == "-":
        return None
    if reply not in names:
        io.show(f"unknown mode {reply!r}; kept {suggestion or 'none'}")
        return suggestion
    return reply
