"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Read input_context via state.get("input_context", {}) — read-only
#  - Never import from an entry point or from another agent
#
# The pre_process slot: the caller contract boundary.
#
# Every caller value the pipeline will act on is validated here, in the node that
# owns the contract — not left to the framework's own gates. Those gates are a
# floor, not a guarantee: where one is absent or configured off, a payload that
# only they would have caught reaches the answer path and comes back as a
# success. So this node refuses on its own terms, and its tests call execute()
# directly, with no framework wrapper in front, to prove that it does.
#
# Reads:  user_input, input_context, monitoring_settings
# Writes: validated_input, partner_id, caller_request, status, error_log

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.caller_contract import ContractError, validate_request
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)


class ValidateInputNode(FunctionNode):
    """Validate the incoming monitoring request against the caller contract.

    Backbone slot: pre_process. Requires a verified external caller — this is
    where an outside request enters the agent.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        user_input = state.get("user_input", "")
        input_context = state.get("input_context") or {}

        try:
            request = validate_request(user_input, input_context)
        except ContractError as rejected:
            # The message names the field and the shape expected of it. The
            # value that failed is not repeated — not here, not in the audit
            # record, not in the log line.
            logger.warning("request refused at %s: %s", rejected.field, rejected.reason)
            emit_trace_event(
                "request_refused",
                {"field": rejected.field, "reason": rejected.reason},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log + [f"Request refused — {rejected.field} {rejected.reason}."],
            }

        partner_id = request["partner_id"]
        settings = from_json(state.get("monitoring_settings"), {})
        supplied = [name for name in ("performance_data", "sla_overrides", "baseline_overrides") if request.get(name)]

        logger.info(
            "request accepted for partner=%s with %d caller-supplied block(s)",
            partner_id,
            len(supplied),
        )

        emit_trace_event(
            "request_validated",
            {
                "partner_id": partner_id,
                "supplied_blocks": supplied,
                "settings_resolved": bool(settings),
            },
            state,
        )

        return {
            # validated_input carries the inert identifier only. The structured
            # part of the request travels on caller_request instead: the text
            # fields are masked and screened at every node boundary, so numbers
            # placed there would arrive rewritten.
            "validated_input": partner_id,
            "partner_id": partner_id,
            "caller_request": to_json(request),
            "status": AgentStatus.SUCCESS,
        }
