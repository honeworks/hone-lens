"""Stage 3: the analysis LLM describes each output cluster (Clio / Kura style, one level).

For every cluster without a description, a sample of its outputs goes to the LLM, which writes a
description and a short "what these outputs have in common". Descriptions are stored with the cluster and
kept while the cluster keeps its id, so re-analysis does not pay again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from hone_lens.budget import Budget
from hone_lens.errors import BudgetExceeded
from hone_lens.findings import Finding, sample_ids
from hone_lens.llm import ask
from hone_lens.ports import TextClient
from hone_lens.store import AnalyticsDB

SAMPLE = 20
MAX_CHARS = 600
INSTRUCTION = (
    "You analyze outputs of an AI workflow. The user message is JSON with `items`: a sample of outputs "
    "that an embedding model put into one cluster. Describe the cluster in one sentence (`description`) "
    "and name concretely what the outputs have in common (`in_common`): shared structure, openings, "
    "names, images."
)
PROPERTIES = {"description": {"type": "string"}, "in_common": {"type": "string"}}


@dataclass
class Described:
    described: int = 0
    notes: list[str] = field(default_factory=list[str])
    stopped: bool = False


def describe_clusters(
    db: AnalyticsDB, llm: TextClient, budget: Budget, workflow: str | None = None
) -> Described:
    """Describe the clusters (of `workflow`) that have no description yet, largest first, until the budget
    is spent."""
    done = Described()
    clusters = db.query(
        "SELECT id, members FROM clusters WHERE description IS NULL AND (?1 IS NULL OR workflow = ?1) "
        "ORDER BY size DESC, id",
        (workflow,),
    )
    for cluster in clusters:
        members = [{"span_id": s} for s in json.loads(cluster["members"])]
        items = db.output_texts(sample_ids(members, SAMPLE), MAX_CHARS)
        try:
            answer, error = ask(llm, "cluster_description", INSTRUCTION, {"items": items}, PROPERTIES, budget)
        except BudgetExceeded as e:
            done.stopped = True
            done.notes.append(
                f"describe stopped: {e}; {len(clusters) - done.described} clusters left undescribed"
            )
            break
        if answer is None:
            done.notes.append(f"cluster {cluster['id']} not described: {error}")
            continue
        db.describe_cluster(cluster["id"], str(answer["description"]), str(answer["in_common"]))
        done.described += 1
    return done


def attach_descriptions(db: AnalyticsDB, findings: list[Finding]) -> None:
    """Put each cluster's description into the findings about it (title and `details`)."""
    described = {
        r["id"]: r
        for r in db.query("SELECT id, description, in_common FROM clusters WHERE description IS NOT NULL")
    }
    for f in findings:
        cluster = described.get(f.details.get("cluster_id") or "")
        if cluster is not None:
            f.details["cluster_description"] = cluster["description"]
            f.details["in_common"] = cluster["in_common"]
            f.title = f"{f.title} ({cluster['in_common']})"
