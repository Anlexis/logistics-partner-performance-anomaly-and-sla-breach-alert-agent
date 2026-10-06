"""AgentCore Platform v1.0"""

# The output boundary: the last thing that runs before anything is released.
#
# Two properties of the framework's output resolution decide how this node has
# to be written, and both are easy to get wrong:
#
#   1. The released value is resolved as `formatted_output or result`, with no
#      check on status. So returning an error status while leaving `result`
#      populated ships the ungated text anyway, inside the error envelope. And
#      because the resolution is `or`, a FALSY `formatted_output` — "", {}, or
#      an absent key — hands the resolution straight back to `result`. Setting
#      it empty to mean "withheld" produces the exact leak it looks like it
#      prevents. The withheld notice is therefore a non-empty string.
#   2. LangGraph merges partial deltas. A key this node does not return keeps
#      whatever value outer state already held — so clearing a field means
#      returning it, emptied. Omitting it is not clearing it.
#
# So on a violation this node returns an error status, returns every
# output-bearing field emptied, and returns a truthy notice in its place.
#
# The scan runs on the text that is actually about to be released, assembled
# first and scanned second. Scanning an upstream field and then composing the
# released text from something else leaves whatever the composition adds
# unscanned — which is how caller-supplied values reach a reader through a
# branch the gate never looked at.
#
# Violation messages name the KIND of finding and nothing else. They never
# quote the matched text: the framework's own gate scans every value this node
# returns and raises when it finds a credential pattern, and the wrapper then
# discards the whole delta — including the clearing this node just did. Quoting
# a finding would turn a contained refusal back into a leak.
#
# Reads:  alert_report, breach_severity, partner_id, alert_required
# Writes: result, formatted_output, alert_report, status, error_log

import logging
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from framework.security.pii_detector import detect_pii

from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

#: Every state field that can carry released text or a payload. On a violation
#: each one is returned emptied. A new answer-bearing field on the state schema
#: must be added here; the accompanying test compares this tuple against the
#: schema so one cannot be added without the omission showing up.
OUTPUT_BEARING_FIELDS: Tuple[str, ...] = ("result", "alert_report")

#: Fields that describe the run rather than carry its answer. They may survive a
#: violation: a grade and a flag are drawn from closed sets, and the partner
#: identifier is locked to an inert alphabet at the contract boundary.
INERT_PROVENANCE_FIELDS: Tuple[str, ...] = (
    "breach_severity",
    "alert_required",
    "partner_id",
    "sla_parameters",
    "baseline_data",
    "performance_data",
    "anomalies_detected",
    "peer_comparison",
    "contractual_remedies",
    "caller_request",
    "monitoring_settings",
)

#: The notice that replaces the report when the gate refuses to release it.
#: Non-empty on purpose — see note 1 at the top of this module.
WITHHELD_NOTICE = (
    "Output withheld: the composed report did not pass the release check. "
    "No report content is included in this response."
)

# The PII finding types this gate treats as blocking. The framework's detector
# is the source of the patterns — a local pattern list would be narrower than
# the framework's and would drift from it.
#
# Two types are deliberately excluded. `name` matches any run of two or more
# title-case words, which its own documentation flags as high recall and low
# precision and recommends post-filtering for structured text; the remedy
# catalogue this agent releases is written in exactly that style ("Performance
# Improvement Notice", "Service Level Management"), so treating the type as
# blocking would refuse every report the agent produces. `name_jp` is anchored
# to labels this agent never emits. Excluding a heuristic is not the same as
# narrowing the pattern set: every structured identifier the framework detects
# still blocks.
_ADVISORY_PII_TYPES = frozenset({"name", "name_jp"})


def evaluate_release(text: str) -> Tuple[bool, Optional[str]]:
    """Decide whether ``text`` may be released.

    Returns ``(True, None)`` when it may, and ``(False, <finding kind>)`` when it
    may not. The second element is a fixed code naming the kind of finding — it
    is never any part of the text itself.

    Credential detection is delegated to the framework's own detector rather
    than to a local pattern list. A local list narrower than the framework's is
    a bypass in disguise: the value passes here, the framework's mandatory scan
    catches it on the way out of this node, and the exception it raises makes
    the wrapper discard everything this node returned — the clearing included.
    """
    credential_findings = detect_credentials(text)
    if credential_findings:
        return False, f"credential:{credential_findings[0]['type']}"

    pii_findings = [finding for finding in detect_pii(text) if finding["type"] not in _ADVISORY_PII_TYPES]
    if pii_findings:
        return False, f"personal_data:{pii_findings[0]['type']}"

    return True, None


def compose_release(
    alert_report: str,
    partner_id: str,
    severity: str,
    alert_required: bool,
) -> str:
    """Assemble the text this run would release.

    Composed before the scan runs, so every branch — the alert report and the
    two summary lines that stand in for it — goes through the same check.
    """
    if alert_report:
        return alert_report
    if severity == "none":
        return (
            f"Partner {partner_id}: no anomalies detected for the period. "
            "Every measured metric is within its contracted threshold and its "
            "historical baseline."
        )
    return f"Partner {partner_id}: assessment complete. " f"Severity: {severity}. Alert required: {alert_required}."


class SecurityGateOutputNode(FunctionNode):
    """The output boundary: scan what is about to be released, or withhold it."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        alert_report = state.get("alert_report") or ""
        severity = state.get("breach_severity") or "none"
        partner_id = state.get("partner_id") or ""
        alert_required = bool(state.get("alert_required"))

        candidate = compose_release(alert_report, partner_id, severity, alert_required)

        released, finding = evaluate_release(candidate)
        if not released:
            logger.warning("release refused: %s", finding)
            emit_trace_event(
                "output_release_refused",
                {"partner_id": partner_id, "finding": finding},
                state,
            )
            withheld: Dict[str, Any] = {field: "" for field in OUTPUT_BEARING_FIELDS}
            withheld["formatted_output"] = WITHHELD_NOTICE
            withheld["status"] = AgentStatus.ERROR
            withheld["error_log"] = error_log + [f"Output withheld by the release check ({finding})."]
            return withheld

        logger.info("released report for partner=%s (%d characters)", partner_id, len(candidate))

        emit_trace_event(
            "output_released",
            {
                "partner_id": partner_id,
                "severity": severity,
                "result_length": len(candidate),
            },
            state,
        )

        return {
            "result": candidate,
            "formatted_output": candidate,
            "status": AgentStatus.SUCCESS,
        }
