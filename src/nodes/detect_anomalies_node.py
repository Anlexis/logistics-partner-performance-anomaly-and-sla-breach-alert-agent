"""AgentCore Platform v1.0"""

# Pipeline step 3 of 7: compare each measurement against the threshold it is
# contracted to and against the partner's own historical average.
#
# Two outcomes are distinguished and both are reported. A metric outside its
# contracted threshold is a breach. A metric still inside its threshold but
# drifting away from the partner's own baseline by more than the configured
# margin is a warning — the case an operator wants to see before it becomes a
# breach, and the reason the baseline is carried at all.
#
# A metric the caller did not measure is not assessed. It is neither a breach
# nor a pass, and inventing a value for it would put a number in the report that
# nobody measured.
#
# Reads:  performance_data, sla_parameters, baseline_data, monitoring_settings
# Writes: anomalies_detected, status, error_log

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# metric -> (threshold key, baseline key, direction, drift-margin key)
# direction "lower_is_worse" means the contract sets a floor the metric must
# stay above; "higher_is_worse" means it sets a ceiling.
_METRICS: Dict[str, Any] = {
    "delivery_rate_pct": (
        "delivery_rate_min_pct",
        "delivery_rate_avg_pct",
        "lower_is_worse",
        "drift_margin_pct",
    ),
    "transit_time_avg_days": (
        "transit_time_max_days",
        "transit_time_avg_days",
        "higher_is_worse",
        "drift_margin_days",
    ),
    "damage_rate_pct": (
        "damage_rate_max_pct",
        "damage_rate_avg_pct",
        "higher_is_worse",
        "drift_margin_pct",
    ),
    "document_error_rate_pct": (
        "document_error_rate_max_pct",
        "document_error_rate_avg_pct",
        "higher_is_worse",
        "drift_margin_pct",
    ),
}


def _assess(
    metric: str,
    current: float,
    threshold: Optional[float],
    baseline: Optional[float],
    direction: str,
    drift_margin: float,
) -> Optional[Dict[str, Any]]:
    """Return an anomaly record for one metric, or None when it is within norms."""
    if threshold is None:
        return None
    if direction == "lower_is_worse":
        breach = current < threshold
        drifting = baseline is not None and current < baseline - drift_margin
    else:
        breach = current > threshold
        drifting = baseline is not None and current > baseline + drift_margin
    if not breach and not drifting:
        return None
    return {
        "metric": metric,
        "current": current,
        "sla_threshold": threshold,
        "baseline": baseline,
        "delta": round(current - baseline, 3) if baseline is not None else None,
        "breach": breach,
    }


class DetectAnomaliesNode(FunctionNode):
    """Flag every measurement outside its threshold or drifting from baseline."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        partner_id = state.get("partner_id", "")
        performance = from_json(state.get("performance_data"), None)
        sla = from_json(state.get("sla_parameters"), None)
        baseline = from_json(state.get("baseline_data"), None)
        settings = from_json(state.get("monitoring_settings"), {}) or {}

        if not isinstance(performance, dict) or not isinstance(sla, dict) or not isinstance(baseline, dict):
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log
                + ["The measurements, the thresholds or the baseline did not reach " "the comparison step."],
            }

        margins = {
            "drift_margin_pct": float(settings.get("drift_margin_pct", 1.5)),
            "drift_margin_days": float(settings.get("drift_margin_days", 0.5)),
        }

        anomalies: List[Dict[str, Any]] = []
        for metric, (threshold_key, baseline_key, direction, margin_key) in _METRICS.items():
            current = performance.get(metric)
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                continue
            record = _assess(
                metric,
                float(current),
                sla.get(threshold_key),
                baseline.get(baseline_key),
                direction,
                margins[margin_key],
            )
            if record is not None:
                anomalies.append(record)

        breach_count = sum(1 for record in anomalies if record["breach"])

        logger.info(
            "partner=%s: %d anomaly record(s), %d of them breaches",
            partner_id,
            len(anomalies),
            breach_count,
        )

        emit_trace_event(
            "anomalies_detected",
            {
                "partner_id": partner_id,
                "anomaly_count": len(anomalies),
                "breach_count": breach_count,
                "assessed_metrics": sorted(metric for metric in _METRICS if metric in performance),
            },
            state,
        )

        return {
            "anomalies_detected": to_json(anomalies),
            "status": AgentStatus.SUCCESS,
        }
