"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic model. Checkpoints are
# serialized with msgpack, and a model instance does not survive that intact.
# Extend AgentState with agent-specific fields only, and never put credentials
# or secrets on it.
#
# Serialization rule: dict and list domain fields are stored as Optional[str]
# holding JSON. Each node deserializes on read and serializes on write, using
# the two helpers at the bottom of this module.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for partner performance monitoring and SLA breach alerting.

    Layered on top of AgentState, which already provides user_input, status,
    session_id, node_history, error_log and the rest of the backbone fields.

    Every structured field below holds JSON text rather than a live object.
    """

    # ValidateInputNode (pre_process) output
    partner_id: Optional[str]  # inert partner identifier taken from the request
    caller_request: Optional[str]  # JSON: the whole validated request contract
    monitoring_settings: Optional[str]  # JSON: the resolved runtime settings

    # LoadBaselineAndSLANode output
    sla_parameters: Optional[str]  # JSON: thresholds in force for this request
    baseline_data: Optional[str]  # JSON: historical per-partner averages

    # IngestPerformanceDataNode output
    performance_data: Optional[str]  # JSON: measurements for the current period

    # DetectAnomaliesNode output
    anomalies_detected: Optional[str]  # JSON: list of anomaly records

    # ClassifyBreachSeverityNode output
    breach_severity: Optional[str]  # "none" | "warning" | "sla_breach" | "critical"

    # GeneratePeerComparisonNode output
    peer_comparison: Optional[str]  # JSON: {peer_count, benchmark_period, metric_deltas}

    # IdentifyContractualRemediesNode output
    contractual_remedies: Optional[str]  # JSON: list of remedies with their legal basis

    # AlertIfRequiredNode output
    alert_required: Optional[bool]
    alert_report: Optional[str]  # the composed alert text, before the output gate

    # SecurityGateOutputNode (post_process) output — released only after the gate
    result: Optional[str]


def to_json(value: Any) -> str:
    """Serialize a structured value for storage on state."""
    return json.dumps(value, ensure_ascii=False)


def from_json(raw: Any, default: Any = None) -> Any:
    """Read a structured value back off state, returning ``default`` when absent.

    A malformed value returns the default rather than raising: state fields are
    written by this template's own nodes, so a parse failure means the pipeline
    is in a state it should degrade out of, not one it should crash on.
    """
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
