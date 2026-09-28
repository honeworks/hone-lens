from importlib.metadata import EntryPoint

import pytest
from typer.testing import CliRunner

from hone_lens import cli
from hone_lens.errors import HoneLensError
from hone_lens.testing import FakeEmbedder, FakeReplayer, FakeTextClient, synthetic_runs


def test_load_port_by_name_and_argument(monkeypatch) -> None:
    eps = [
        EntryPoint("fake", "hone_lens.testing:FakeEmbedder", "hone.embedders"),
        EntryPoint("openai", "hone_lens.adapters.openai:OpenAITextClient", "hone.embedders"),
    ]
    monkeypatch.setattr(cli, "entry_points", lambda group: eps if group == "hone.embedders" else [])
    assert cli.load_port("hone.embedders", None) is None
    assert isinstance(cli.load_port("hone.embedders", "fake"), FakeEmbedder)
    assert cli.load_port("hone.embedders", "openai:m-1").model == "m-1"  # NAME:ARG calls the factory with ARG
    with pytest.raises(
        HoneLensError, match=r"no hone.embedders entry point 'x'; installed: \['fake', 'openai'\]"
    ):
        cli.load_port("hone.embedders", "x")
    with pytest.raises(
        HoneLensError, match=r"hone.embedders 'openai' could not be made from 'openai'; try openai:MODEL"
    ):
        cli.load_port("hone.embedders", "openai")
    with pytest.raises(HoneLensError, match="installed: none"):
        cli.load_port("hone.replayers", "x")


def test_llm_stages_through_the_cli(tmp_path, monkeypatch) -> None:
    ports = {
        "hone.text_clients": FakeTextClient.analyst,
        "hone.embedders": FakeEmbedder.semantic,
        "hone.replayers": FakeReplayer.removing_section_reduces_similarity,
    }
    monkeypatch.setattr(cli, "load_port", lambda group, spec: ports[group]() if spec else None)
    runs = synthetic_runs(tmp_path, n_runs=200, plant=["homogeneity_from_example"])
    ws = str(tmp_path / "lens")
    runner = CliRunner()
    assert runner.invoke(cli.app, ["ingest", f"hone:{runs.root}", "-w", ws]).exit_code == 0
    out = runner.invoke(
        cli.app, ["analyze", "song_ideas", "--llm", "x", "--embedder", "x", "--budget", "5 calls", "-w", ws]
    ).output
    assert "share one pattern" in out and "LLM cost: $0.0000 in 1 calls" in out
    out = runner.invoke(
        cli.app,
        [
            "test",
            "F-0001",
            "--replayer",
            "x",
            "--embedder",
            "x",
            "--variants",
            "1",
            "--samples",
            "10",
            "-w",
            ws,
        ],
    ).output
    assert out.startswith("F-0001: confirmed; largest_cluster_share")
    out = runner.invoke(
        cli.app,
        [
            "test",
            "F-0001",
            "--replayer",
            "x",
            "--embedder",
            "x",
            "--variants",
            "1",
            "--samples",
            "10",
            "--json",
            "-w",
            ws,
        ],
    ).output
    assert '"confirmed": true' in out


def test_label_refusal_is_an_error_exit(tmp_path, monkeypatch) -> None:
    llm = FakeTextClient.analyst()
    monkeypatch.setattr(cli, "load_port", lambda group, spec: llm if spec else None)
    runs = synthetic_runs(tmp_path, n_runs=60, plant=["homogeneity_from_example"])
    ws = str(tmp_path / "lens")
    runner = CliRunner()
    runner.invoke(cli.app, ["ingest", f"hone:{runs.root}", "-w", ws])
    out = runner.invoke(cli.app, ["label", "song_ideas", "--llm", "x", "--yes", "-w", ws])
    assert out.exit_code == 1 and "no taxonomy for 'song_ideas'" in out.output
    review = runner.invoke(
        cli.app, ["review", "song_ideas", "--llm", "x", "--sample", "5", "-w", ws], input="\n" * 40
    )
    assert review.exit_code == 1 and "needs a person at a terminal" in review.output  # CliRunner has no TTY
