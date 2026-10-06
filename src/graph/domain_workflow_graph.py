"""AgentCore Platform v1.0"""

# The monitoring pipeline — the inner graph.
#
# Instantiated by PartnerSLADomainWorkflowGraphNode.get_subgraph(). Inherits
# BaseGraph, so the topology is entirely its own: there are no backbone slots to
# fill and no initialize/finalize step, both of which belong to the outer graph.
#
# Pipeline (linear — no conditional branching):
#   START
#     -> load_baseline_and_sla        thresholds in force + historical baseline
#     -> ingest_performance_data      measurements for the current period
#     -> detect_anomalies             deviations from threshold and baseline
#     -> classify_breach_severity     none | warning | sla_breach | critical
#     -> generate_peer_comparison     the partner against network reference figures
#     -> identify_contractual_remedies  severity mapped to the remedy catalogue
#     -> alert_if_required            compose the report when the grade warrants one
#     -> END
#
# Every node here declares ANONYMOUS: the external caller gate sits on the outer
# graph's pre_process slot, and a node inside the pipeline that demanded a
# higher level than a verified external caller can hold would be unreachable.

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph.context_bridge import get_request
from src.nodes.alert_if_required_node import AlertIfRequiredNode
from src.nodes.classify_breach_severity_node import ClassifyBreachSeverityNode
from src.nodes.detect_anomalies_node import DetectAnomaliesNode
from src.nodes.generate_peer_comparison_node import GeneratePeerComparisonNode
from src.nodes.identify_contractual_remedies_node import IdentifyContractualRemediesNode
from src.nodes.ingest_performance_data_node import IngestPerformanceDataNode
from src.nodes.load_baseline_and_sla_node import LoadBaselineAndSLANode
from src.schemas.state import State, to_json


class PartnerSLADomainWorkflowGraph(BaseGraph):
    """The seven-step partner performance monitoring pipeline."""

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "PartnerSLADomainWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """The pipeline runs on whatever settings it is given, complete or not.

        The outer graph resolves them against a full fallback set before they
        get here, so there is no key whose absence this could usefully refuse.
        """

    # ── Initial state ─────────────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the bridged request and the forwarded settings into inner state.

        The framework passes a single string across the graph boundary, so the
        validated measurements and overrides arrive on the request bridge
        instead. Reading them here — inside the inner invoke — is what puts them
        on the state the pipeline nodes actually see.
        """
        request, settings = get_request()
        resolved = settings or self.config.get("monitoring") or {}
        return {
            "caller_request": to_json(request),
            "monitoring_settings": to_json(resolved),
        }

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register the seven pipeline nodes.

        No super() call: BaseGraph declares this abstract and injects nothing.
        Nodes take no constructor arguments — settings travel on state.
        """
        self._nodes["load_baseline_and_sla"] = LoadBaselineAndSLANode()
        self._nodes["ingest_performance_data"] = IngestPerformanceDataNode()
        self._nodes["detect_anomalies"] = DetectAnomaliesNode()
        self._nodes["classify_breach_severity"] = ClassifyBreachSeverityNode()
        self._nodes["generate_peer_comparison"] = GeneratePeerComparisonNode()
        self._nodes["identify_contractual_remedies"] = IdentifyContractualRemediesNode()
        self._nodes["alert_if_required"] = AlertIfRequiredNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the pipeline. Linear — no conditional branching."""
        self._sg.add_edge(START, "load_baseline_and_sla")
        self._sg.add_edge("load_baseline_and_sla", "ingest_performance_data")
        self._sg.add_edge("ingest_performance_data", "detect_anomalies")
        self._sg.add_edge("detect_anomalies", "classify_breach_severity")
        self._sg.add_edge("classify_breach_severity", "generate_peer_comparison")
        self._sg.add_edge("generate_peer_comparison", "identify_contractual_remedies")
        self._sg.add_edge("identify_contractual_remedies", "alert_if_required")
        self._sg.add_edge("alert_if_required", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Required by the base class; never consulted on a linear topology.

        Annotated with this graph's own State rather than the framework's base
        state: LangGraph reads a path callable's annotation as its input schema
        and projects away every field outside it, so an annotation naming the
        wrong schema makes the fields the routing decision reads always absent.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "alert_if_required"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the result handed back to the enclosing GraphNode.

        Designed together with PartnerSLADomainWorkflowGraphNode.merge_output()
        so the two agree on field names. `output` carries only the composed
        report: it is never resolved from a general-purpose result field, which
        on a failed run would hand back whatever the last node happened to
        leave behind.
        """
        return {
            "output": state.get("alert_report"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "breach_severity": state.get("breach_severity"),
            "alert_required": state.get("alert_required"),
            "partner_id": state.get("partner_id"),
        }
