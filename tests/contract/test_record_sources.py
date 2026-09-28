"""The built-in record sources satisfy the RecordSource contract (current.md §6)."""

import json

from hone_lens import ports
from hone_lens.sources import HoneSpanStore, OtlpJsonFiles
from hone_lens.testing import check_record_source, synthetic_runs


def test_builtin_sources(tmp_path) -> None:
    runs = synthetic_runs(tmp_path, n_runs=20, plant=["vision_400", "gpu_thrash"])
    jsonl = tmp_path / "jsonl" / "spans.jsonl"
    jsonl.parent.mkdir()
    jsonl.write_text("".join(json.dumps(s) + "\n" for s in HoneSpanStore(runs.root).spans()))
    for source in (
        HoneSpanStore(runs.root),
        HoneSpanStore(runs.root / "models" / "spans.db"),
        HoneSpanStore(jsonl),
        OtlpJsonFiles(runs.otlp_path),
    ):
        assert isinstance(source, ports.RecordSource)
        check_record_source(source)
