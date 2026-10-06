# Test specification — Partner Performance Anomaly & SLA Breach Alert Agent

## Strategy

The suite is written around three questions, in order of how expensive the answer is to get wrong.

1. **Does a deployed instance serve a request at all?** The end-to-end tests drive the real HTTP
   entry point rather than a node or a graph. A suite that only exercises nodes cannot see a
   caller whose trust the adapter never establishes, or caller data that reaches the entry point
   and stops there — both look healthy from inside a unit test.
2. **Does the output depend on the input?** Every graded outcome is reached from measurements the
   test supplies, and a clean period and a breaching period are asserted to differ. A pipeline
   that falls back to a built-in sample produces the same report for every request, which is
   indistinguishable from working until somebody acts on it.
3. **Does each boundary hold, and would a test notice if it stopped holding?** The caller contract
   and the output boundary are exercised by calling `execute()` directly, with no framework
   wrapper in front, so a pass means this template refuses the input rather than that a framework
   gate happened to refuse it first.

Deterministic throughout — no model call, no network.

## Test files

| File | Scope |
|---|---|
| `tests/unit/test_agent.py` | The caller contract, each pipeline node, the output boundary |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | The framework's non-overridable gates |
| `tests/integration/test_invoke_end_to_end.py` | The real HTTP entry point, end to end |
| `tests/proof_of_boundary/test_import_isolation.py` | Import isolation |
| `tests/proof_of_boundary/test_state_safety.py` | State carries no credentials and no live objects |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | The backbone runs in its fixed order |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | Human-review interrupt handling (skipped: not enabled) |

## Framework compliance

| ID | Test | Expected |
|---|---|---|
| TC-01 | State is a flat TypedDict | No models or dataclasses among its fields |
| TC-02 | No platform-SDK imports under `src/` | 0 violations |
| TC-03 | No credential-shaped field names on State | 0 violations |
| TC-04 | Lifecycle events are not re-emitted by nodes | Absent from every `execute()` body |
| TC-06 | The input gate cannot be overridden | `TypeError` at class definition |
| TC-07 | The output gate cannot be overridden | `TypeError` at class definition |
| TC-08 | Every node declares its trust level explicitly | Declared in each class body |
| TC-09 | Every node emits a domain audit event | Reachable `emit_trace_event()` in each `execute()` |
| TC-10 | The class name matches the manifest entry point | Both name `PartnerSLABreachAlertAgent` |

## The caller contract

| ID | Case | Expected |
|---|---|---|
| CT-01 | NaN, ±Infinity, their string forms, booleans, out-of-range | Refused, per field, across all three blocks |
| CT-02 | `NaN` / `Infinity` as bare JSON literals | Refused — they never pass through a string |
| CT-03 | A bare partner identifier | Accepted |
| CT-04 | A nested JSON request | Accepted — the documented shape must not be read as injection |
| CT-05 | Structured parameters and body both present | The structured channel wins |
| CT-06 | Identifier with a space, punctuation, traversal, or over length | Refused |
| CT-07 | An unrecognised field, at the top level or inside a block | Refused, naming the field |
| CT-08 | A rejected value | Never appears in the message, the field path or the reason |
| CT-09 | A hostile field name | Reported by position, not repeated back |
| CT-10 | A body over the size ceiling | Refused |
| CT-11 | `<\|im_start\|>`, `<\|endoftext\|>`, `[INST]`, `<<SYS>>` | Refused as a class |
| CT-12 | Template expressions, markup, instruction-override phrasing | Refused |
| CT-13 | A control token nested in a value, in a key, or `\u`-escaped | Refused — the second pass runs after the parse |
| CT-14 | A control token in the structured parameters | Refused, naming that channel |
| CT-15 | Ordinary identifiers and a full documented request | Accepted — the fail-closed direction |

## Pipeline nodes

