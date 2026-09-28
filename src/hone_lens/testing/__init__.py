"""Public test helpers: planted-issue generator, fakes for every port, contract checkers."""

from hone_lens.testing.contracts import (
    check_embedder,
    check_record_source,
    check_replayer,
    check_step_rerunner,
    check_text_client,
)
from hone_lens.testing.fakes import (
    FakeEmbedder,
    FakeRecordSource,
    FakeReplayer,
    FakeStepRerunner,
    FakeTextClient,
    FakeTextResult,
    ScriptedIO,
)
from hone_lens.testing.flow import FakeFlowRuns
from hone_lens.testing.synthetic import PLANTS, SyntheticRuns, synthetic_runs

__all__ = [
    "PLANTS",
    "FakeEmbedder",
    "FakeFlowRuns",
    "FakeRecordSource",
    "FakeReplayer",
    "FakeStepRerunner",
    "FakeTextClient",
    "FakeTextResult",
    "ScriptedIO",
    "SyntheticRuns",
    "check_embedder",
    "check_record_source",
    "check_replayer",
    "check_step_rerunner",
    "check_text_client",
    "synthetic_runs",
]
