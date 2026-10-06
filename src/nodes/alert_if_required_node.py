"""AgentCore Platform v1.0"""

# Pipeline step 7 of 7: compose the alert when the grade warrants one.
#
# A report is composed for warning, sla_breach and critical. A clean period is
# reported as clean, without an alert — an alert that fires on every period is
# one nobody reads.
#
# Every line of the report is assembled from values this pipeline computed or
# from the fixed remedy catalogue. The only caller strings that appear are the
# partner identifier and the reporting period, both locked at the contract
# boundary to an alphabet with no whitespace and no punctuation, so a caller
# cannot write a sentence — or a heading, or a directive — into a document
# somebody else is going to act on.
#
# Reads:  breach_severity, anomalies_detected, peer_comparison,
#         contractual_remedies, performance_data, monitoring_settings, partner_id
# Writes: alert_required, alert_report, status, error_log

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json

logger = logging.getLogger(__name__)

_SEVERITY_LABELS: Dict[str, str] = {
    "none": "No Alert",
    "warning": "Warning",
    "sla_breach": "SLA Breach",
    "critical": "Critical Breach",
}

_ALERT_SEVERITIES = frozenset({"warning", "sla_breach", "critical"})

# Used when a grade arrives that the label table does not cover. The grade comes
# from a closed set one step upstream, so this is unreachable in practice — but
# it is a label, not the value itself, so an unexpected grade cannot render its
# own text into the report.
_UNKNOWN_LABEL = "Unclassified"

_DEFAULT_MAX_REPORT_CHARS = 20000


def _format_report(
    partner_id: str,
    period: str,
    severity: str,
    anomalies: List[Dict[str, Any]],
    peer_comparison: Dict[str, Any],
    remedies: List[Dict[str, Any]],
) -> str:
    """Compose the alert report."""
    lines = [
        "=== Partner SLA Alert ===",
        f"Partner: {partner_id}",
        f"Period: {period}",
        f"Severity: {_SEVERITY_LABELS.get(severity, _UNKNOWN_LABEL)}",
        "",
    ]

    if anomalies:
        lines.append("--- Anomalies Detected ---")
        for record in anomalies:
            tag = "[BREACH]" if record.get("breach") else "[WARNING]"
            lines.append(
                f"  {tag} {record.get('metric')}: current={record.get('current')}, "
                f"contracted threshold={record.get('sla_threshold')}, "
                f"baseline={record.get('baseline')}, delta={record.get('delta')}"
            )
        lines.append("")

    metric_deltas = peer_comparison.get("metric_deltas", {})
    if metric_deltas:
        lines.append(
            f"--- Network Comparison (n={peer_comparison.get('peer_count', 0)}, "
            f"reference period: {peer_comparison.get('benchmark_period', '')}) ---"
        )
        for metric, info in metric_deltas.items():
            lines.append(
                f"  {metric}: {info.get('current')} vs network average "
                f"{info.get('peer_avg')} ({info.get('assessment')}, "
                f"delta={info.get('delta')})"
            )
        lines.append("")

    if remedies:
        lines.append("--- Contractual Remedies Available ---")
        for index, remedy in enumerate(remedies, 1):
            lines.append(f"  {index}. {remedy.get('remedy')}")
            lines.append(f"     Legal basis: {remedy.get('legal_basis')}")
            lines.append(
                f"     Escalation: {remedy.get('escalation_level')} | " f"Timeline: {remedy.get('timeline_days')} days"
            )
        lines.append("")

    return "\n".join(lines)


class AlertIfRequiredNode(FunctionNode):
    """Compose the alert report when the grade warrants one."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        severity = state.get("breach_severity", "none")
        partner_id = state.get("partner_id", "")
        settings = from_json(state.get("monitoring_settings"), {}) or {}
        performance = from_json(state.get("performance_data"), {}) or {}

        alert_required = severity in _ALERT_SEVERITIES
        alert_report = ""

        if alert_required:
            anomalies = from_json(state.get("anomalies_detected"), []) or []
            peer_comparison = from_json(state.get("peer_comparison"), {}) or {}
            remedies = from_json(state.get("contractual_remedies"), []) or []
            period = performance.get("period", "unspecified")
            alert_report = _format_report(
                partner_id,
                period if isinstance(period, str) else "unspecified",
                severity,
                anomalies if isinstance(anomalies, list) else [],
                peer_comparison if isinstance(peer_comparison, dict) else {},
                remedies if isinstance(remedies, list) else [],
            )

            ceiling = settings.get("max_report_chars", _DEFAULT_MAX_REPORT_CHARS)
            limit = (
                int(ceiling)
                if isinstance(ceiling, int) and not isinstance(ceiling, bool)
                else _DEFAULT_MAX_REPORT_CHARS
            )
            if len(alert_report) > limit:
                # A report longer than a monitoring summary can be means the
                # pipeline is emitting something other than what it computed.
                # Refusing beats truncating: a report cut mid-line reads as
                # complete and is acted on as complete.
                emit_trace_event(
                    "alert_report_over_ceiling",
                    {"partner_id": partner_id, "ceiling_chars": limit},
                    state,
                )
                return {
                    "alert_required": True,
                    "alert_report": "",
                    "status": AgentStatus.ERROR,
                    "error_log": error_log
                    + [
                        f"The composed report exceeds the configured ceiling of "
                        f"{limit} characters and was not released."
                    ],
                }

        logger.info(
            "partner=%s graded %s, alert_required=%s",
            partner_id,
            severity,
            alert_required,
        )

        emit_trace_event(
            "alert_evaluation_complete",
            {
                "partner_id": partner_id,
                "severity": severity,
                "alert_required": alert_required,
                "report_chars": len(alert_report),
            },
            state,
        )

        return {
            "alert_required": alert_required,
            "alert_report": alert_report,
            "status": AgentStatus.SUCCESS,
        }
