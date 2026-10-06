# Design — Partner Performance Anomaly & SLA Breach Alert Agent

## Position in the framework

| Field | Value |
|---|---|
| Agent class | `PartnerSLABreachAlertAgent` |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Inner pipeline base | `BaseGraph` — a fully custom linear topology with no backbone slots |
| Pattern | A backbone graph whose `main` slot delegates to a nested pipeline |
| Generation | Deterministic. No model is called anywhere; every number in the report is computed |

Three layers stay separate:

- **State** — a flat TypedDict. Structured fields are stored as JSON text.
- **Nodes** — `FunctionNode` subclasses. `execute()` returns only the fields it changes, and
  takes no constructor arguments; everything a node needs arrives on state.
- **Graph** — a backbone graph outside, a pipeline graph inside.

## Architecture

### Backbone

| Node | Role | Slot | Trust level |
|---|---|---|---|
| `InitializeNode` | Session and schema setup | `initialize` | _(framework default)_ |
| `ValidateInputNode` | The caller contract boundary | `pre_process` | `VERIFIED_EXTERNAL` |
| `PartnerSLADomainWorkflowGraphNode` | Wraps the monitoring pipeline | `main` | `ANONYMOUS` |
| `SecurityGateOutputNode` | The output boundary | `post_process` | `ANONYMOUS` |
| `FinalizeNode` | Response metadata and timing | `finalize` | _(framework default)_ |

Only the entry node requires a verified external caller. Every node behind it declares
`ANONYMOUS`: a node inside the pipeline that demanded a level higher than a verified external
caller can hold would be unreachable on every real request.

### The monitoring pipeline

| Step | Node | Responsibility |
|---|---|---|
| 1 | `LoadBaselineAndSLANode` | Resolve the thresholds in force and the historical baseline |
| 2 | `IngestPerformanceDataNode` | Take the current period's measurements from the request |
| 3 | `DetectAnomaliesNode` | Compare each measurement against threshold and baseline |
| 4 | `ClassifyBreachSeverityNode` | Grade: none / warning / sla_breach / critical |
| 5 | `GeneratePeerComparisonNode` | Set the partner against the network reference figures |
| 6 | `IdentifyContractualRemediesNode` | Map the grade onto the remedy catalogue |
| 7 | `AlertIfRequiredNode` | Compose the report when the grade warrants one |

### Data flow

```
START -> initialize -> pre_process (ValidateInputNode) -> main (PartnerSLADomainWorkflowGraphNode)
                                                            |
                        PartnerSLADomainWorkflowGraph:
                        load_baseline_and_sla -> ingest_performance_data
                        -> detect_anomalies -> classify_breach_severity
                        -> generate_peer_comparison -> identify_contractual_remedies
                        -> alert_if_required -> END (pipeline)
                                                            |
                     -> post_process (SecurityGateOutputNode) -> finalize -> END
```

### How the request crosses the graph boundary

The framework invokes a nested graph as `subgraph.invoke(user_input, session_id=..., ctx=...)`.
A single string crosses; neither the outer state nor the caller's structured invocation
parameters are forwarded. So the measurements and overrides the pre_process node validated would
stop at the boundary, and the pipeline would run on its built-in defaults for every request.

`src/graph/context_bridge.py` carries them across. `extract_input()` stashes the validated
request and the resolved settings one step before the inner invoke; the pipeline's
`_extra_initial_state()` reads them back inside it. Only validated values travel — the raw
request body never does. Packing them into the text field instead is not viable: that field is
masked and screened at every node boundary, so the numbers would arrive rewritten.

### State

| Field | Type | Stored as | Purpose |
|---|---|---|---|
| `validated_input` | `Optional[str]` | text | The inert partner identifier |
| `partner_id` | `Optional[str]` | text | The same identifier, for the nodes that report on it |
| `caller_request` | `Optional[str]` | JSON | The whole validated request contract |
| `monitoring_settings` | `Optional[str]` | JSON | The resolved runtime settings |
| `sla_parameters` | `Optional[str]` | JSON | Thresholds in force for this request |
| `baseline_data` | `Optional[str]` | JSON | Historical per-partner averages |
| `performance_data` | `Optional[str]` | JSON | Measurements for the current period |
| `anomalies_detected` | `Optional[str]` | JSON | Anomaly records |
| `breach_severity` | `Optional[str]` | text | none / warning / sla_breach / critical |
| `peer_comparison` | `Optional[str]` | JSON | Network comparison |
| `contractual_remedies` | `Optional[str]` | JSON | Remedies with their legal basis |
| `alert_required` | `Optional[bool]` | bool | Whether the grade warranted an alert |
| `alert_report` | `Optional[str]` | text | The composed report, before the output boundary |
| `result` | `Optional[str]` | text | The released report |

State constraints:

- Flat TypedDict only — primitives and JSON-serializable types.
- Structured values are stored as JSON text, never as live dicts or lists: checkpoints are
  serialized with msgpack and a live object does not survive that intact.
- No credentials of any kind. State is checkpointed.
- No models, dataclasses or arbitrary objects, for the same serialization reason.

## Runtime configuration

`config/agent.yaml` is the static manifest: identity, entry point, compile-time requirements.
It carries no runtime block, so a reader pointed at it gets nothing back and degrades silently to
defaults.

`config/config.yaml` holds the runtime parameters. The registry loads it and passes it as
`Graph(config=...)`; the standalone HTTP entry point does the same, so the values are live in
both deployments rather than in only one. It declares:

- `max_retry`, `timeout_s` — the backbone's retry budget and request timeout.
- `monitoring.sla_thresholds` — the contracted service levels used when the caller declares no
  override.
