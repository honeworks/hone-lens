import json

from hone_lens.testing import FakeTextClient


def _ask(task: str, payload: dict) -> dict:
    client = FakeTextClient.analyst()
    r = client.complete([{"role": "user", "content": json.dumps(payload)}], schema={"title": task})
    assert r.error is None
    return r.parsed


def test_cluster_description_names_shared_words() -> None:
    items = [
        "A storm opens the song as Captain Mara",
        "A storm opens as Captain Rook",
        "storm and Captain Anya",
    ]
    out = _ask("cluster_description", {"items": items})
    assert "storm" in out["in_common"] and "captain" in out["in_common"]
    assert (
        _ask("cluster_description", {"items": ["alpha beta", "gamma delta"]})["in_common"]
        == "nothing specific"
    )


def test_notes_taxonomy_and_labels() -> None:
    storm = "A storm opens the song as Captain Mara steers."
    cut = "A quiet verse about a baker who"
    assert "storm" in _ask("trace_note", {"output": storm})["note"]
    assert "cut off" in _ask("trace_note", {"output": cut})["note"]
    assert _ask("trace_note", {"output": "Fine idea."})["note"] == "no obvious problem"
    notes = [_ask("trace_note", {"output": t})["note"] for t in (storm, storm, cut, "Fine.")]
    modes = _ask("taxonomy", {"notes": notes})["modes"]
    assert len(modes) == 2
    assert _ask("label", {"output": storm, "modes": modes})["mode"] == modes[0]["name"]
    assert _ask("label", {"output": "Fine.", "modes": modes})["mode"] is None


def test_hypothesis_and_variants() -> None:
    h = _ask(
        "hypothesis", {"causes": [{"kind": "prompt_section", "target": "format_example", "effect_size": 0.8}]}
    )
    assert "format_example" in h["hypothesis"]
    assert "No recorded input" in _ask("hypothesis", {"causes": []})["hypothesis"]
    assert len(_ask("section_variants", {"n": 3})["variants"]) == 3


def test_unknown_task_and_plain_prompt() -> None:
    client = FakeTextClient.analyst()
    assert client.complete([{"role": "user", "content": "hi"}]).text == "OK"
    assert client.complete([{"role": "user", "content": "hi"}], schema={"title": "other"}).parsed == {
        "ok": True
    }
