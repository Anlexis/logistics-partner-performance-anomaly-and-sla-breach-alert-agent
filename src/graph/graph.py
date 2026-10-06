"""AgentCore Platform v1.0"""

# The outer graph for the partner performance monitoring agent.
#
# Backbone (fixed by the framework — add_edges() is not overridden):
#   START -> initialize -> pre_process -> main -> {route} -> post_process
#         -> finalize -> END
#
# Slot assignments:
#   pre_process  = ValidateInputNode          (validates the caller contract)
#   main         = PartnerSLADomainWorkflowGraphNode (runs the monitoring pipeline)
#   post_process = SecurityGateOutputNode     (the output boundary)
#
# The class name matches config/agent.yaml `class:` and the import in
# src/api/server.py.

from pathlib import Path
from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.utils.config_loader import load_agent_config

from src.graph.context_bridge import set_request
from src.nodes.security_gate_output_node import SecurityGateOutputNode
from src.nodes.validate_input_node import ValidateInputNode
from src.schemas.state import State, from_json, to_json

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Values used when config/config.yaml is absent or carries no monitoring block —
# a bare Graph() in a minimal host still runs against a complete set rather than
# against whatever each node happens to default to on its own.
_FALLBACK_MONITORING: Dict[str, Any] = {
    "sla_thresholds": {
        "delivery_rate_min_pct": 95.0,
        "transit_time_max_days": 3.0,
        "damage_rate_max_pct": 0.5,
        "document_error_rate_max_pct": 2.0,
    },
    "baseline": {
        "delivery_rate_avg_pct": 97.2,
        "transit_time_avg_days": 2.1,
        "damage_rate_avg_pct": 0.3,
        "document_error_rate_avg_pct": 1.1,
    },
    "peer_benchmark": {
        "delivery_rate_pct": 96.1,
        "transit_time_avg_days": 2.4,
        "damage_rate_pct": 0.4,
        "document_error_rate_pct": 1.5,
        "peer_count": 47,
        "benchmark_period": "2025-Q4",
    },
    "drift_margin_pct": 1.5,
    "drift_margin_days": 0.5,
    "max_report_chars": 20000,
}


def runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml — the live runtime parameters.

    The agent registry loads this file and hands it to the graph constructor;
    the standalone HTTP entry point does the same, so the retry budget and the
    monitoring settings are live in both deployments rather than declared and
    ignored.

    Reading the static manifest (config/agent.yaml) here instead would return
    nothing: the manifest carries identity and compile-time requirements only,
    and a reader pointed at it degrades silently to defaults.
    """
    loaded = load_agent_config(_REPO_ROOT)
    return dict(loaded) if isinstance(loaded, dict) else {}


def monitoring_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the monitoring settings from a graph config, falling back to file.

    Declared keys win over the fallback one level down, so an operator may
    override a single threshold without restating the whole block.
    """
    source = config if config else runtime_config()
    declared = source.get("monitoring")
    if not isinstance(declared, dict) or not declared:
        return {key: dict(value) if isinstance(value, dict) else value for key, value in _FALLBACK_MONITORING.items()}
    resolved: Dict[str, Any] = {}
    for key, fallback in _FALLBACK_MONITORING.items():
        value = declared.get(key)
        if isinstance(fallback, dict):
            merged = dict(fallback)
            if isinstance(value, dict):
                merged.update(value)
            resolved[key] = merged
        else:
            resolved[key] = fallback if value is None else value
    return resolved


