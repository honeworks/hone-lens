import json
import os
import sqlite3
from pathlib import Path

import pytest

from hone_lens.errors import SourceError
from hone_lens.sources import HoneSpanStore, OtlpJsonFiles, open_source, read_new
from hone_lens.testing import FakeRecordSource, synthetic_runs
from hone_lens.testing._store_writer import write_store
from hone_lens.testing.contracts import example_call_span

MIDWAY = "2026-08-01T00:10:00.000Z"  # 40 runs, 30 s apart, from midnight


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    return synthetic_runs(tmp_path_factory.mktemp("syn"), n_runs=40, plant=["vision_400"])


def _span(i: int, **extra) -> dict:
    return {
        **example_call_span(),
        "span_id": f"{i:016x}",
        "start_time": f"2026-09-27T14:03:{i:02d}.000Z",
        **extra,
    }


# --- hone stores -------------------------------------------------------------------------------------------


def test_hone_store_reads_every_store_below_a_folder(runs) -> None:
    source = HoneSpanStore(runs.root)
    files = source.files()
    assert [p.parent.name for p in files if p.suffix == ".db"] == ["models", "select"]
    run_folders = [p.parent for p in files if p.suffix == ".jsonl"]
    assert len(run_folders) == 40 and {p.parent for p in run_folders} == {runs.flows / "song_ideas" / "runs"}
    spans = source.spans()
    names = {s["name"] for s in spans}
    assert {"hone.flow.run", "hone.flow.step", "hone.models.chat", "hone.select.score"} <= names
    # run, step, generator, judge, vision, select run / score / decision; every fifth run resumed once
    assert len(spans) == 40 * 8 + 8
    assert source.name == f"hone:{runs.root.resolve()}"


def test_hone_store_since_filters_by_start_time(runs) -> None:
    source = HoneSpanStore(runs.root / "models" / "spans.db")
    later = source.spans(since=MIDWAY)
    assert later and all(s["start_time"] >= MIDWAY for s in later)
    assert len(later) < len(source.spans())


def test_hone_store_span_shape(runs) -> None:
    span = next(
        s for s in HoneSpanStore(runs.root / "models" / "spans.db").spans() if s["status"]["code"] == "error"
    )
    assert span["status"]["message"].startswith("HTTP 400")
    assert isinstance(span["attributes"], dict) and isinstance(span["resource"], dict)
    assert span["events"] == [] and span["links"] == []


