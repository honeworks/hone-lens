import hashlib
import itertools
import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

import pytest

from hone_lens.mapping import output_text
from hone_lens.testing import PLANTS, synthetic_runs

STORES = ("models", "select")


def _spans(db: Path) -> list[dict]:
    rows = sqlite3.connect(db).execute(
        "SELECT span_id, trace_id, parent_span_id, name, start_time, end_time, attributes, status_code, "
        "status_message, resource FROM spans"
    )
    keys = (
        "span_id",
        "trace_id",
        "parent",
        "name",
        "start",
        "end",
        "attributes",
        "status",
        "message",
        "resource",
    )
    spans = [dict(zip(keys, r, strict=True)) for r in rows]
    for s in spans:
        s["attributes"], s["resource"] = json.loads(s["attributes"]), json.loads(s["resource"])
    return spans


def _flow(runs) -> list[dict]:
    """The spans of every run folder, in the same shape as `_spans`."""
    spans = []
    for path in sorted(runs.flows.glob("song_ideas/runs/*/spans.jsonl")):
        for line in path.read_text().splitlines():
            s = json.loads(line)
            spans.append(
                {
                    "span_id": s["span_id"],
                    "trace_id": s["trace_id"],
                    "parent": s["parent_span_id"],
                    "name": s["name"],
                    "start": s["start_time"],
                    "end": s["end_time"],
                    "attributes": s["attributes"],
                    "status": s["status"]["code"],
                    "message": s["status"]["message"],
                    "resource": s["resource"],
                    "events": s["events"],
                }
            )
    return spans


def _all(runs) -> list[dict]:
    return [s for p in STORES for s in _spans(runs.root / p / "spans.db")] + _flow(runs)


def _calls(runs, scorer=None) -> list[dict]:
    spans = _spans(runs.root / "models" / "spans.db")
    return [s for s in spans if s["attributes"].get("hone.scorer") == scorer]


def _ms(span: dict) -> float:
    parse = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))  # noqa: E731
    return (parse(span["end"]) - parse(span["start"])).total_seconds() * 1000