| ID | Node | Case | Expected |
|---|---|---|---|
| UT-V-01..06 | `ValidateInputNode` | Empty, valid, structured-only `validated_input`, refusal naming, partial delta, direct refusal | As described in each test |
| UT-L-01..04 | `LoadBaselineAndSLANode` | Declared settings used; caller override wins key by key; no identifier; incomplete settings | SUCCESS / SUCCESS / ERROR / ERROR |
| UT-I-01..02 | `IngestPerformanceDataNode` | Measurements carried through; none supplied | Carried / empty, never a fabricated set |
| UT-D-01..05 | `DetectAnomaliesNode` | Clean; threshold breaches; drift inside threshold; unmeasured metric; missing inputs | `[]` / breaches / warning / not assessed / ERROR |
| UT-C-01..05 | `ClassifyBreachSeverityNode` | Empty; drift only; one breach; delivery+transit; missing set | none / warning / sla_breach / critical / ERROR |
| UT-P-01..03 | `GeneratePeerComparisonNode` | Only measured metrics compared; figures come from settings; figures missing | As described |
| UT-R-01..07 | `IdentifyContractualRemediesNode` | Each grade's remedy count; the statutory citation; an unrecognised grade | 0/1/2/3 / present / ERROR |
| UT-A-01..03 | `AlertIfRequiredNode` | Clean period; each graded severity; report over the ceiling | No alert / composed / withheld |

## The output boundary

| ID | Case | Expected |
|---|---|---|
| OB-01 | A clean report | Released |
| OB-02 | Six credential shapes in the report | Withheld; the value appears nowhere in the returned delta |
| OB-03 | Email, phone and card shapes | Withheld; same |
| OB-04 | Ordinary title-case prose from the remedy catalogue | Still released — the fail-closed direction |
| OB-05 | A withheld release | Every output-bearing field is RETURNED and empty, not omitted |
| OB-06 | The withheld notice | Non-empty — a falsy one re-activates the framework's fallback |
| OB-07 | The violation message | Names the kind of finding; never the matched value |
| OB-08 | The no-alert branch and the summary branch | Both scanned — the released text is assembled first |
| OB-09 | The domain check | A module-level function, not a method on the node |
| OB-10 | The state-field inventory | Every domain field is classified output-bearing or inert |

## End to end, through the entry point

| ID | Case | Expected |
|---|---|---|
| E2E-01 | Health endpoint | Open |
| E2E-02 | No bearer token / a wrong one | 401, with the same body either way |
| E2E-03 | A verified request | SUCCESS, non-empty output, the output boundary in the node history |
| E2E-04 | A clean period vs a breaching period | Different reports |
| E2E-05 | The caller's own measurements | Appear in the report |
| E2E-06 | A request with no measurements | Reports that nothing was measured |
| E2E-07 | Each graded outcome | Reachable from measurements alone |
| E2E-08 | A threshold override sent as a structured parameter | Changes the outcome and appears in the report |
| E2E-09 | A declared runtime threshold | Changes the outcome |
| E2E-10 | Non-finite and out-of-range numbers | Refused, no output |
| E2E-11 | Injection forms | Refused, never echoed |
| E2E-12 | A credential in the request or in a structured parameter | 400 naming the field, value never echoed |
| E2E-13 | Ordinary domain data on the same channel | Still accepted |
| E2E-14 | An oversized structured parameter | 413 |

## Output containment

The fault is injected on the data path — the remedy catalogue the pipeline reads — never on the
gate. Patching the gate to force a violation tests the patch, not the agent.

| ID | Case | Expected |
|---|---|---|
| OC-01 | Clean-path control | The same request still produces its real report |
| OC-02 | A withheld release | No released text, no catalogue text anywhere in the envelope |
| OC-03 | The envelope's output | A non-empty notice |
| OC-04 | The node history | The output boundary ran, so the block happened there |
| OC-05 | The envelope | No traceback, no source path |

## Proof of boundary

| ID | Test | Expected |
|---|---|---|
| PB-1 | Import isolation | 0 violations |
| PB-2 / PB-5 | State safety | No credential-shaped fields, no prohibited types |
| PB-6 | Invoke order | The backbone runs initialize → pre_process → main → post_process → finalize |
| PB-7 | Human-review interrupt propagation | Skipped — human review is not enabled in `config/config.yaml` |

`deploy/invoke_payload.json` carries the same request as PB-6, so the payload a deployment is
exercised with is the one the backbone order is proven on.