def test_hone_store_read_new_follows_the_change_log(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    write_store(db, [_span(1), _span(2)], package="hone-models")
    source = HoneSpanStore(db)
    first, cursor = source.read_new({})
    assert [s["span_id"] for s in first] == [f"{1:016x}", f"{2:016x}"]
    assert cursor == {str(db): 2}
    assert source.read_new(cursor) == ([], cursor)
    write_store(db, [_span(3)], package="hone-models")
    new, cursor = source.read_new(cursor)
    assert [s["span_id"] for s in new] == [f"{3:016x}"]
    assert cursor == {str(db): 3}


def test_hone_store_resolves_blobs(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    write_store(
        db,
        [_span(1, attributes={"gen_ai.output.messages": {"$blob": "abc"}, "x": {"$blob": "zzz"}})],
        package="p",
    )
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO blobs VALUES ('abc', 'application/json', 4, ?)", (json.dumps("long text").encode(),)
        )
    c.close()
    (span,) = HoneSpanStore(db).spans()
    assert span["attributes"]["gen_ai.output.messages"] == "long text"
    assert span["attributes"]["x"] == {"$blob": "zzz"}  # a missing blob stays a visible reference


def test_hone_store_reads_jsonl_files(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    path.write_text(json.dumps(_span(1)) + "\n\n" + json.dumps(_span(2)) + "\n")
    source = HoneSpanStore(tmp_path)
    assert [s["span_id"] for s in source.spans(since="2026-09-27T14:03:02.000Z")] == [f"{2:016x}"]
    spans, cursor = source.read_new({})
    assert len(spans) == 2
    assert source.read_new(cursor)[0] == []
    os.utime(path, ns=(1, 1))  # touched but not grown: nothing new
    assert source.read_new(cursor)[0] == []
    with path.open("a") as f:
        f.write(json.dumps(_span(3)) + "\n")
    spans, cursor = source.read_new(cursor)
    assert [s["span_id"] for s in spans] == [f"{3:016x}"]  # only the appended line
    path.write_text(json.dumps(_span(4)) + "\n")  # rewritten shorter: read again from the start
    assert [s["span_id"] for s in source.read_new(cursor)[0]] == [f"{4:016x}"]


def test_hone_store_leaves_an_unfinished_last_line_for_the_next_read(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    whole = json.dumps(_span(2)) + "\n"
    path.write_text(json.dumps(_span(1)) + "\n" + whole[:20])  # a writer is in the middle of line 2
    source = HoneSpanStore(tmp_path)
    spans, cursor = source.read_new({})
    assert [s["span_id"] for s in spans] == [f"{1:016x}"]
    path.write_text(json.dumps(_span(1)) + "\n" + whole)
    assert [s["span_id"] for s in source.read_new(cursor)[0]] == [f"{2:016x}"]
    path.write_text(path.read_text() + json.dumps(_span(3)))  # a complete last line without newline
    assert [s["span_id"] for s in HoneSpanStore(tmp_path).spans()][-1] == f"{3:016x}"


def test_hone_store_offsets_are_bytes_with_non_ascii(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    first = _span(1)
    first["attributes"] = {**first["attributes"], "title": "é🎵 café"}
    path.write_text(json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8")
    source = HoneSpanStore(tmp_path)
    spans, cursor = source.read_new({})
    assert spans[0]["attributes"]["title"] == "é🎵 café"
    assert cursor == {str(path.resolve()): path.stat().st_size}  # bytes, not characters
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_span(2), ensure_ascii=False) + "\n")
    spans, cursor = source.read_new(cursor)
    assert [s["span_id"] for s in spans] == [f"{2:016x}"] and cursor[
        str(path.resolve())
    ] == path.stat().st_size


def test_hone_store_reads_an_old_style_cursor_whole(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    path.write_text(json.dumps(_span(1)) + "\n")
    assert len(HoneSpanStore(tmp_path).read_new({str(path.resolve()): [1, 2]})[0]) == 1


def test_hone_store_rejects_bad_jsonl(tmp_path: Path) -> None:
    (tmp_path / "spans.jsonl").write_text(json.dumps(_span(1)) + "\n{not json\n")
    with pytest.raises(SourceError, match=r"spans.jsonl: the line at byte \d+ is not a JSON span"):
        HoneSpanStore(tmp_path).spans()


def test_hone_store_rejects_jsonl_spans_of_unknown_schema_versions(tmp_path: Path) -> None:
    span = _span(1)
    span["attributes"] = {**span["attributes"], "hone.schema_version": "9"}
    (tmp_path / "spans.jsonl").write_text(json.dumps(span) + "\n")
    with pytest.raises(SourceError, match="schema version '9'"):
        HoneSpanStore(tmp_path).spans()


def test_hone_store_missing_path(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="no honeworks span store"):
        HoneSpanStore(tmp_path / "nowhere").spans()


def test_hone_store_rejects_non_stores_and_unknown_schema_versions(tmp_path: Path) -> None:
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
    with pytest.raises(SourceError, match="not a honeworks span store"):
        HoneSpanStore(other).spans()
    db = tmp_path / "spans.db"
    write_store(db, [_span(1)], package="p")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
    c.close()
    with pytest.raises(SourceError, match="schema version '9'"):
        HoneSpanStore(db).spans()


# --- OTLP files ----------------------------------------------------------------------------------------------


def test_otlp_reads_synthetic_export(runs) -> None:
    source = OtlpJsonFiles(runs.otlp_path)
    spans = source.spans()
    assert len(spans) == 80
    chat = next(s for s in spans if s["kind"] == "client")
    assert chat["name"].startswith("chat ")
    assert chat["resource"] == {"service.name": "song_ideas"}
    assert chat["attributes"]["gen_ai.usage.prompt_tokens"] > 0  # older names stay; ingest maps them
    assert chat["attributes"]["gen_ai.response.finish_reasons"] in (["stop"], ["length"])
    assert chat["start_time"].endswith("Z") and chat["parent_span_id"]
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["kind"] == "internal" and root["status"] == {"code": "ok", "message": ""}


def _otlp(spans: list[dict]) -> dict:
    return {"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": spans}]}]}


