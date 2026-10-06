"""AgentCore Platform v1.0"""

# The caller request contract for partner SLA monitoring.
#
# Everything a caller can send arrives here first and leaves as a validated,
# bounded, inert structure — or does not leave at all. Three rules hold for
# every field without exception:
#
#   1. Numbers are finite and in range. A comparison against NaN is False in
#      both directions, so an unchecked NaN does not raise: it quietly makes
#      every threshold test pass and the agent reports "within contract" on a
#      measurement that means nothing. Infinity does the mirror image. Both
#      parse cleanly through float(), and both can arrive as raw JSON literals,
#      so neither is caught by "is it a number?".
#   2. Strings that reach the released report are identifiers, not prose. The
#      report is read by people acting on a contractual breach; a caller must
#      not be able to write sentences into it.
#   3. A rejected value is never echoed back. Errors name the field and say
#      what shape was expected. The value that failed stays out of the message,
#      the audit record and the log line.
#
# Unknown fields are refused rather than ignored. Ignoring an unrecognised name
# leaves it in the request as it travels on, which is how a value nobody
# validated ends up somewhere that trusts it.

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Mapping, Optional, Tuple

# ── Inert alphabets ──────────────────────────────────────────────────────────
# The partner identifier and the reporting period are the only caller strings
# that reach the released report, and both are locked to this alphabet: letters,
# digits, underscore and hyphen. No whitespace, no punctuation that could open a
# sentence or a markup tag. This is also the render alphabet the output boundary
# reasons about.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_PERIOD_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")

# A field NAME is caller data too. One that is short and inert can be repeated
# back in an error message; anything else is reported by position instead.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# ── Bounds ───────────────────────────────────────────────────────────────────
_PERCENT = (0.0, 100.0)
_DAYS = (0.0, 365.0)

#: Current-period measurements the caller may supply, with the range each is
#: accepted in. A rate is a percentage; a transit time is a number of days.
PERFORMANCE_FIELDS: Dict[str, Tuple[float, float]] = {
    "delivery_rate_pct": _PERCENT,
    "transit_time_avg_days": _DAYS,
    "damage_rate_pct": _PERCENT,
    "document_error_rate_pct": _PERCENT,
}

#: Contracted thresholds the caller may override for this request.
SLA_FIELDS: Dict[str, Tuple[float, float]] = {
    "delivery_rate_min_pct": _PERCENT,
    "transit_time_max_days": _DAYS,
    "damage_rate_max_pct": _PERCENT,
    "document_error_rate_max_pct": _PERCENT,
}

#: Historical baseline values the caller may override for this request.
BASELINE_FIELDS: Dict[str, Tuple[float, float]] = {
    "delivery_rate_avg_pct": _PERCENT,
    "transit_time_avg_days": _DAYS,
    "damage_rate_avg_pct": _PERCENT,
    "document_error_rate_avg_pct": _PERCENT,
}

#: Top-level names accepted in the request body and in the structured
#: parameters. Anything else is refused.
BLOCK_FIELDS = ("performance_data", "sla_overrides", "baseline_overrides")

#: Ceiling on the number of entries in any one block. The whitelists above are
#: shorter than this, so it only ever fires on a request that is trying to be
#: large rather than correct.
MAX_BLOCK_ENTRIES = 16

#: Ceiling on the request body, in characters.
MAX_INPUT_CHARS = 65_536

# ── Injection screen ─────────────────────────────────────────────────────────
# Chat-template control tokens are screened as a CLASS, not as a list of known
# strings. A model prompt is assembled from delimiters, and a caller who can
# write a delimiter into the request can open a turn that the assembled prompt
# reads as coming from somewhere else. The three families below cover the
# delimiters in general use; matching the family rather than the literal is what
# keeps a variant from walking straight through.
_CONTROL_TOKEN_RE = re.compile(r"<\|[^|>]{0,64}\|>|\[/?\s*INST\s*\]|<</?\s*SYS\s*>>", re.IGNORECASE)

# Template expressions. Written as a full expression — an opening delimiter with
# its closing one — so that a JSON object simply ending in two braces is not
# mistaken for one. Screening on a bare closing brace refuses every nested JSON
# payload there is, which is the documented request shape.
_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\$\{.*?\}", re.DOTALL)

