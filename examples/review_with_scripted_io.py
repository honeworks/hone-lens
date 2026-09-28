"""Stage 4: build a failure taxonomy with the AI and a person, then label every output.

What: `review`, a conversation in which the AI writes a note on each sampled output and proposes failure
modes, and a person accepts, edits, renames, drops or merges them; then `label_all`, which labels every
output with the confirmed taxonomy after checking that the LLM agrees with the person's labels.

How: `ws.review(workflow, io=, sample=)` returns the confirmed `Taxonomy` (and stores it);
`ws.label_all(workflow, budget=, io=)` shows a cost estimate, asks for confirmation, checks agreement on
the reviewed sample (>= 70%) and labels the rest. `io` is anything with `show(text)` and
`ask(question, default)`: `tl.ConsoleIO()` in a terminal, `ScriptedIO(answers)` here, which answers from
a list and records what it was shown. The answers, in order: one per sampled output's note (Enter keeps
the AI's note, text replaces it); one per proposed failure mode (Enter keeps it, a new name renames it,
`-` drops it, `=other` merges it into mode `other`); then new modes as `name: description` until an empty
answer; then one label per sampled output (Enter keeps the AI's suggestion, a mode name, or `-` for none).

Why: failure modes nobody named cannot be counted. A person-confirmed taxonomy, applied to every output
with a measured agreement, turns "some outputs feel off" into rates you can track.

Run: python examples/review_with_scripted_io.py
"""

import tempfile
from pathlib import Path

import hone_lens as tl
from hone_lens.testing import FakeEmbedder, FakeTextClient, ScriptedIO, synthetic_runs

SAMPLE = 10

with tempfile.TemporaryDirectory() as tmp:
    runs = synthetic_runs(Path(tmp), n_runs=200, plant=["homogeneity_from_example"])
    ws = tl.Workspace(Path(tmp) / "lens", embedder=FakeEmbedder.semantic(), llm=FakeTextClient.analyst())
    ws.ingest(f"hone:{runs.root}")
    ws.analyze("song_ideas")  # clusters feed the stratified sample

    answers = ["copies the storm example almost word for word"]  # edit the first AI note
    answers += [""] * (SAMPLE - 1)  # accept the other notes
    # the AI proposes one failure mode per kind of note: here two, both about copying the example
    answers += ["copies_example"]  # rename the first proposed mode
    answers += ["-"]  # drop the second, a near-duplicate ("=copies_example" would merge it instead)
    answers += ["vague: the idea lacks concrete details", ""]  # add a mode, then done
    # the remaining questions (one label per sampled output) get their defaults: the AI's suggestion
    io = ScriptedIO(answers)
    taxonomy = ws.review("song_ideas", io=io, sample=SAMPLE)
    print("shown to the person first:\n" + io.shown[0])
    print("failure modes:", [(m.name, m.description) for m in taxonomy.modes])
    assert [m.name for m in taxonomy.modes] == ["copies_example", "vague"]  # renamed, then added
    print("labels of the sample:", [n.label for n in taxonomy.notes])
    assert taxonomy.notes[0].note == "copies the storm example almost word for word"

    # Opt-in: label all 200 outputs. It shows an estimate and asks first ("y" here), then checks that the
    # LLM agrees with the person's labels on the sample (>= 70%) before labeling the rest.
    labels = ws.label_all("song_ideas", budget="300 calls", io=ScriptedIO(["y"]))
    assert not labels.refused and labels.labeled == labels.outputs
    print(f"agreement {labels.agreement:.0%}, labeled {labels.labeled}: {labels.counts}")