- `monitoring.baseline` — the rolling partner averages, used the same way.
- `monitoring.peer_benchmark` — the network reference figures and their reference period.
- `monitoring.drift_margin_pct` / `drift_margin_days` — how far a metric may move from its own
  baseline, while still inside its contracted threshold, before it is reported as a warning.
- `monitoring.max_report_chars` — the ceiling on the released report.

The outer graph resolves the block once and seeds it into state; the `main` slot forwards it to
the pipeline. A caller override is applied on top, key by key, so a caller may override a single
threshold without restating the rest.

## Security boundaries

### The caller contract (`src/schemas/caller_contract.py`)

Every value a caller can send is validated in the node that owns the contract, not left to the
framework's own gates. Those gates are a floor: where one is absent or configured off, a payload
that only they would have caught reaches the answer path and comes back as a success.

- **Numbers.** Every caller number goes through one bounded parser. It refuses booleans (Python
  treats them as integers), non-numerics, values outside the field's range, and NaN and both
  infinities — which survive `float()`, arrive as bare JSON literals, and make every comparison
  False, so an unchecked one passes any threshold test silently in whichever direction the caller
  needs.
- **Strings that reach the report.** The partner identifier and the reporting period are the only
  ones, and both are locked to `[A-Za-z0-9_-]` with a length bound. No whitespace, no punctuation
  that could open a sentence or a tag.
- **Unknown fields are refused, not ignored.** Ignoring an unrecognised name leaves it in the
  request as it travels on.
- **Injection.** Chat-template control tokens are screened as a class — `<|...|>`, `[INST]`,
  `<<SYS>>` — alongside template expressions, executable markup, and instruction-override
  phrasing. Screening runs on the raw body and again on the parsed structure, keys included: the
  parser has already decoded `\u` escapes by then, so an escaped delimiter is an ordinary
  delimiter when the second pass sees it. The template-expression pattern matches a full
  expression rather than a bare closing brace — screening on `}}` alone refuses every nested JSON
  payload there is, which is the documented request shape.
- **Errors name the field, never the value.** A rejected value never appears in a message, an
  audit record or a log line.

### The entry point (`src/api/server.py`)

Nothing upstream establishes caller trust in a standalone deployment, and the entry node requires
a verified caller. The adapter is therefore the auth boundary: with `INVOKE_AUTH_TOKEN` set, a
caller presenting it as a bearer token runs as verified external; trust established by an upstream
component is never demoted. With no token configured and no upstream trust, the adapter says so
with a 503 rather than letting every request fail at the trust gate with an envelope that explains
nothing.

The adapter also screens the request and the structured parameters for credential shapes before
invoking, using the framework's own detector on the same assembled object the framework's scan
sees — so what is refused here and what the framework blocks are one set by construction. Without
it, a credential-shaped string in the structured parameters makes the *first* node of the graph
fail with an opaque error, because that node copies them verbatim into its own result and the
framework's mandatory output scan raises. The request cannot succeed either way; refusing it here
turns an unreadable failure into an actionable one that names the field.

### The output boundary (`src/nodes/security_gate_output_node.py`)

The framework resolves the released value as `formatted_output or result`, with no check on
status. Two consequences shape this node:

- Returning an error status while leaving `result` populated ships the ungated text anyway,
  inside the error envelope.
- Because the resolution is `or`, a falsy `formatted_output` hands the resolution straight back to
  `result`. Setting it empty to mean "withheld" produces the exact leak it looks like it prevents.

So on a violation the node returns an error status, returns every output-bearing field emptied —
returned, not omitted, because partial deltas are merged and an omitted key keeps its old value —
and returns a non-empty notice in place of the report.

The scan runs on the text that is actually about to be released, assembled first and scanned
second. Scanning an upstream field and composing the released text from something else leaves
whatever the composition adds unscanned.

Credential detection is delegated to the framework's own detector. A local pattern list narrower
than the framework's is a bypass in disguise: the value passes the local check, the framework's
mandatory scan catches it on the way out, and the exception discards the node's whole delta —
including the clearing. Personal-data detection uses the framework detector too, minus its
title-case name heuristic: that heuristic matches any run of two or more title-case words, which
is how the remedy catalogue is written, so treating it as blocking would refuse every report the
agent produces.

Violation messages name the kind of finding and nothing else, for the same reason the delta must
stay clean.

### Numeric rounding grid

Not applicable. The report renders measured rates, transit times and their deltas — no monetary
aggregates — so there is no rounding grid to enforce and no currency-marker grammar in the
pipeline. The output invariant this agent does enforce is the one above: nothing is released that
carries a credential shape or structured personal data, on any branch.

### Audit trail

Every node emits a domain event through `emit_trace_event()` on a reachable path. Lifecycle
events are emitted by the framework's node wrapper and are not duplicated here. Events carry
counts, field names and fixed reason codes — never released text and never a rejected value.

### Import isolation

Framework imports go through `framework.*` and `shared.*` only. The pipeline is imported inside
`get_subgraph()` rather than at module load, which keeps the two graph modules free of a circular
import.

## Design decisions

| Decision | Chosen | Rationale |
|---|---|---|
| Base class | `AgentBaseGraph` | A multi-step pipeline behind a fixed backbone |
| Pipeline base | `BaseGraph` | A linear topology with no backbone slots to fill |
| Structured request channel | A context bridge | The framework forwards only a string to a nested graph |
| Runtime settings | `config/config.yaml` | The manifest carries no runtime block; a reader pointed at it degrades silently |
| State serialization | JSON text for structured fields | Checkpoints are msgpack; live objects do not survive |
| Grades | Four, with delivery+transit as the critical pair | A shortfall in both is network-wide rather than lane-specific |
| Reference figures and thresholds | Declared in config, not in code | An operator changes one block instead of the source |
| Absent measurements | Reported as absent | A substitute set makes the report read the same whatever the caller sent |