def _otlp_spans(path: Path) -> list[dict]:
    docs = [json.loads(line) for line in path.read_text().splitlines()]
    return [s for d in docs for rs in d["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]]


def test_writes_records_spec_stores(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=10)
    for package in STORES:
        db = sqlite3.connect(runs.root / package / "spans.db")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"meta", "spans", "blobs", "changes"} <= tables
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        assert meta["schema"] == "hone-spans" and meta["schema_version"] == "1"
        assert meta["package"] == f"hone-{package}"
        spans = _spans(runs.root / package / "spans.db")
        assert all(s["resource"]["hone.package"] == meta["package"] for s in spans)
        assert db.execute("SELECT count(*) FROM changes").fetchone()[0] == len(spans)
    counts = {p: len(_spans(runs.root / p / "spans.db")) for p in STORES}
    assert counts == {"models": 20, "select": 30}


def test_writes_hone_flow_run_folders(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=10)
    assert runs.flows == runs.root / "flows"
    folders = sorted(p for p in (runs.flows / "song_ideas" / "runs").iterdir())
    assert [f.name for f in folders] == [f"run-0-{i:05d}" for i in range(10)]
    for folder in folders:
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["format_version"] == "1" and manifest["run_id"] == folder.name
        assert (manifest["workflow"], manifest["status"]) == ("song_ideas", "completed")
        lines = (folder / "spans.jsonl").read_text().splitlines()
        assert all(json.loads(line)["attributes"]["hone.run_id"] == folder.name for line in lines)
    flow = _flow(runs)
    assert all(s["resource"]["hone.package"] == "hone-flow" for s in flow)
    steps = [s["attributes"] for s in flow if s["name"] == "hone.flow.step"]
    assert len(steps) == 10 and {s["hone.flow.status"] for s in steps} == {"done"}
    assert {s["hone.flow.attempt"] for s in steps} == {1}
    assert not any(k.startswith("hone.flow.cache") for s in flow for k in s["attributes"])
    # every fifth run was interrupted once and resumed: two hone.flow.run spans in one trace
    calls = [s for s in flow if s["name"] == "hone.flow.run"]
    assert len(calls) == 12
    interrupted = [s for s in calls if s["attributes"]["hone.flow.status"] == "interrupted"]
    assert [s["attributes"]["hone.run_id"] for s in interrupted] == ["run-0-00004", "run-0-00009"]
    assert all(s["status"] == "error" and s["message"] == "run interrupted" for s in interrupted)


def test_spans_link_into_one_trace_per_run(tmp_path: Path) -> None:
    spans = _all(synthetic_runs(tmp_path, n_runs=20, plant=["vision_400"]))
    by_id = {s["span_id"]: s for s in spans}
    for s in spans:
        assert s["attributes"]["hone.schema_version"] == "1"
        if s["parent"] is None:
            assert s["name"] == "hone.flow.run"
            continue
        parent = by_id[s["parent"]]
        assert parent["trace_id"] == s["trace_id"]
        expected = {"hone.flow.step": "hone.flow.run", "hone.select.run": "hone.flow.step"}
        if s["name"] in expected:
            assert parent["name"] == expected[s["name"]]
        if s["name"] == "hone.models.chat":
            scorer = s["attributes"].get("hone.scorer")
            want = {None: "hone.flow.step", "quality": "hone.select.score", "cover_art": "hone.select.run"}[
                scorer
            ]
            assert parent["name"] == want
    traces_per_run: dict[str, set[str]] = {}
    for s in spans:
        traces_per_run.setdefault(s["attributes"]["hone.run_id"], set()).add(s["trace_id"])
    assert all(len(t) == 1 for t in traces_per_run.values())


def test_same_seed_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    a = synthetic_runs(tmp_path / "a", n_runs=30, plant=["truncation"])
    b = synthetic_runs(tmp_path / "b", n_runs=30, plant=["truncation"])
    for package in STORES:
        rows = lambda r: sorted(sqlite3.connect(r.root / package / "spans.db").execute("SELECT * FROM spans"))  # noqa: E731, B023
        assert rows(a) == rows(b)
    assert a.otlp_path.read_text() == b.otlp_path.read_text()
    assert _flow(a) == _flow(b)
    synthetic_runs(tmp_path / "a", n_runs=30, plant=["truncation"])  # same seed again: no duplicates
    assert len(_flow(a)) == len(_flow(b))
    db = sqlite3.connect(a.root / "models" / "spans.db")
    assert db.execute("SELECT count(*) FROM spans").fetchone()[0] == 60
    assert db.execute("SELECT count(*) FROM changes").fetchone()[0] == 60
    ids = [s["spanId"] for s in _otlp_spans(a.otlp_path)]
    assert len(ids) == len(set(ids)) == 60


def test_new_seed_appends_later_disjoint_runs(tmp_path: Path) -> None:
    first = synthetic_runs(tmp_path, n_runs=20)
    before = _all(first)
    second = synthetic_runs(tmp_path, n_runs=5, seed=1)
    after = _all(second)
    new = [s for s in after if s["span_id"] not in {b["span_id"] for b in before}]
    assert len(after) == len(before) + len(new) and len(new) == 36  # 35 + the resume call of run 4
    assert {s["trace_id"] for s in new}.isdisjoint({s["trace_id"] for s in before})
    assert min(s["start"] for s in new) > max(s["start"] for s in before)
    assert second.otlp_path != first.otlp_path and first.otlp_path.exists()


@pytest.mark.parametrize("bad", [{"plant": ["nope"]}, {"n_runs": -1}])
def test_bad_arguments_are_rejected(tmp_path: Path, bad: dict) -> None:
    with pytest.raises(ValueError, match="unknown plant|n_runs") as info:
        synthetic_runs(tmp_path, **{"n_runs": 1, **bad})
    if "plant" in bad:
        assert "homogeneity_from_example" in str(info.value)


def test_zero_runs_and_duplicate_plants(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=0, plant=["truncation", "truncation"])
    assert runs.plants == ("truncation",)
    assert _all(runs) == []
    assert _otlp_spans(runs.otlp_path) == []


def test_truth_matches_the_recorded_data(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=20, plant=list(PLANTS))
    assert set(runs.truth) == set(PLANTS)
    calls = [c["attributes"] for c in _spans(runs.root / "models" / "spans.db")]
    models = {c["hone.models.model_id"] for c in calls}
    scorers = {c.get("hone.scorer") for c in calls}
    for truth in runs.truth.values():
        assert truth["scope"]["workflow"] == "song_ideas" and 0 < truth["rate"] <= 1
        assert truth["scope"].get("model", "gemma4-12b") in models
        assert truth["scope"].get("scorer", "quality") in scorers
    runs.truth["truncation"]["scope"]["model"] = "changed"
    assert (
        synthetic_runs(tmp_path / "x", 1, ["truncation"]).truth["truncation"]["scope"]["model"]
        == "gemma4-12b"
    )
    assert synthetic_runs(tmp_path / "y", 1).truth == {}


def test_homogeneity_plant_copies_the_example_only_after_v3(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=400, plant=["homogeneity_from_example", "truncation"])
    by_version: dict[str, list[bool]] = {}
    for call in _calls(runs):
        attrs = call["attributes"]
        copied = "Captain" in output_text(call)
        by_version.setdefault(attrs["hone.models.prompt.template_version"], []).append(copied)
    assert not any(by_version["2"])
    assert 0.7 < sum(by_version["3"]) / len(by_version["3"]) < 0.9


def test_sections_point_at_their_text(tmp_path: Path) -> None:
    for call in _calls(synthetic_runs(tmp_path, n_runs=40, plant=["latency_regression_after_v4"])):
        attrs = call["attributes"]
        prompt = json.loads(attrs["gen_ai.input.messages"])[0]["content"]
        sections = json.loads(attrs["hone.models.prompt.sections"])
        version = attrs["hone.models.prompt.template_version"]
        assert ("format_example" in [s["id"] for s in sections]) == (version != "2")
        assert next(s for s in sections if s["id"] == "task")["version"] == ("4" if version == "4" else "2")
        for prev, section in itertools.pairwise(sections):
            assert prev["end"] < section["start"]
        for section in sections:
            text = prompt[section["start"] : section["end"]]
            assert hashlib.sha256(text.encode()).hexdigest() == section["sha256"]


def test_clean_set_has_no_planted_signal(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=200)
    calls = _calls(runs)
    assert not any("Captain" in output_text(c) for c in calls)
    assert all(c["attributes"]["gen_ai.response.finish_reasons"] == ["stop"] for c in calls)
    assert all("gen_ai.request.seed" not in c["attributes"] for c in calls)
    assert len({c["attributes"]["gen_ai.input.messages"] for c in calls}) == len(calls)
    assert {c["attributes"]["hone.models.prompt.template_version"] for c in calls} == {"2", "3"}
    everything = _all(runs)
    assert all(
        s["status"] == "ok" or s["attributes"].get("hone.flow.status") == "interrupted" for s in everything
    )
    models = [s for s in everything if s["name"] == "hone.models.chat"]
    assert not any(s["attributes"]["hone.models.gpu.unloaded"] for s in models)
    assert all(s["attributes"]["hone.models.gpu.lease_wait_ms"] < 40 for s in models)
    assert {j["attributes"]["hone.models.model_id"] for j in _calls(runs, scorer="quality")} == {"qwen3-8b"}
    assert _calls(runs, scorer="cover_art") == []
    assert not any(s["events"] for s in _flow(runs))  # no source-change warnings
    scores = [
        s["attributes"]["hone.select.score.value"] for s in everything if s["name"] == "hone.select.score"
    ]
    assert min(scores) >= 0.45
    # prompt v3 adds the example section but tokens grow modestly (no token-growth finding expected)
    tokens = {
        v: [
            c["attributes"]["gen_ai.usage.input_tokens"]
            for c in calls
            if c["attributes"]["hone.models.prompt.template_version"] == v
        ]
        for v in ("2", "3")
    }
    assert statistics.mean(tokens["3"]) < 1.5 * statistics.mean(tokens["2"])


def test_stats_plants(tmp_path: Path) -> None:
    plants = [
        "truncation",
        "judge_equals_generator",
        "score_zero_spike",
        "vision_400",
        "gpu_thrash",
        "nondeterministic_seed",
    ]
    runs = synthetic_runs(tmp_path, n_runs=400, plant=plants)
    calls = _calls(runs)
    length = [
        c["attributes"] for c in calls if c["attributes"]["gen_ai.response.finish_reasons"] == ["length"]
    ]
    assert 0.02 < len(length) / len(calls) < 0.09
    for a in length:
        assert a["hone.models.structured.path"] == "repaired"
        assert a["hone.models.context.estimated_prompt_tokens"] >= 0.9 * a["hone.models.context.limit"]
        assert a["gen_ai.usage.input_tokens"] >= 0.9 * a["hone.models.context.limit"]
    judges = [j["attributes"] for j in _calls(runs, "quality")]
    assert {j["hone.models.model_id"] for j in judges} == {"gemma4-4b"}
    vision = _calls(runs, "cover_art")
    assert len(vision) == 400 and all(v["status"] == "error" and "400" in v["message"] for v in vision)
    assert all(c["attributes"]["hone.models.gpu.unloaded"] for c in calls)
    assert any(c["message"] == "CUDA out of memory" for c in calls)
    scores = [s for s in _all(runs) if s["name"] == "hone.select.score"]
    zeros = sum(s["attributes"]["hone.select.score.value"] == 0.0 for s in scores)
    assert 0.05 < zeros / len(scores) < 0.16
    assert sum(j["hone.models.structured.path"] == "failed" for j in judges) == zeros
    inputs = [c["attributes"]["gen_ai.input.messages"] for c in calls]
    assert len(set(inputs)) < len(inputs)


def test_source_change_and_latency_plants(tmp_path: Path) -> None:
    runs = synthetic_runs(
        tmp_path, n_runs=400, plant=["source_changed_version_unchanged", "latency_regression_after_v4"]
    )
    flow = _flow(runs)
    steps = [s["attributes"] for s in flow if s["name"] == "hone.flow.step"]
    assert len({s["hone.flow.source_hash"] for s in steps}) == 2
    assert {s["hone.flow.step_version"] for s in steps} == {"1"}
    calls = [s for s in flow if s["name"] == "hone.flow.run"]
    resumed = {
        s["attributes"]["hone.run_id"] for s in calls if s["attributes"]["hone.flow.status"] == "interrupted"
    }
    assert len(resumed) == 80
    warned = [s for s in calls if s["events"]]
    assert all(
        s["attributes"]["hone.run_id"] in resumed and s["attributes"]["hone.flow.status"] == "completed"
        for s in warned
    )
    assert 0.07 < len(warned) / len(resumed) < 0.25
    (event,) = warned[0]["events"]
    assert event["name"] == "warning" and event["attributes"]["kind"] == "source_changed_version_unchanged"
    assert event["attributes"]["step"] == "ideas" and event["attributes"]["old"] != event["attributes"]["new"]
    calls = _calls(runs)
    by_version: dict[str, list[dict]] = {}
    for c in calls:
        by_version.setdefault(c["attributes"]["hone.models.prompt.template_version"], []).append(c)
    assert set(by_version) == {"2", "3", "4"}
    median = {v: statistics.median(_ms(c) for c in cs) for v, cs in by_version.items()}
    assert median["4"] > 1.5 * median["3"] and median["3"] < 1.3 * median["2"]
    assert min(c["start"] for c in by_version["4"]) > max(c["start"] for c in by_version["3"])


def test_otlp_variant_matches_the_store_without_hone_fields(tmp_path: Path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=100, plant=["homogeneity_from_example", "truncation"])
    spans = _otlp_spans(runs.otlp_path)
    assert len(spans) == 200
    keys = {a["key"] for s in spans for a in s["attributes"]}
    assert keys and not any(k.startswith("hone.") for k in keys)
    assert "gen_ai.usage.prompt_tokens" in keys
    chats = [s for s in spans if s["name"].startswith("chat ")]
    steps = {s["spanId"] for s in spans if s["name"] == "generate_idea"}
    assert all(c["parentSpanId"] in steps for c in chats)
    assert all(int(s["startTimeUnixNano"]) <= int(s["endTimeUnixNano"]) for s in spans)

    def attr(span: dict, key: str) -> dict:
        return next(a["value"] for a in span["attributes"] if a["key"] == key)

    texts = sorted(
        output_text(
            {"attributes": {"gen_ai.output.messages": attr(c, "gen_ai.output.messages")["stringValue"]}}
        )
        for c in chats
    )
    assert texts == sorted(output_text(c) for c in _calls(runs))
    finish = [
        attr(c, "gen_ai.response.finish_reasons")["arrayValue"]["values"][0]["stringValue"] for c in chats
    ]
    assert "length" in finish
    assert attr(chats[0], "gen_ai.usage.prompt_tokens")["intValue"].isdigit()
