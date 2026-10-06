"""AgentCore Platform v1.0"""

# The service layer: where the agent looks up the terms it measures against.
#
# Nodes orchestrate; this module answers "what is this partner contracted to,
# and what has it historically achieved?". Keeping the two apart is what makes
# the answer substitutable: a deployment that holds its service levels in a
# contract system and its baselines in a telemetry store replaces this class and
# leaves the pipeline alone.
#
# The shipped implementation resolves both from the operator's declared settings
# and the caller's per-request overrides. That is a real answer, not a
# placeholder — it is how a deployment with no contract system of its own runs —
# and it is the seam a deployment with one replaces.
#
# No credentials live here. A replacement that calls an external system takes
# them from the invocation context's secret store, never from state and never
# from module-level constants.

from typing import Any, Dict, Mapping


class PartnerSLAService:
    """Resolves the service levels and the historical baseline for one request.

    Both resolutions follow the same rule: start from what the operator
    declared, then apply the caller's overrides one key at a time. Applying them
    key by key rather than wholesale is what lets a caller override a single
    threshold for one request without restating the other three — replacing the
    block would silently drop the ones it did not mention.

    Only keys the declared set already contains are applied. A caller cannot
    introduce a threshold the deployment never declared, so the shape of what
    the pipeline compares against is fixed by the operator, not by the request.
    """

    def __init__(self, settings: Mapping[str, Any]) -> None:
        self._settings = dict(settings or {})

    @property
    def is_configured(self) -> bool:
        """True when both the service levels and the baseline are declared.

        Neither has a sensible default at this layer: comparing measurements
        against thresholds nobody set would produce a grade with nothing behind
        it. The caller of this service turns a False here into a refusal.
        """
        return isinstance(self._settings.get("sla_thresholds"), dict) and isinstance(
            self._settings.get("baseline"), dict
        )

    def service_levels(self, overrides: Mapping[str, Any] | None = None) -> Dict[str, float]:
        """The contracted thresholds in force for this request."""
        return self._resolve("sla_thresholds", overrides)

    def baseline(self, overrides: Mapping[str, Any] | None = None) -> Dict[str, float]:
        """The partner's historical averages, as they apply to this request."""
        return self._resolve("baseline", overrides)

    def _resolve(self, block: str, overrides: Mapping[str, Any] | None) -> Dict[str, float]:
        declared = self._settings.get(block)
        if not isinstance(declared, dict):
            return {}
        resolved = {key: float(value) for key, value in declared.items()}
        for key, value in (overrides or {}).items():
            if key in resolved:
                resolved[key] = float(value)
        return resolved
