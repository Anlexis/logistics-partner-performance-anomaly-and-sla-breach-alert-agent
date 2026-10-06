"""AgentCore Platform v1.0"""

# Pipeline step 5 of 7: set the partner's measurements against the network
# reference figures.
#
# The reference figures come from the configured monitoring settings rather than
# from constants in this file, so an operator running against a different
# network or a different reference period changes one YAML block instead of the
# code. Only metrics the caller actually measured are compared — a comparison
# against an unmeasured metric would be a number with nothing behind it.
#
# Reads:  performance_data, monitoring_settings, partner_id
# Writes: peer_comparison, status, error_log

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Metrics where a lower figure is the worse outcome. For everything else a
# higher figure is worse, so the same delta reads the opposite way.
_LOWER_IS_WORSE = frozenset({"delivery_rate_pct"})

_COMPARED = (
    "delivery_rate_pct",
    "transit_time_avg_days",
    "damage_rate_pct",
    "document_error_rate_pct",
)


def _assessment(metric: str, delta: float) -> str:
    """Describe where the partner sits relative to the reference figure."""
    if delta == 0:
        return "at_peer"
    if metric in _LOWER_IS_WORSE:
        return "below_peer" if delta < 0 else "above_peer"
    return "above_peer" if delta > 0 else "below_peer"


def _build_comparison(performance: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """Build the comparison structure from measured metrics only."""
    metric_deltas: Dict[str, Any] = {}
    for metric in _COMPARED:
        current = performance.get(metric)
        peer = reference.get(metric)
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            continue
        if not isinstance(peer, (int, float)) or isinstance(peer, bool):
            continue
        delta = round(float(current) - float(peer), 3)
        metric_deltas[metric] = {
            "current": float(current),
            "peer_avg": float(peer),
            "delta": delta,
            "assessment": _assessment(metric, delta),
        }
    return {
        "peer_count": reference.get("peer_count", 0),
        "benchmark_period": reference.get("benchmark_period", ""),
        "metric_deltas": metric_deltas,
    }


class GeneratePeerComparisonNode(FunctionNode):
    """Compare the partner's measurements against the network reference figures."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        partner_id = state.get("partner_id", "")
        performance = from_json(state.get("performance_data"), None)
        settings = from_json(state.get("monitoring_settings"), {}) or {}

        if not isinstance(performance, dict):
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log + ["The measurements did not reach the comparison step."],
            }

        reference = settings.get("peer_benchmark")
        if not isinstance(reference, dict):
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log
                + ["Monitoring settings are incomplete: the network reference " "figures must be configured."],
            }

        comparison = _build_comparison(performance, reference)
        compared: List[str] = sorted(comparison["metric_deltas"])

        logger.info(
            "partner=%s compared on %d metric(s) against %s reference partners",
            partner_id,
            len(compared),
            comparison.get("peer_count", 0),
        )

        emit_trace_event(
            "peer_comparison_generated",
            {
                "partner_id": partner_id,
                "peer_count": comparison.get("peer_count", 0),
                "metrics_compared": len(compared),
            },
            state,
        )

        return {
            "peer_comparison": to_json(comparison),
            "status": AgentStatus.SUCCESS,
        }
