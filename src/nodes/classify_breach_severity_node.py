"""AgentCore Platform v1.0"""

# Pipeline step 4 of 7: grade the anomaly set.
#
#   none        nothing was flagged
#   warning     something drifted, nothing crossed a contracted threshold
#   sla_breach  at least one metric is outside its contracted threshold
#   critical    on-time delivery and transit time are both outside theirs
#
# The critical grade is not "several breaches": it is that specific pair. When
# deliveries are both late and slower than contracted, the shortfall is in the
# network rather than in one lane, and the remedies that open up are the ones
# that assume the partner cannot recover the period on its own.
#
# Reads:  anomalies_detected, partner_id
# Writes: breach_severity, status, error_log

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json

logger = logging.getLogger(__name__)

# Breached together, these two mean the shortfall is network-wide.
_CRITICAL_COMBINATION = frozenset({"delivery_rate_pct", "transit_time_avg_days"})


def classify_severity(anomalies: List[Dict[str, Any]]) -> str:
    """Grade an anomaly set."""
    if not anomalies:
        return "none"
    breached = {record.get("metric") for record in anomalies if record.get("breach")}
    if not breached:
        return "warning"
    if _CRITICAL_COMBINATION.issubset(breached):
        return "critical"
    return "sla_breach"


class ClassifyBreachSeverityNode(FunctionNode):
    """Grade the anomaly set as none, warning, sla_breach or critical."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        partner_id = state.get("partner_id", "")
        anomalies = from_json(state.get("anomalies_detected"), None)

        if not isinstance(anomalies, list):
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log + ["The anomaly set did not reach the grading step."],
            }

        severity = classify_severity(anomalies)

        logger.info(
            "partner=%s graded %s from %d anomaly record(s)",
            partner_id,
            severity,
            len(anomalies),
        )

        emit_trace_event(
            "breach_severity_classified",
            {
                "partner_id": partner_id,
                "severity": severity,
                "anomaly_count": len(anomalies),
            },
            state,
        )

        return {
            "breach_severity": severity,
            "status": AgentStatus.SUCCESS,
        }
