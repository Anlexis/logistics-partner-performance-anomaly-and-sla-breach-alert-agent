"""AgentCore Platform v1.0"""

# Reference implementation of a pre_process node, kept alongside the sample
# graphs in src/examples/ as the shape a new node starts from. The agent's own
# backbone uses ValidateInputNode instead — this file is not wired into it.
#
# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Read input_context via state.get("input_context", {}) — read-only
#  - Never import from an entry point or from another agent

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event


class PreProcessNode(FunctionNode):
    """Validate and enrich incoming input before the main step."""

    # Declared explicitly by design, never inherited implicitly. Raise it to a
    # verified-external or internal level when the node performs a privileged
    # operation.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only

        if not user_input or not user_input.strip():
            emit_trace_event(
                "pre_process_rejected",
                {"reason": "empty_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["The request carried no input."],
            }

        # An audit event on every path a node can take, not only the happy one:
        # a refusal that leaves no trace is indistinguishable from a request
        # that was never made.
        emit_trace_event(
            "pre_process_complete",
            {"input_length": len(user_input), "channel": input_context.get("channel", "unknown")},
            state,
        )

        return {
            "validated_input": user_input.strip(),
            "enriched_context": {
                "source": "Logistics Partner Performance Anomaly & SLA Breach Alert Agent",
                "channel": input_context.get("channel", "unknown"),
            },
            "status": AgentStatus.SUCCESS,
        }
