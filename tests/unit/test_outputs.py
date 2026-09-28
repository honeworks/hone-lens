import hashlib
import importlib
import json

import numpy as np
import pytest

import hone_lens as tl
from hone_lens.errors import HoneLensError
from hone_lens.outputs.closeness import closeness, section_texts
from hone_lens.outputs.cluster import cluster, mean_pairwise
from hone_lens.outputs.embed import embed
from hone_lens.outputs.homogeneity import homogeneity, measures, outliers
from hone_lens.testing import FakeEmbedder, FakeRecordSource, synthetic_runs


@pytest.fixture
def db(tmp_path) -> tl.AnalyticsDB:
    return tl.AnalyticsDB(tmp_path / "lens.db")


def _unit(rows) -> np.ndarray:
    v = np.asarray(rows, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_embed_caches_by_model_and_text(db) -> None:
    e = FakeEmbedder(dimensions=8)
    first = embed(db, e, ["a", "b", "a"])
    assert first.shape == (3, 8) and np.allclose(first[0], first[2])
    assert e.calls == [["a", "b"]]  # each distinct text once
    again = embed(db, e, ["b", "c"])
    assert e.calls[-1] == ["c"] and np.allclose(again[0], first[1])
    assert embed(db, e, []).shape == (0, 8)
    other = FakeEmbedder(dimensions=8, by_words=True)  # another model id: its own cache
    embed(db, other, ["a"])
    assert other.calls == [["a"]]


def test_embed_errors_are_explicit(db) -> None:
    class Broken:
        model_id, dimensions = "broken", 2

        def embed(self, texts, *, trace=None):
            raise RuntimeError("server down")

    class Short:
        model_id, dimensions = "short", 2

        def embed(self, texts, *, trace=None):
            return [[1.0, 0.0]]

    with pytest.raises(HoneLensError, match="embedder 'broken' failed: RuntimeError: server down"):
        embed(db, Broken(), ["x"])
    with pytest.raises(HoneLensError, match="returned 1 vectors for 2 texts"):
        embed(db, Short(), ["x", "y"])


def test_embed_normalizes_and_batches(db, monkeypatch) -> None:
    embed_module = importlib.import_module("hone_lens.outputs.embed")  # the package re-exports `embed()`

    monkeypatch.setattr(embed_module, "BATCH", 2)
    e = FakeEmbedder(dimensions=4)
    vectors = embed(db, e, ["a", "b", "c", "d", "e"])
    assert [len(c) for c in e.calls] == [2, 2, 1]
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_cluster_finds_dense_groups_and_leaves_the_rest() -> None:
    rng = np.random.default_rng(1)
    base_a, base_b = rng.normal(size=32), rng.normal(size=32)
    group_a = [base_a + 0.05 * rng.normal(size=32) for _ in range(30)]
    group_b = [base_b + 0.05 * rng.normal(size=32) for _ in range(12)]
    noise = [rng.normal(size=32) for _ in range(40)]
    vectors = _unit([*group_a, *noise, *group_b])
    c = cluster(vectors)
    assert c.sizes() == [30, 12]  # largest first
    assert set(c.labels[:30]) == {0} and set(c.labels[30:70]) == {-1} and set(c.labels[70:]) == {1}
    again = cluster(vectors)
    assert again.centers == c.centers and (again.labels == c.labels).all()
    assert 0.0 < (mean_pairwise(vectors, c.sample) or 0) < 0.5


def test_cluster_samples_large_inputs(monkeypatch) -> None:
    cluster_module = importlib.import_module("hone_lens.outputs.cluster")

    monkeypatch.setattr(cluster_module, "SAMPLE", 50)
    vectors = _unit(np.random.default_rng(0).normal(size=(200, 16)).tolist())
    c = cluster(vectors, seed=3)
    assert len(c.sample) == 50 and c.centers == [] and set(c.labels) == {-1}
    assert (c.sample == cluster(vectors, seed=3).sample).all()
    assert not (c.sample == cluster(vectors, seed=4).sample).all()


def test_cluster_tiny_inputs() -> None:
    c = cluster(_unit([[1.0, 0.0]]))
    assert c.centers == [] and list(c.labels) == [-1] and mean_pairwise(_unit([[1.0, 0.0]]), c.sample) is None
    empty = cluster(np.zeros((0, 2), dtype=np.float32))
    assert empty.centers == [] and len(empty.labels) == 0


def _rows(texts: list[str], version: str | None = None) -> list[dict]:
    return [
        {
            "span_id": f"{i:016x}",
            "start_time": f"2026-09-01T00:00:{i % 60:02d}.000Z",
            "text": t,
            "text_sha": hashlib.sha256(t.encode()).hexdigest(),
            "template_version": version,
        }
        for i, t in enumerate(texts)
    ]


def test_duplicates_and_measures() -> None:
    texts = ["the same answer again"] * 30 + [f"answer number {i} about thing {i * 7}" for i in range(10)]
    rows = _rows(texts)
    vectors = embed_texts(texts)
    c = cluster(vectors)
    m = measures(rows, vectors, c)
    assert m["distinct_rate"] == 11 / 40 and m["largest_cluster_share"] == 30 / 40
    assert m["repeated_ngram_rate"] > 0.3 and m["clusters"] >= 1
    found = {f.metric["name"]: f for f in homogeneity({"workflow": "w", "step": "s"}, rows, m, c, {})}
    assert found["distinct_rate"].severity == "high" and found["distinct_rate"].affected == 1
    assert found["largest_cluster_share"].metric["baseline"] is None  # no prompt versions to compare


def embed_texts(texts: list[str]) -> np.ndarray:
    return _unit(FakeEmbedder.semantic().embed(texts))


def test_outliers() -> None:
    texts = [f"a song about rain and trains number {i % 5}" for i in range(60)]
    texts += ["zzz qqq xxx", "vvv www yyy", "kkk jjj hhh", "ppp bbb mmm", "fff ggg ttt"]
    rows = _rows(texts)
    c = cluster(embed_texts(texts))
    (f,) = outliers({"workflow": "w"}, rows, c)
    assert f.affected == 5 and f.detector == "outliers" and f.category == "quality"
    assert f.metric["value"] == 5 / 65 and set(f.evidence) == {r["span_id"] for r in rows[60:]}
    two_far = texts[:60] + texts[60:62]
    assert outliers({"workflow": "w"}, _rows(two_far), cluster(embed_texts(two_far))) == []
    assert outliers({"workflow": "w"}, rows[:10], cluster(embed_texts(texts[:10]))) == []  # too few outputs


def test_clusters_keep_their_descriptions(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=100, plant=["homogeneity_from_example"])
    ws = tl.Workspace(tmp_path / "lens", embedder=FakeEmbedder.semantic())
    ws.ingest(f"hone:{runs.root}")
    ws.analyze(stages=("outputs",))
    (c,) = ws.db.query("SELECT id, size, total, members FROM clusters")
    assert c["total"] == 100 and 30 <= c["size"] <= 50
    ws.db.describe_cluster(c["id"], "storm openings", "storm, captain")
    ws.analyze(stages=("outputs",))
    (again,) = ws.db.query("SELECT id, description FROM clusters")
    assert again == {"id": c["id"], "description": "storm openings"}


def test_outputs_stage_needs_an_embedder(tmp_path) -> None:
    ws = tl.Workspace(tmp_path / "lens")
    report = ws.analyze(stages=("outputs",))
    assert report.notes == ["outputs stage skipped: no embedder; pass Workspace(embedder=...)"]


def _call(
    i: int, text: str, *, step: str = "s", workflow: str = "w", prompt: str = "", sections=None, version=None
):
    attributes: dict[str, object] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "m",
        "hone.run_id": f"run-{i}",
        "hone.step": step,
        "gen_ai.input.messages": json.dumps([{"role": "user", "content": prompt}]),
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": text}]),
    }
    if sections is not None:
        attributes["hone.models.prompt.sections"] = json.dumps(sections)
        attributes["hone.models.prompt.template_version"] = version
    return {
        "trace_id": f"{i:032x}",
        "span_id": f"{i:016x}",
        "name": "hone.models.chat",
        "start_time": f"2026-09-01T00:{i // 60 % 60:02d}:{i % 60:02d}.000Z",
        "resource": {"service.name": workflow},
        "attributes": attributes,
    }