class PartnerSLADomainWorkflowGraphNode(GraphNode):
    """The `main` slot: wraps the monitoring pipeline.

    Contracts:
      _parent_config() - forward the resolved settings to the inner graph
      get_subgraph()   - instantiate the pipeline on every execute()
      extract_input()  - hand the partner identifier to the pipeline and bridge
                         the rest of the validated request
      merge_output()   - map the pipeline's output into the outer state delta
      error_strategy   - "propagate": re-raise inner errors rather than
                         continuing on a partial result

    The trust gate on a GraphNode is a deliberate no-op: the backbone's
    pre_process node has already enforced the external caller gate, and every
    node inside the pipeline declares ANONYMOUS so a verified caller's context
    passes through unchanged.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def _parent_config(self) -> Dict[str, Any]:
        """Return the config the inner graph is constructed with.

        Read from config/config.yaml, not from the manifest: the manifest has no
        runtime block, so a reader pointed at it would hand the pipeline an
        empty mapping and every declared value would go dead without a symptom.
        """
        return {"monitoring": monitoring_settings(runtime_config())}

    def get_subgraph(self) -> Any:
        """Instantiate and return the monitoring pipeline.

        Imported inside the method to keep module load free of a circular
        import. Nodes take no constructor arguments and read what they need from
        seeded state.
        """
        from src.graph.domain_workflow_graph import PartnerSLADomainWorkflowGraph

        return PartnerSLADomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the partner identifier, and bridge the rest of the request.

        The framework hands only a string to the inner graph, so the structured
        part of the request travels on the bridge instead — set here, one step
        before the inner invoke, and read by the inner graph's initial-state
        hook. Two things cross: the request the pre_process node has already
        validated, and the settings the outer graph resolved from its own
        config, so an operator-declared threshold reaches the pipeline rather
        than stopping at the boundary.
        """
        request = from_json(state.get("caller_request"), {})
        settings = from_json(state.get("monitoring_settings"), {})
        set_request(
            request if isinstance(request, dict) else {},
            settings if isinstance(settings, dict) else {},
        )
        return str(state.get("partner_id") or state.get("validated_input") or "")

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the pipeline's output into the outer state delta.

        `alert_report` carries the composed report and is what the output gate
        in post_process reads. On a non-success inner result the report is
        written empty rather than omitted: LangGraph merges partial deltas, so
        an omitted key leaves whatever was in outer state before, and the
        framework's own output resolution falls back to exactly such a leftover.
        """
        status = sub_result.get("status")
        report = sub_result.get("output")
        if status != AgentStatus.SUCCESS.value or not isinstance(report, str):
            return {
                "alert_report": "",
                "result": "",
                "status": status or AgentStatus.ERROR.value,
            }
        return {
            "alert_report": report,
            "status": status,
            "breach_severity": sub_result.get("breach_severity"),
            "alert_required": sub_result.get("alert_required"),
        }


class PartnerSLABreachAlertAgent(AgentBaseGraph):
    """Monitors a logistics partner's performance against its contracted service
    levels and its own historical baseline, and reports what a breach opens up.

    The monitoring pipeline is encapsulated in the `main` slot's GraphNode.

    Class name matches:
      config/agent.yaml  class: "src.graph.graph.PartnerSLABreachAlertAgent"
      src/api/server.py  from src.graph.graph import PartnerSLABreachAlertAgent
    """

    @property
    def name(self) -> str:
        # Matches config/agent.yaml `name:` — the registry key is built from it.
        return "Logistics Partner Performance Anomaly & SLA Breach Alert Agent"

    @property
    def state_schema(self) -> type:
        return State

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the live monitoring settings into the outer state.

        Both the pre_process node and the output gate need them — the first to
        resolve the thresholds a caller did not override, the second for the
        released-report ceiling. Nodes take no constructor arguments, so the
        settings travel through state, serialized like every other structured
        field.
        """
        return {"monitoring_settings": to_json(monitoring_settings(self.config))}

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode (required)
        self._nodes["pre_process"] = ValidateInputNode()
        self._nodes["main"] = PartnerSLADomainWorkflowGraphNode()
        self._nodes["post_process"] = SecurityGateOutputNode()

    # add_edges() is NOT overridden — backbone wiring is the framework's concern.


# The registry and the HTTP entry point import the class by name; this alias
# keeps the shorter import working for callers that use it.
Graph = PartnerSLABreachAlertAgent
