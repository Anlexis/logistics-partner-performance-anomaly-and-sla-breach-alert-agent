"""AgentCore Platform v1.0"""

# Request bridge across the outer/inner graph boundary.
#
# Why it exists: the framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. A single string
# crosses — neither the outer state nor the caller's structured invocation
# parameters are forwarded. So the validated request the pre_process node
# produces (the measurements for the period, the threshold and baseline
# overrides) and the operator's declared monitoring settings would never reach
# the monitoring pipeline on their own. The pipeline would run on its built-in
# defaults on every call, and the agent would report the same figures whatever
# the caller sent.
#
# The two sanctioned subclass hooks bridge it:
#
#   PartnerSLADomainWorkflowGraphNode.extract_input(state)
#                                               [runs BEFORE subgraph.invoke]
#       -> set_request(<validated request>, <resolved settings>)
#   PartnerSLADomainWorkflowGraph._extra_initial_state()
#                                               [runs INSIDE subgraph.invoke]
#       -> seeds both into the inner state
#
# What crosses is the VALIDATED request only — every value has already passed
# its bounded, inert shape check. The raw request body never travels.
#
# Packing the measurements into the text field instead is not viable: that field
# is masked and screened at every node boundary, so the numbers would arrive
# rewritten and the pipeline would compute on corrupted input. This channel is
# not masked.
#
# A ContextVar keeps the hand-off correct per thread and per task, so concurrent
# invocations inside one process cannot see each other's request.

from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple

_REQUEST: ContextVar[Optional[Tuple[Dict[str, Any], Dict[str, Any]]]] = ContextVar("partner_sla_request", default=None)


def set_request(request: Optional[Dict[str, Any]], settings: Optional[Dict[str, Any]]) -> None:
    """Stash the validated request and resolved settings for the imminent invoke."""
    _REQUEST.set((dict(request or {}), dict(settings or {})))


def get_request() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read (without consuming) the stashed request; empty mappings when unset."""
    stashed = _REQUEST.get()
    if stashed is None:
        return {}, {}
    request, settings = stashed
    return dict(request), dict(settings)