def test_otlp_value_types_kinds_events_links(tmp_path: Path) -> None:
    raw = {
        "traceId": "A" * 32,
        "spanId": "B" * 16,
        "name": "x",
        "kind": "SPAN_KIND_SERVER",
        "startTimeUnixNano": "1790000000123456789",
        "status": {"code": "STATUS_CODE_ERROR", "message": "boom"},
        "attributes": [
            {"key": "i", "value": {"intValue": "7"}},
            {"key": "d", "value": {"doubleValue": 0.5}},
            {"key": "b", "value": {"boolValue": True}},
            {"key": "a", "value": {"arrayValue": {"values": [{"stringValue": "s"}]}}},
            {"key": "k", "value": {"kvlistValue": {"values": [{"key": "n", "value": {"intValue": 1}}]}}},
            {"key": "e", "value": {}},
        ],
        "events": [{"name": "retry", "timeUnixNano": "1790000000000000000", "attributes": []}],
        "links": [{"traceId": "C" * 32, "spanId": "D" * 16}],
    }
    (tmp_path / "t.json").write_text(json.dumps(_otlp([raw]), indent=2))  # pretty-printed single object
    (span,) = OtlpJsonFiles(tmp_path / "*.json").spans()
    assert span["trace_id"] == "a" * 32 and span["span_id"] == "b" * 16
    assert span["kind"] == "server"
    assert span["start_time"] == "2026-09-21T14:13:20.123Z"
    assert span["end_time"] is None
    assert span["status"] == {"code": "error", "message": "boom"}
    assert span["attributes"] == {"i": 7, "d": 0.5, "b": True, "a": ["s"], "k": {"n": 1}, "e": None}
    assert span["events"][0]["name"] == "retry"
    assert span["links"] == [{"trace_id": "c" * 32, "span_id": "d" * 16, "attributes": {}}]


@pytest.mark.parametrize(
    ("kind", "expected"), [(3, "client"), (0, "internal"), (None, "internal"), ("odd", "internal")]
)
def test_otlp_kinds(tmp_path: Path, kind, expected) -> None:
    raw = {"traceId": "a" * 32, "spanId": "b" * 16, "name": "x", "kind": kind, "startTimeUnixNano": 0}
    (tmp_path / "t.json").write_text(json.dumps(_otlp([raw])))
    assert OtlpJsonFiles(tmp_path / "t.json").spans()[0]["kind"] == expected


def test_otlp_errors(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="no OTLP JSON files"):
        OtlpJsonFiles(tmp_path / "*.json").files()
    (tmp_path / "bad.json").write_text("{nope")
    with pytest.raises(SourceError, match="not OTLP JSON"):
        OtlpJsonFiles(tmp_path / "bad.json").spans()
    (tmp_path / "bad.json").write_text(json.dumps(_otlp([{"name": "no ids"}])))
    with pytest.raises(SourceError, match="malformed OTLP span"):
        OtlpJsonFiles(tmp_path / "bad.json").spans()


def test_otlp_read_new_skips_unchanged_files(runs) -> None:
    source = OtlpJsonFiles(runs.otlp_path.parent / "*.json")
    spans, cursor = source.read_new({})
    assert len(spans) == 80
    assert source.read_new(cursor) == ([], cursor)


# --- source strings and generic sources ------------------------------------------------------------------------


def test_open_source_strings(tmp_path: Path) -> None:
    assert isinstance(open_source(f"hone:{tmp_path}"), HoneSpanStore)
    assert isinstance(open_source("otlp:traces/*.json"), OtlpJsonFiles)
    for bad in ("nope:x", "hone:", "just-a-path"):
        with pytest.raises(
            SourceError, match="use one of 'hone:<path>', 'otlp:<glob>'.*'flow:<storage>/<workflow>'"
        ):
            open_source(bad)


def test_read_new_for_any_record_source_looks_back_by_the_longest_span() -> None:
    # spans are written at their end, so a long parent can arrive after later-starting children
    source = FakeRecordSource([_span(1, end_time="2026-09-27T14:03:01.500Z"), _span(2, end_time=None)])
    spans, cursor = read_new(source, {})
    assert len(spans) == 2 and cursor == {"since": "2026-09-27T14:03:01.500Z", "lookback_s": 0.5}
    again, cursor2 = read_new(source, cursor)
    assert [s["span_id"] for s in again] == [f"{2:016x}"]  # re-read inside the lookback; ingest deduplicates
    assert cursor2 == cursor
    assert read_new(FakeRecordSource([]), cursor) == ([], cursor)
    assert read_new(FakeRecordSource([]), {}) == ([], {})
