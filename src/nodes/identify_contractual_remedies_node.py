"""AgentCore Platform v1.0"""

# Pipeline step 6 of 7: map the grade onto the remedies the contract opens up.
#
# Legal references:
#   - 物流総合効率化法 2024 (Act for Comprehensive Efficiency of Logistics)
#   - 改正下請法 2026 (Amended Subcontracting Act)
#   - 民法541条 (Civil Code Article 541 — termination for cause)
#
# Reads:  breach_severity, partner_id
# Writes: contractual_remedies, status, error_log
#
# Every value in the catalogue is fixed by the contract terms, not composed
# from caller text, so nothing a caller sends can reach the report through here.

import logging
from typing import Any, ClassVar, Dict, FrozenSet, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json

logger = logging.getLogger(__name__)

# Contractual remedy catalog — severity-indexed
_REMEDY_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "none": [],
    "warning": [
        {
            "remedy": "Performance Improvement Notice (PIN)",
            "legal_basis": "3PL Contract Section 8.2 — Service Level Management",
            "escalation_level": "account_manager",
            "timeline_days": 14,
        },
    ],
    "sla_breach": [
        {
            "remedy": "Formal SLA Breach Notice with 30-day remedy period",
            "legal_basis": "3PL Contract Section 12.1; 物流総合効率化法 2024 Art.15",
            "escalation_level": "director",
            "timeline_days": 30,
        },
        {
            "remedy": "Service credit claim per contracted penalty matrix",
            "legal_basis": "3PL Contract Appendix D — SLA Credit Schedule",
            "escalation_level": "legal",
            "timeline_days": 60,
        },
    ],
    "critical": [
        {
            "remedy": "Emergency escalation — CEO/COO bilateral performance review",
            "legal_basis": "3PL Master Agreement Section 5.4 — Critical Failure Protocol",
            "escalation_level": "executive",
            "timeline_days": 3,
        },
        {
            "remedy": "Termination for cause notice per 民法541条",
            "legal_basis": "民法541条; 改正下請法 2026 Compliance; 物流総合効率化法 2024 Art.15",
            "escalation_level": "legal",
            "timeline_days": 30,
        },
        {
            "remedy": "MLIT data-sharing report under 物流総合効率化法 2024 Art.15",
            "legal_basis": "物流総合効率化法 2024 第15条 — 荷主報告義務",
            "escalation_level": "compliance",
            "timeline_days": 10,
        },
    ],
}

_VALID_SEVERITIES: FrozenSet[str] = frozenset(_REMEDY_CATALOG.keys())


class IdentifyContractualRemediesNode(FunctionNode):
    """Map the grade onto the remedies the contract opens up."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        error_log: List[str] = list(state.get("error_log") or [])
        partner_id = state.get("partner_id", "")
        severity = state.get("breach_severity", "none")

        if severity not in _VALID_SEVERITIES:
            return {
                "status": AgentStatus.ERROR,
                "error_log": error_log
                + [
                    "The grade handed to the remedy step is not one this catalogue "
                    f"covers. Expected one of {sorted(_VALID_SEVERITIES)}."
                ],
            }

        remedies: List[Dict[str, Any]] = list(_REMEDY_CATALOG[severity])

        logger.info(
            "partner=%s graded %s opens %d remedy option(s)",
            partner_id,
            severity,
            len(remedies),
        )

        # Audit trail
        emit_trace_event(
            "contractual_remedies_identified",
            {
                "partner_id": partner_id,
                "severity": severity,
                "remedy_count": len(remedies),
            },
            state,
        )

        return {
            "contractual_remedies": to_json(remedies),
            "status": AgentStatus.SUCCESS,
        }
