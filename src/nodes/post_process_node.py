"""AgentCore Platform v1.0"""

# Reference implementation of a post_process node, kept alongside the sample
# graphs in src/examples/ as the shape a new node starts from. The agent's own
# backbone uses SecurityGateOutputNode instead — this file is not wired into it.

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event


class PostProcessNode(FunctionNode):
    """Format and finalise the output."""

    # Declared explicitly by design, never inherited implicitly. Raise it to a
    # verified-external or internal level when the node performs a privileged
    # operation.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        result = state.get("result", "")

        # The released length is recorded, never the released text: an audit
        # trail that quotes what it audits becomes a second copy of it.
        emit_trace_event(
            "post_process_complete",
            {"result_length": len(result) if isinstance(result, str) else 0},
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS,
        }