# Executable or embedding markup.
_MARKUP_RE = re.compile(r"</?\s*(?:script|iframe|object|embed|style|svg)\b", re.IGNORECASE)

# Instruction-override phrasing. The verb alone is not enough: "ignore" and
# "override" are ordinary logistics words, and a screen that fires on the verb
# refuses real requests. The pattern requires the verb to govern an instruction
# noun, which is what makes the phrase an override attempt rather than prose.
_DIRECTIVE_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\s+"
    r"(?:all\s+|any\s+|the\s+|your\s+|these\s+|those\s+|previous\s+|prior\s+|earlier\s+|preceding\s+|above\s+)*"
    r"(?:instruction|instructions|rule|rules|prompt|prompts|direction|directions|"
    r"guideline|guidelines|constraint|constraints|policy|policies)\b",
    re.IGNORECASE,
)

# A role header addressed to a model, e.g. "system: you are now ...".
_ROLE_HEADER_RE = re.compile(
    r"(?:^|[\n\r])\s*(?:system|assistant|developer)\s*:\s*(?:you\b|now\b|ignore\b|disregard\b)",
    re.IGNORECASE,
)

_SCREENS = (
    ("control_token", _CONTROL_TOKEN_RE),
    ("template_expression", _TEMPLATE_RE),
    ("markup", _MARKUP_RE),
    ("instruction_override", _DIRECTIVE_RE),
    ("role_header", _ROLE_HEADER_RE),
)


class ContractError(ValueError):
    """A request that cannot be accepted, described without quoting it back.

    ``field`` is a path such as ``performance_data.delivery_rate_pct`` or, when
    the caller's own field name is not safe to repeat, a positional reference.
    ``reason`` is a fixed code from a closed set. Neither carries any part of
    the rejected value.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def field_reference(name: object, index: int, prefix: str = "") -> str:
    """Render a caller-supplied field name in a form that is safe to repeat."""
    root = f"{prefix}." if prefix else ""
    if isinstance(name, str) and _SAFE_NAME_RE.match(name):
        return f"{root}{name}"
    return f"{root}field #{index}"


def finite_in_range(value: Any, low: float, high: float) -> Optional[float]:
    """Return ``value`` as a float within ``[low, high]``, or ``None``.

    ``None`` means refuse. Booleans are refused before anything else because
    Python treats them as integers, so ``True`` would otherwise be accepted as
    the number 1. NaN and both infinities are refused after conversion: they
    survive ``float()`` intact, and every comparison involving NaN is False, so
    a threshold test against one passes silently in whichever direction the
    caller needs it to.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    if number < low or number > high:
        return None
    return number


def screen_text(text: str) -> Optional[str]:
    """Return the name of the first screen the text trips, or ``None``.

    The return value is a fixed code, never the matched text.
    """
    for label, pattern in _SCREENS:
        if pattern.search(text):
            return label
    return None


def screen_structure(value: Any, path: str = "") -> Optional[Tuple[str, str]]:
    """Screen every string in a parsed structure, keys included, depth first.

    Keys are screened as well as values because a name is caller-controlled and
    ends up in error messages and audit records. Running after the parse rather
    than before it is what closes the escape route through ``\\u`` sequences: the
    parser has already turned them back into the characters they encode, so an
    escaped delimiter is an ordinary delimiter by the time this runs.

    Returns ``(path, screen_name)`` for the first finding, or ``None``.
    """
    if isinstance(value, str):
        label = screen_text(value)
        return (path or "input", label) if label else None
    if isinstance(value, Mapping):
        for index, (key, item) in enumerate(value.items(), start=1):
            if isinstance(key, str):
                label = screen_text(key)
                if label:
                    return (field_reference(key, index, path), label)
            found = screen_structure(item, field_reference(key, index, path))
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = screen_structure(item, f"{path}[{index}]" if path else f"[{index}]")
            if found:
                return found
    return None


