"""The spans of one synthetic run (used by `synthetic.py`): hone-flow run-folder spans, the models and
select stores, and the plain-OTel variant."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from hone_lens.mapping import iso

WORKFLOW = "song_ideas"
STEP = "ideas"
GENERATOR = "gemma4-12b"
JUDGE = "qwen3-8b"
JUDGE_SAME_FAMILY = "gemma4-4b"
CONTEXT_LIMIT = 4096

Span = dict[str, Any]


def hex_id(*parts: object, n: int) -> str:
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:n]


@dataclass
class RunPlan:
    """Everything decided for one run before its spans are written."""

    seed: int
    index: int
    rng: random.Random
    plants: tuple[str, ...]
    version: str
    start: datetime

    @property
    def trace_id(self) -> str:
        return hex_id(self.seed, self.index, "trace", n=32)

    @property
    def run_id(self) -> str:
        return f"run-{self.seed}-{self.index:05d}"

    @property
    def resumed(self) -> bool:
        """Every fifth run was interrupted once and finished by a resume call."""
        return self.index % 5 == 4

    @property
    def shared(self) -> dict[str, str]:
        item = f"item-{self.seed}-{self.index:05d}"
        return {"hone.run_id": self.run_id, "hone.item": item, "hone.step": STEP}

    def span_id(self, key: str) -> str:
        return hex_id(self.seed, self.index, key, n=16)

    def span(
        self,
        name: str,
        parent: str | None,
        at: tuple[float, float],
        attributes: dict[str, Any],
        *,
        error: str = "",
        key: str = "",
    ) -> Span:
        """One span; `at` is (offset from the run start, duration), both in milliseconds."""
        start = self.start + timedelta(milliseconds=at[0])
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id(key or name),
            "parent_span_id": parent,
            "name": name,
            "kind": "client" if name.startswith("hone.models.") else "internal",
            "start_time": iso(start),
            "end_time": iso(start + timedelta(milliseconds=at[1])),
            "status": {"code": "error" if error else "ok", "message": error},
            "attributes": {"hone.schema_version": "1", **attributes},
            "events": [],
            "links": [],
            "resource": {
                "service.name": "oneshotstudio",
                "hone.package": "hone-" + name.split(".")[1],
                "hone.package.version": "0.1.0",
            },
        }


def run_spans(
    plan: RunPlan, prompt: str, sections: list[dict[str, Any]], output: str
) -> dict[str, list[Span]]:
    """All spans of one run, keyed by store: `flow`, `models`, `select`, and `otlp` (plain OTel variant)."""
    gen_ms = plan.rng.lognormvariate(math.log(2000), 0.25) * (1.8 if plan.version == "4" else 1.0)
    judge_ms = plan.rng.lognormvariate(math.log(800), 0.2)
    step_ms = gen_ms + judge_ms + 200
    step_id = plan.span_id("hone.flow.step")
    flow = [
        *_run_calls(plan, step_ms),
        plan.span("hone.flow.step", plan.span_id("hone.flow.run"), (10, step_ms), _step_attrs(plan)),
    ]
    call = _generator_call(plan, step_id, prompt, sections, output=output, ms=gen_ms)
    select, judge_calls = _selection(plan, step_id, gen_ms + 30, judge_ms)
    otlp = _otlp_variant(plan, prompt, call, gen_ms)
    return {"flow": flow, "models": [call, *judge_calls], "select": select, "otlp": otlp}


_FLOW = {"hone.flow.workflow": WORKFLOW, "hone.flow.workflow_version": "1"}


def _run_calls(plan: RunPlan, step_ms: float) -> list[Span]:
    """The `hone.flow.run` span of each call on the run: one, or an interrupted call and its resume."""
    attrs = {"hone.run_id": plan.run_id, **_FLOW}
    final = plan.span("hone.flow.run", None, (0, step_ms + 50), {**attrs, "hone.flow.status": "completed"})
    if not plan.resumed:
        return [final]
    first = plan.span(
        "hone.flow.run",
        None,
        (-60_000, 1000),
        {**attrs, "hone.flow.status": "interrupted"},
        error="run interrupted",
        key="hone.flow.run.first",
    )
    # the plant: the step's code changed between the calls but its version did not (hone-flow warns)
    plant = "source_changed_version_unchanged" in plan.plants
    if plant and random.Random(f"{plan.seed}:{plan.index}:resume").random() < 0.15:
        warning = {"kind": "source_changed_version_unchanged", "step": STEP, "at": final["start_time"]}
        warning |= {"old": _source_hash(False), "new": _source_hash(True)}
        final["events"] = [{"name": "warning", "time": final["start_time"], "attributes": warning}]
    return [first, final]


def _source_hash(changed: bool) -> str:
    return hex_id("source", changed, n=16)


def _step_attrs(plan: RunPlan) -> dict[str, Any]:
    return {
        **plan.shared,
        **_FLOW,
        "hone.flow.step_version": "1",
        # the step's source changes with prompt v3 while its version stays "1"
        "hone.flow.source_hash": _source_hash(plan.version != "2"),
        "hone.flow.status": "done",
        "hone.flow.attempt": 1,
        "hone.flow.params": json.dumps({"temperature": 0.9}),
        "hone.flow.deterministic": False,
        "hone.flow.labels": "[]",
        "hone.flow.resource": "gpu:ollama",
    }


def _generator_call(
    plan: RunPlan, parent: str, prompt: str, sections: list[dict[str, Any]], *, output: str, ms: float
) -> Span:
    rng = plan.rng
    truncated = "truncation" in plan.plants and rng.random() < 0.05
    prompt_tokens = rng.randrange(3850, 4000) if truncated else len(prompt) // 4 + rng.randrange(5)
    if truncated:
        output = output[: len(output) // 2]
    attrs: dict[str, Any] = {
        **plan.shared,
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "ollama",
        "gen_ai.request.model": f"{GENERATOR}:latest",
        "gen_ai.response.model": f"{GENERATOR}:latest",
        "gen_ai.request.temperature": 0.9,
        "gen_ai.request.max_tokens": 400,
        "gen_ai.response.finish_reasons": ["length" if truncated else "stop"],
        "gen_ai.usage.input_tokens": prompt_tokens,
        "gen_ai.usage.output_tokens": len(output) // 4 + rng.randrange(5),
        "gen_ai.input.messages": json.dumps([{"role": "user", "content": prompt}]),
        "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": output}]),
        "hone.models.model_id": GENERATOR,
        "hone.models.prompt.template_id": "song_idea",
        "hone.models.prompt.template_version": plan.version,
        "hone.models.prompt.sections": json.dumps(sections),
        # the idea is requested as JSON; a truncated answer needs repair (hone.models.structured.path)
        "hone.models.structured.path": "repaired" if truncated else "parsed",
        "hone.models.context.limit": CONTEXT_LIMIT,
        "hone.models.context.estimated_prompt_tokens": prompt_tokens,
        **_gpu_attrs(plan),
    }
    if "nondeterministic_seed" in plan.plants:
        attrs["gen_ai.request.seed"] = rng.randrange(2**31)
    error = "CUDA out of memory" if "gpu_thrash" in plan.plants and rng.random() < 0.02 else ""
    return plan.span("hone.models.chat", parent, (20, ms), attrs, key="generate", error=error)


def _gpu_attrs(plan: RunPlan) -> dict[str, Any]:
    thrash = "gpu_thrash" in plan.plants
    return {
        "hone.models.gpu.lease_wait_ms": plan.rng.randrange(3000, 9000) if thrash else plan.rng.randrange(40),
        "hone.models.gpu.unloaded": thrash,
        "hone.models.gpu.vram_before_mb": 5000,
        "hone.models.gpu.vram_after_mb": 7400 if thrash else 5200,
    }


def _selection(plan: RunPlan, parent: str, offset: float, judge_ms: float) -> tuple[list[Span], list[Span]]:
    select_id, score_id = plan.span_id("hone.select.run"), plan.span_id("hone.select.score")
    # the plant: the judge's answer failed to parse, and the failure was recorded as a score of 0
    failed = "score_zero_spike" in plan.plants and plan.rng.random() < 0.1
    value = 0.0 if failed else round(plan.rng.uniform(0.45, 0.95), 3)
    scorer = {**plan.shared, "hone.scorer": "quality", "hone.candidate_id": f"cand-{plan.index}"}
    decision = {
        **plan.shared,
        "hone.select.winner_id": f"cand-{plan.index}",
        "hone.select.fallback_used": False,
    }
    select = [
        plan.span(
            "hone.select.run",
            parent,
            (offset, judge_ms + 20),
            {**plan.shared, "hone.select.policy": "best", "hone.select.n": 1},
        ),
        plan.span(
            "hone.select.score",
            select_id,
            (offset + 5, judge_ms + 5),
            {**scorer, "hone.select.scorer": "quality", "hone.select.score.value": value},
        ),
        plan.span("hone.select.decision", select_id, (offset + judge_ms + 12, 3), decision),
    ]
    calls = [_judge_call(plan, score_id, scorer, (offset + 8, judge_ms), failed=failed)]
    if "vision_400" in plan.plants:
        calls.append(_vision_call(plan, select_id, offset + 9))
    return select, calls


def _judge_call(
    plan: RunPlan, parent: str, scorer: dict[str, str], at: tuple[float, float], *, failed: bool
) -> Span:
    model = JUDGE_SAME_FAMILY if "judge_equals_generator" in plan.plants else JUDGE
    attrs = {
        **scorer,
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "ollama",
        "gen_ai.request.model": f"{model}:latest",
        "gen_ai.response.finish_reasons": ["stop"],
        "gen_ai.usage.input_tokens": 220,
        "gen_ai.usage.output_tokens": 12,
        "hone.models.model_id": model,
        "hone.models.structured.path": "failed" if failed else "constrained",
        **_gpu_attrs(plan),
    }
    return plan.span("hone.models.chat", parent, at, attrs, key="judge")


def _vision_call(plan: RunPlan, parent: str, offset: float) -> Span:
    content = [{"type": "text", "text": "Rate this cover art."}, {"type": "image", "path": "cover.png"}]
    attrs = {
        **plan.shared,
        "hone.scorer": "cover_art",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": f"{GENERATOR}:latest",
        "hone.models.model_id": GENERATOR,
        "gen_ai.input.messages": json.dumps([{"role": "user", "content": content}]),
    }
    error = f"HTTP 400: model {GENERATOR} does not support image input"
    return plan.span("hone.models.chat", parent, (offset, 40), attrs, error=error, key="vision")


def _otlp_variant(plan: RunPlan, prompt: str, call: Span, ms: float) -> list[Span]:
    """The same generator call as a tool without honeworks would record it (older gen_ai names)."""
    a = call["attributes"]
    output = json.loads(a["gen_ai.output.messages"])[0]["content"]
    step_id = hex_id(plan.seed, plan.index, "otlp-step", n=16)
    start = plan.start + timedelta(milliseconds=5)
    base: Span = {
        "trace_id": hex_id(plan.seed, plan.index, "otlp-trace", n=32),
        "events": [],
        "links": [],
        "status": {"code": "ok", "message": ""},
        "resource": {"service.name": WORKFLOW},
        "start_time": iso(start),
    }
    step: Span = {
        **base,
        "span_id": step_id,
        "parent_span_id": None,
        "name": "generate_idea",
        "kind": "internal",
        "end_time": iso(start + timedelta(milliseconds=ms + 40)),
        "attributes": {},
    }
    chat_attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.system": "ollama",
        "gen_ai.request.model": a["gen_ai.request.model"],
        "gen_ai.request.temperature": 0.9,
        "gen_ai.response.finish_reasons": a["gen_ai.response.finish_reasons"],
        "gen_ai.usage.prompt_tokens": a["gen_ai.usage.input_tokens"],
        "gen_ai.usage.completion_tokens": a["gen_ai.usage.output_tokens"],
        "gen_ai.input.messages": json.dumps(
            [{"role": "user", "parts": [{"type": "text", "content": prompt}]}]
        ),
        "gen_ai.output.messages": json.dumps(
            [{"role": "assistant", "parts": [{"type": "text", "content": output}]}]
        ),
    }
    chat: Span = {
        **base,
        "span_id": hex_id(plan.seed, plan.index, "otlp-chat", n=16),
        "parent_span_id": step_id,
        "name": f"chat {a['gen_ai.request.model']}",
        "kind": "client",
        "end_time": iso(start + timedelta(milliseconds=ms)),
        "attributes": chat_attrs,
    }
    return [step, chat]