def _workspace(tmp_path, spans, embedder=None) -> tl.Workspace:
    ws = tl.Workspace(tmp_path / "lens", embedder=embedder or FakeEmbedder.semantic())
    ws.ingest(FakeRecordSource(spans))
    return ws


PROMPT = "Write a song idea.\n\nExample: a storm at sea with captain Mara."
SECTIONS = [
    {"id": "task", "version": "1", "start": 0, "end": 18},
    {"id": "example", "version": "2", "start": 20, "end": 63},
]


def test_section_texts_and_closeness(tmp_path) -> None:
    copies = [
        _call(i, f"a storm at sea with captain {i}", prompt=PROMPT, sections=SECTIONS) for i in range(10)
    ]
    others = [
        _call(i, f"a baker {i} bakes bread in town", prompt=PROMPT, sections=SECTIONS) for i in range(10, 20)
    ]
    blank = [{"id": "empty", "version": "1", "start": 18, "end": 20}]
    ws = _workspace(tmp_path, [*copies, *others, _call(20, "x", prompt=PROMPT, sections=blank)])
    ids = [f"{i:016x}" for i in range(21)]
    sections = section_texts(ws.db, ids)
    assert sections == {
        ("task", "1"): "Write a song idea.",
        ("example", "2"): PROMPT[20:],
    }  # blank text dropped
    assert section_texts(ws.db, ids[10:12]) == {
        ("task", "1"): "Write a song idea.",
        ("example", "2"): PROMPT[20:],
    }
    vectors = embed_texts(
        [f"a storm at sea with captain {i}" for i in range(10)] + [f"a baker {i}" for i in range(10)]
    )
    members = np.array([True] * 10 + [False] * 10)
    top, rest = closeness(ws.db, ws.embedder, sections, vectors, members)  # type: ignore[arg-type]
    assert top["section"] == "example" and top["difference"] > 0.3 > rest["difference"]
    assert closeness(ws.db, ws.embedder, sections, vectors, np.ones(20, dtype=bool)) == []  # type: ignore[arg-type]
    assert closeness(ws.db, ws.embedder, {}, vectors, members) == []  # type: ignore[arg-type]


