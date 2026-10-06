"""AgentCore Platform v1.0"""

# Pipeline step 2 of 7: take the current period's measurements from the request.
#
# The measurements are whatever the caller sent, already bounded and
# range-checked at the contract boundary. There is no substitute set: a request
# that carries no measurements is reported as carrying none, and the pipeline
# produces the corresponding "nothing to assess" outcome rather than a graded
# breach computed from figures the caller never sent. A fabricated fallback here
# would make the report read the same whatever the input was — which is the one
# failure mode a monitoring agent cannot have.
#
# Reads:  caller_request, partner_id
# Writes: performance_data, status, error_log

import logging
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.caller_contract import PERFORMANCE_FIELDS
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)


class IngestPerformanceDataNode(FunctionNode):
    """Normalise the current period's measurements into pipeline state."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        partner_id = state.get("partner_id", "")
        request = from_json(state.get("caller_request"), {}) or {}

        supplied = request.get("performance_data")
        performance: Dict[str, Any] = dict(supplied) if isinstance(supplied, dict) else {}
        measured = [name for name in PERFORMANCE_FIELDS if name in performance]
        period = performance.get("period", "unspecified")

        logger.info(
            "ingested %d measurement(s) for partner=%s period=%s",
            len(measured),
            partner_id,
            period,
        )

        emit_trace_event(
            "performance_data_ingested",
            {
                "partner_id": partner_id,
                "metrics_count": len(measured),
                "period": period,
            },
            state,
        )

        return {
            "performance_data": to_json(performance),
            "status": AgentStatus.SUCCESS,
        }
