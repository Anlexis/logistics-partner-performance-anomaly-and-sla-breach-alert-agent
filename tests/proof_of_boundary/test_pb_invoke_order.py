# Invoke-order boundary: a full agent.invoke() must execute the fixed backbone
# in order.
#
# The backbone is fixed by the framework and is never overridden by a template
# (add_edges() belongs to the framework):
#
#     START -> initialize -> pre_process -> main -> {route} -> post_process
#           -> finalize -> END
#
# The framework records every executed node in `node_history`, a state field
# whose reducer is operator.add, so entries accumulate in execution order. Each
# entry is the node class NAME, appended by the node wrapper.
#
# The `main` slot here is a GraphNode subclass that delegates to the monitoring
# pipeline. The pipeline runs with its own state, and its inner node_history is
# not merged back into the outer state, so the outer history carries exactly the
# five backbone class names and never the pipeline's own nodes.
#
# This test drives a real end-to-end invoke over a valid domain payload and
# asserts the surfaced node_history matches the backbone order. A SUCCESS
# terminal status is required: on any non-SUCCESS status the framework's routing
# short-circuits main -> finalize and the post_process slot never runs.
#
# Deterministic — no model call, no network. framework.* / src.* imports only.

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import PartnerSLABreachAlertAgent

# --- TEMPLATE-SPECIFIC -------------------------------------------------------
# The `main`-slot GraphNode class name for this template.
_MAIN_SLOT_NODE = "PartnerSLADomainWorkflowGraphNode"

# A valid monitoring request carrying no personal data. The measurements are
# outside the contracted thresholds, so the run drives the whole pipeline
# through to a composed alert report and a SUCCESS terminal status.
#
# deploy/invoke_payload.json carries the same request, so the payload this test
# proves the backbone on is the one a deployment is exercised with.
_VALID_PAYLOAD = json.dumps(
    {
        "partner_id": "LP001",
        "performance_data": {
            "delivery_rate_pct": 91.5,
            "transit_time_avg_days": 3.8,
            "damage_rate_pct": 0.9,
            "document_error_rate_pct": 3.2,
            "period": "2026-W26",
        },
    }
)
# --- END TEMPLATE-SPECIFIC ---------------------------------------------------

# The backbone execution order, by node class name as recorded in node_history.
# Four entries are framework-fixed and identical for every template of this
# shape; only _MAIN_SLOT_NODE is template-specific.
_EXPECTED_ORDER = [
    "InitializeNode",  # framework default (initialize slot)
    "ValidateInputNode",  # pre_process — the caller contract boundary
    _MAIN_SLOT_NODE,  # TEMPLATE-SPECIFIC (main slot GraphNode)
    "SecurityGateOutputNode",  # post_process — the output boundary
    "FinalizeNode",  # framework default (finalize slot)
]


def _run() -> dict:
    """Run a full end-to-end invocation and return the output dict.

    Uses a verified external caller context so the backbone trust gate admits
    the call — the pre_process node requires it — and the pipeline's own nodes,
    which require nothing beyond anonymous, admit it in turn.
    """
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return PartnerSLABreachAlertAgent().invoke(_VALID_PAYLOAD, ctx=ctx)


class TestInvokeOrderBoundary:
    """A full agent.invoke() executes the backbone in the fixed order."""

    def test_invoke_reaches_success(self):
        """The full run must terminate SUCCESS — otherwise the framework's
        routing short-circuits main -> finalize and the output boundary in the
        post_process slot never runs."""
        result = _run()
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"Expected SUCCESS, got {result.get('status')!r}. result={result!r}"

    def test_output_is_non_empty(self):
        """A successful run must surface a non-empty released output."""
        assert _run().get("output"), "invoke() surfaced an empty output"

    def test_node_history_is_populated(self):
        """node_history must be a non-empty list of node class-name strings."""
        history = _run().get("node_history")
        assert isinstance(history, list) and history, f"node_history must be a non-empty list, got {history!r}"
        assert all(isinstance(n, str) for n in history), f"node_history entries must be strings, got {history!r}"

    def test_backbone_slot_order(self):
        """The core boundary: pre_process runs before main, main before
        post_process — as a strict ordered subsequence of node_history."""
        history = _run().get("node_history", [])
        ordered_slots = ["ValidateInputNode", _MAIN_SLOT_NODE, "SecurityGateOutputNode"]
        for name in ordered_slots:
            assert name in history, f"Expected backbone slot {name!r} in node_history, got {history!r}"
        positions = [history.index(name) for name in ordered_slots]
        assert positions == sorted(positions), (
            f"Backbone slots executed out of order: {ordered_slots} at {positions}. " f"node_history={history!r}"
        )

    def test_full_backbone_sequence(self):
        """The complete backbone order:
        initialize -> pre_process -> main -> post_process -> finalize."""
        history = _run().get("node_history", [])
        assert history == _EXPECTED_ORDER, (
            "node_history does not match the backbone order.\n"
            f"  expected: {_EXPECTED_ORDER}\n"
            f"  actual:   {history}"
        )