def test_too_few_outputs_are_not_analyzed(tmp_path) -> None:
    ws = _workspace(tmp_path, [_call(i, "same answer") for i in range(19)])
    assert ws.analyze(stages=("outputs",)).findings == []
    assert ws.db.query("SELECT count(*) AS n FROM clusters")[0]["n"] == 0
    ws = _workspace(tmp_path / "20", [_call(i, "same answer") for i in range(20)])
    metrics = {f.metric["name"] for f in ws.analyze(stages=("outputs",)).findings}
    assert metrics == {"largest_cluster_share", "distinct_rate"}


def test_clusters_per_workflow_and_step(tmp_path) -> None:
    spans = [_call(i, "same answer", step="a") for i in range(20)]
    spans += [_call(i, "other answer", step="b", workflow="v") for i in range(20, 40)]
    ws = _workspace(tmp_path, spans)
    ws.analyze(stages=("outputs",))
    assert ws.db.query("SELECT workflow, step FROM clusters ORDER BY step") == [
        {"workflow": "w", "step": "a"},
        {"workflow": "v", "step": "b"},
    ]
    assert {f.scope["workflow"] for f in ws.analyze("v", stages=("outputs",)).findings} == {"v"}
    assert ws.analyze("nothing", stages=("outputs",)).findings == []
    assert len(ws.db.query("SELECT id FROM clusters")) == 2  # re-analysing one workflow kept the other's


def test_regressed_share_below_the_threshold(tmp_path) -> None:
    v1 = [
        _call(i, f"idea {i} about a baker in a town number {i * 7}", sections=[], version="1")
        for i in range(100)
    ]
    v2 = [
        _call(i, f"idea {i} about a baker in a town number {i * 7}", sections=[], version="2")
        for i in range(100, 170)
    ]
    v2 += [_call(i, "a storm at sea with captain Mara", sections=[], version="2") for i in range(170, 200)]
    ws = _workspace(tmp_path, v1 + v2)
    (f,) = ws.analyze(stages=("outputs",)).findings
    assert f.metric["value"] == 0.15  # under the 20% share threshold, but up from 0% on version 1
    assert f.metric["baseline"] == 0.0 and f.details["by_prompt_version"] == {"1": 0.0, "2": 0.3}


def test_embedder_failure_keeps_stage_1(tmp_path) -> None:
    class Down:
        model_id, dimensions = "down", 4

        def embed(self, texts, *, trace=None):
            raise ConnectionError("refused")

    ws = _workspace(tmp_path, [_call(i, f"text {i}") for i in range(30)], embedder=Down())
    report = ws.analyze(stages=("stats", "outputs"))
    assert report.notes == ["outputs stage failed: embedder 'down' failed: ConnectionError: refused"]


def test_embedding_cache_round_trip_zero_vectors_and_size_changes(db) -> None:
    class Zero:
        model_id, dimensions = "zero", 3

        def __init__(self):
            self.calls = 0

        def embed(self, texts, *, trace=None):
            self.calls += 1
            return [[0.0] * self.dimensions for _ in texts]

    z = Zero()
    v = embed(db, z, ["a"])
    assert np.isfinite(v).all() and not v.any()
    e = FakeEmbedder(dimensions=5)
    first = embed(db, e, ["b"])
    assert np.array_equal(embed(db, e, ["b"]), first)  # float32 bytes round-trip exactly
    z.dimensions = 4  # same model id, other size: the cached vector is not reused
    assert embed(db, z, ["a"]).shape == (1, 4) and z.calls == 2


def test_no_clusters_no_share_finding() -> None:
    texts = [f"answer {i} about thing {i * 13} in place {i * 7}" for i in range(30)]
    rows = _rows(texts)
    c = cluster(embed_texts(texts))
    m = measures(rows, embed_texts(texts), c)
    assert c.centers == [] and m["largest_cluster_share"] == 0.0
    assert homogeneity({"workflow": "w"}, rows, m, c, {}) == []
