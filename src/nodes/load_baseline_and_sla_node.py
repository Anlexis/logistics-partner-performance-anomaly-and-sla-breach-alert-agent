"""AgentCore Platform v1.0"""

# Pipeline step 1 of 7: establish the thresholds and the baseline this request
# will be measured against.
#
# The lookup itself lives in src/services/service.py. This node decides what the
# pipeline does with the answer — refuse when the deployment has declared no
# terms to measure against, record what the caller overrode, and put the result
# on state — while the service decides what the answer is. A deployment holding
# its service levels in a contract system replaces the service and leaves this
# node untouched.
#
# The caller's overrides arrive already bounded and range-checked from the
# contract boundary, so nothing re-validates them here.
#
# Reads:  caller_request, monitoring_settings, user_input
# Writes: partner_id, sla_parameters, baseline_data, status, error_log

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.service import PartnerSLAService

logger = logging.getLogger(__name__)


class LoadBaselineAndSLANode(FunctionNode):
    """Resolve the service levels in force and the partner's historical baseline."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        request = from_json(state.get("caller_request"), {}) or {}
        settings = from_json(state.get("monitoring_settings"), {}) or {}

        # Inside the pipeline the incoming text is the partner identifier the
        # outer graph handed across; the bridged request carries it too.
        partner_id = str(request.get("partner_id") or state.get("partner_id") or state.get("user_input") or "").strip()
        if not partner_id:
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log + ["No partner identifier reached the monitoring pipeline."],
            }

        service = PartnerSLAService(settings)
        if not service.is_configured:
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log
                + [
                    "Monitoring settings are incomplete: the service-level thresholds "
                    "and the historical baseline must both be configured."
                ],
            }

        sla_overrides = request.get("sla_overrides") or {}
        baseline_overrides = request.get("baseline_overrides") or {}
        sla = service.service_levels(sla_overrides)
        baseline = service.baseline(baseline_overrides)

        logger.info("resolved thresholds and baseline for partner=%s", partner_id)

        emit_trace_event(
            "baseline_and_sla_resolved",
            {
                "partner_id": partner_id,
                "sla_key_count": len(sla),
                "sla_overridden": sorted(sla_overrides),
                "baseline_overridden": sorted(baseline_overrides),
            },
            state,
        )

        return {
            "partner_id": partner_id,
            "sla_parameters": to_json(sla),
            "baseline_data": to_json(baseline),
            "status": AgentStatus.SUCCESS,
        }