def _numeric_block(
    raw: Any,
    spec: Mapping[str, Tuple[float, float]],
    block: str,
) -> Dict[str, float]:
    """Validate one block of named numbers against its whitelist and bounds."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ContractError(block, "expected an object of named numeric values")
    if len(raw) > MAX_BLOCK_ENTRIES:
        raise ContractError(block, f"at most {MAX_BLOCK_ENTRIES} entries are accepted")

    validated: Dict[str, float] = {}
    for index, (name, value) in enumerate(raw.items(), start=1):
        reference = field_reference(name, index, block)
        if name == "period" and block == "performance_data":
            validated_period = _identifier(value, reference, _PERIOD_RE, 16)
            validated["period"] = validated_period  # type: ignore[assignment]
            continue
        if not isinstance(name, str) or name not in spec:
            raise ContractError(reference, "is not a recognised field for this block")
        low, high = spec[name]
        number = finite_in_range(value, low, high)
        if number is None:
            raise ContractError(
                reference,
                f"must be a finite number between {low:g} and {high:g}",
            )
        validated[name] = number
    return validated


def _identifier(value: Any, reference: str, pattern: "re.Pattern[str]", limit: int) -> str:
    """Validate one inert identifier."""
    if not isinstance(value, str):
        raise ContractError(reference, "must be a text identifier")
    candidate = value.strip()
    if not pattern.match(candidate):
        raise ContractError(
            reference,
            f"must be 1-{limit} characters of letters, digits, underscore or hyphen",
        )
    return candidate


# Keys the platform itself puts into input_context, not the caller. The Marketplace
# runner invokes every agent as
#     agent.invoke(message, ctx=ctx, input_context={"conversation_history": history})
# (agenticstar-agentcore, shared/bootstrap/marketplace_app.py), whatever the user typed.
# Refusing it as an unknown field refused every chat request before the question was
# read. Discarded, not validated: nothing in this pipeline reads prior turns, and
# screening a transcript would let one earlier message refuse every later one. Discarding
# adds no exposure — the backbone's first node has already copied the raw input_context
# into state before this contract runs.
PLATFORM_RESERVED_KEYS = frozenset({"conversation_history"})


def validate_request(
    raw_input: str,
    input_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate a whole request and return it in canonical form.

    The request body is either a bare partner identifier or a JSON object
    carrying the identifier and, optionally, the measurement and override
    blocks. The same blocks may instead arrive as structured invocation
    parameters, which take precedence when both are present — that channel is
    typed, so a caller that has structure to send should send it there.

    Raises :class:`ContractError` naming the field that failed.
    """
    if not isinstance(raw_input, str) or not raw_input.strip():
        raise ContractError("input", "is required and must not be empty")
    body = raw_input.strip()
    if len(body) > MAX_INPUT_CHARS:
        raise ContractError("input", f"must be at most {MAX_INPUT_CHARS} characters")

    label = screen_text(body)
    if label:
        raise ContractError("input", f"was refused by the {label} screen")

    parsed: Any
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if parsed is None:
        request: Dict[str, Any] = {"partner_id": body}
    elif isinstance(parsed, Mapping):
        found = screen_structure(parsed, "input")
        if found:
            raise ContractError(found[0], f"was refused by the {found[1]} screen")
        request = dict(parsed)
    else:
        raise ContractError("input", "must be a partner identifier or a JSON object")

    context = dict(input_context or {})
    for reserved in PLATFORM_RESERVED_KEYS:
        context.pop(reserved, None)
    if context:
        found = screen_structure(context, "input_context")
        if found:
            raise ContractError(found[0], f"was refused by the {found[1]} screen")

    for index, name in enumerate(request, start=1):
        if name != "partner_id" and name not in BLOCK_FIELDS:
            raise ContractError(field_reference(name, index, "input"), "is not a recognised field")
    for index, name in enumerate(context, start=1):
        if name not in BLOCK_FIELDS:
            raise ContractError(field_reference(name, index, "input_context"), "is not a recognised field")

    partner_id = _identifier(request.get("partner_id"), "partner_id", _IDENTIFIER_RE, 32)

    def block(name: str) -> Any:
        return context[name] if name in context else request.get(name)

    performance = _numeric_block(block("performance_data"), PERFORMANCE_FIELDS, "performance_data")
    sla_overrides = _numeric_block(block("sla_overrides"), SLA_FIELDS, "sla_overrides")
    baseline_overrides = _numeric_block(block("baseline_overrides"), BASELINE_FIELDS, "baseline_overrides")

    if performance and not any(name in performance for name in PERFORMANCE_FIELDS):
        raise ContractError("performance_data", "must carry at least one measured performance value")

    return {
        "partner_id": partner_id,
        "performance_data": performance,
        "sla_overrides": sla_overrides,
        "baseline_overrides": baseline_overrides,
    }
