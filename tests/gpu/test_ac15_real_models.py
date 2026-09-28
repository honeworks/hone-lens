"""AC-15 [real]: real embeddings (Ollama `/api/embed` through a plain urllib adapter) and a real analysis LLM
(OpenAI-compatible `/v1`) on 300 synthetic runs: the homogeneity cluster is found and its description
mentions the planted pattern. Run through the GPU lock: `scripts/gpu-lock.sh uv run pytest -m gpu`."""

import json
import math
import os
import urllib.request

import pytest

import hone_lens as tl
from hone_lens.adapters.openai import OpenAITextClient
from hone_lens.testing import synthetic_runs

OLLAMA_URL = os.environ.get("HONE_TEST_OLLAMA_URL", "http://127.0.0.1:11434")

pytestmark = [pytest.mark.gpu, pytest.mark.ollama, pytest.mark.e2e]

PATTERN_WORDS = (
    "storm",
    "captain",
    "ship",
    "sea",
    "water",
    "thunder",
    "lantern",
    "wave",
    "nautical",
    "maritime",
)


class OllamaEmbedder:
    """The plain urllib embedding adapter for this test (Ollama `/api/embed`)."""

    def __init__(self, model: str) -> None:
        self.model_id = model
        self.dimensions = len(self.embed(["probe"])[0])

    def embed(self, texts, *, trace=None) -> list[list[float]]:
        if not texts:
            return []
        request = urllib.request.Request(  # noqa: S310 - local Ollama URL
            f"{OLLAMA_URL}/api/embed",
            data=json.dumps({"model": self.model_id, "input": list(texts)}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=300) as r:  # noqa: S310
            vectors = json.load(r)["embeddings"]
        return [[x / (math.sqrt(sum(v * v for v in vec)) or 1.0) for x in vec] for vec in vectors]


def test_ac15_real_embeddings_and_llm_find_and_describe_the_pattern(tmp_path, ollama_model) -> None:
    embed_model = ollama_model("HONE_TEST_EMBED_MODEL", "nomic-embed-text:latest")
    text_model = ollama_model("HONE_TEST_TEXT_MODEL", "gemma4-12b:latest")
    runs = synthetic_runs(tmp_path, n_runs=300, plant=["homogeneity_from_example"])
    llm = OpenAITextClient(
        text_model, base_url=f"{OLLAMA_URL}/v1", api_key=os.environ.get("OLLAMA_API_KEY", "ollama")
    )
    ws = tl.Workspace(tmp_path / "lens", embedder=OllamaEmbedder(embed_model), llm=llm)
    ws.ingest(f"hone:{runs.root}")
    report = ws.analyze("song_ideas", budget="4 calls")
    homogeneity = [
        f
        for f in report.findings
        if f.detector == "homogeneity" and f.metric["name"] == "largest_cluster_share"
    ]
    assert homogeneity, [f.title for f in report.findings]
    f = homogeneity[0]
    assert 0.3 <= f.metric["value"] <= 0.55, f.metric
    assert f.details["closeness"] and f.details["closeness"][0]["section"] == "format_example"
    description = f"{f.details.get('cluster_description', '')} {f.details.get('in_common', '')}".lower()
    assert any(word in description for word in PATTERN_WORDS), (description, report.notes)
