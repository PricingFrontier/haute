"""Provider-neutral streaming adapters for the assistant agent loop.

The optional provider SDKs are deliberately imported only when an adapter has
to construct its production client.  The rest of Haute can therefore import
the assistant package without requiring either SDK, while tests and callers can
inject a client at the adapter seam.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Literal, Protocol, TypeAlias

from haute._env import int_env
from haute._logging import get_logger
from haute.assistant._catalog import MUTATING_OPERATION_IDS, OPERATION_IDS
from haute.assistant._config import (
    DEFAULT_TURN_TIMEOUT,
    TURN_TIMEOUT_ENV,
    AssistantConfig,
    unsupported_anthropic_model,
)
from haute.errors import ConfigError, HauteError

logger = get_logger(component="assistant.providers")


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A provider text fragment."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A complete provider tool call with parsed JSON arguments."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Token usage for one provider round-trip."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class TurnStop:
    """The provider's terminal reason for one streamed round-trip."""

    reason: Literal["end", "tool_use"]
    usage: ProviderUsage


@dataclass(frozen=True, slots=True)
class ThinkingStarted:
    """The model opened a thinking block. It carries none of the thinking."""


@dataclass(frozen=True, slots=True)
class ReplayContent:
    """One round's assistant content as its provider must receive it back.

    The blocks are the provider's own wire content, in stream order; the loop
    treats them as opaque and sends them back only within the same turn.
    """

    blocks: tuple[Mapping[str, Any], ...]


ProviderEvent: TypeAlias = TextDelta | ToolCallRequest | TurnStop | ThinkingStarted | ReplayContent


class AssistantProvider(Protocol):
    """Provider-neutral streaming interface consumed by the agent loop."""

    def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]: ...


class AssistantProviderError(HauteError):
    """A sanitized provider or provider-stream failure."""

    def __init__(
        self,
        provider: str,
        failure_class: str = "stream",
        detail: str | None = None,
    ) -> None:
        self.provider = provider
        self.failure_class = failure_class
        message = f"{provider} provider {failure_class} failure"
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__(message)


def _provider_error(provider: str, failure_class: str, detail: str) -> AssistantProviderError:
    """Build an error from hand-authored text only; never include SDK text."""

    return AssistantProviderError(provider, failure_class, detail)


#: Failure categories a request raised before any response stream exists may
#: retry: none produced partial output, so resending cannot duplicate text or
#: tool calls.
_PRE_STREAM_RETRYABLE_CATEGORIES = frozenset({"rate_limit", "connection"})

#: Connect bound for OpenAI-compatible clients. The SDK default (five seconds)
#: timed out live against a Databricks serving endpoint's cold connection.
_PROVIDER_CONNECT_TIMEOUT_SECONDS = 30.0


def _failure_category(error: Exception) -> str:
    """Classify an SDK exception by its class name, without logging."""

    class_name = type(error).__name__.lower()
    if "auth" in class_name or "permission" in class_name:
        return "authentication"
    if "rate" in class_name or "ratelimit" in class_name:
        return "rate_limit"
    if any(word in class_name for word in ("connection", "timeout", "network")):
        return "connection"
    if "status" in class_name or class_name in {
        "badrequesterror",
        "internalservererror",
        "apierror",
    }:
        return "status"
    return "stream"


def _classify_sdk_error(provider: str, error: Exception) -> AssistantProviderError:
    """Map an SDK exception class to a sanitized failure category.

    The raw provider error is logged server-side for the operator — the
    sanitization contract only forbids leaking it into the chat stream.
    Without this log a quota/authentication failure is undiagnosable from
    ``haute serve`` output.
    """

    logger.warning(
        "assistant_provider_request_failed",
        provider=provider,
        error_class=type(error).__name__,
        detail=str(error),
    )
    return _provider_error(
        provider, _failure_category(error), "the provider request could not be completed"
    )


def _usage_value(value: object, provider: str, field: str) -> int:
    if value is None:
        return 0
    if type(value) is not int:
        raise _provider_error(provider, "malformed_stream", f"invalid {field} usage")
    result = value
    if result < 0:
        raise _provider_error(provider, "malformed_stream", f"invalid {field} usage")
    return result


def _text_value(value: object) -> str:
    """One text field of a streamed Anthropic content block, or a malformed stream."""

    if not isinstance(value, str):
        raise _provider_error("anthropic", "malformed_stream", "a content block field is not text")
    return value


def _attr(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _chunk_shape(chunk: object) -> str:
    """Describe one raw OpenAI-dialect chunk structurally, never its content.

    Emitted at debug level so an operator can capture a gateway's exact wire
    dialect (content kinds, finish reasons, usage placement) from a live
    stream. Text values, tool arguments, and any other user or model content
    are deliberately excluded — only type names, counts, and finish/usage
    markers appear. Total by construction: it must never raise mid-stream.
    """

    parts: list[str] = []
    raw_choices = _attr(chunk, "choices", ())
    if raw_choices is None:
        raw_choices = ()
    if not isinstance(raw_choices, Sequence) or isinstance(raw_choices, (str, bytes)):
        parts.append(f"choices=<{type(raw_choices).__name__}>")
    else:
        for choice in raw_choices:
            delta = _attr(choice, "delta")
            bits: list[str] = []
            content = _attr(delta, "content")
            if isinstance(content, str):
                bits.append(f"text[{len(content)}]")
            elif isinstance(content, Sequence) and not isinstance(content, bytes):
                kinds = ",".join(str(_attr(part, "type")) for part in content)
                bits.append(f"parts[{kinds}]")
            elif content is not None:
                bits.append(f"content=<{type(content).__name__}>")
            tool_calls = _attr(delta, "tool_calls")
            if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
                bits.append(f"tools[{len(tool_calls)}]")
            finish = _attr(choice, "finish_reason")
            if finish is not None:
                bits.append(f"finish={finish}")
            parts.append("{" + " ".join(bits) + "}")
    if _attr(chunk, "usage") is not None:
        parts.append("usage")
    return " ".join(parts) or "empty"


def _iter_content_text(content: object, provider: str = "openai") -> Iterator[str]:
    """Normalise an OpenAI content delta into assistant text fragments.

    api.openai.com streams plain strings.  OpenAI-compatible gateways serving
    Anthropic models (e.g. Databricks Foundation Model APIs) instead stream a
    list of typed content parts: ``text`` parts carry the assistant reply and
    ``reasoning`` parts carry thinking summaries, which the chat deliberately
    does not surface.  Any other shape fails loudly.
    """

    if isinstance(content, str):
        yield content
        return
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        raise _provider_error(provider, "malformed_stream", "content delta is not text")
    for part in content:
        part_type = _attr(part, "type")
        if part_type == "reasoning":
            continue
        if part_type == "text":
            text = _attr(part, "text")
            if not isinstance(text, str):
                raise _provider_error(
                    provider, "malformed_stream", "text content part carries no text"
                )
            yield text
            continue
        raise _provider_error(provider, "malformed_stream", "unsupported content part type")


def _map_stop_reason(provider: str, reason: object) -> Literal["end", "tool_use"]:
    """Map natural stops; surface every non-natural stop as a typed failure.

    A truncated (output-token limit) or filtered/refused response must never
    be presented as a completed turn — partial prose reading as an answer is
    exactly the silent-wrongness class the project forbids.
    """

    if not isinstance(reason, str):
        raise _provider_error(provider, "malformed_stream", "unsupported stop reason")
    if reason in {"end_turn", "stop", "stop_sequence"}:
        return "end"
    if reason in {"tool_use", "tool_calls", "function_call"}:
        return "tool_use"
    if reason in {"max_tokens", "length"}:
        raise _provider_error(
            provider, "truncated", "the output-token limit was reached before the turn finished"
        )
    if reason in {"refusal", "content_filter"}:
        raise _provider_error(provider, "filtered", "the provider filtered or refused the output")
    raise _provider_error(provider, "malformed_stream", "unsupported stop reason")


def _parse_tool_arguments(
    provider: str,
    raw: str,
    *,
    initial: object = None,
) -> dict[str, Any]:
    if not raw:
        if initial is None:
            return {}
        if isinstance(initial, Mapping):
            return dict(initial)
        raise _provider_error(provider, "malformed_stream", "tool arguments are not an object")

    try:
        parsed = json.loads(
            raw,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _provider_error(
            provider, "malformed_stream", "tool arguments are not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise _provider_error(provider, "malformed_stream", "tool arguments are not an object")
    return parsed


def _reject_json_constant(_value: str) -> Any:
    """Reject NaN and infinities, which are not finite JSON values."""

    raise ValueError


def _tool_input_schemas(
    tools: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, object]]:
    """Index trusted advertised input schemas by tool name."""

    schemas: dict[str, Mapping[str, object]] = {}
    for tool in tools:
        name = tool.get("name")
        schema = tool.get("input_schema")
        if isinstance(name, str) and isinstance(schema, Mapping):
            schemas[name] = schema
    return schemas


def _declared_compatible_types(schema: Mapping[str, object]) -> frozenset[str]:
    """Return exclusively declared compatible JSON types, or no safe target."""

    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        declared = frozenset({raw_type})
    elif (
        isinstance(raw_type, Sequence)
        and not isinstance(raw_type, (str, bytes))
        and all(isinstance(item, str) for item in raw_type)
    ):
        declared = frozenset(raw_type)
    else:
        return frozenset()
    compatible = frozenset({"array", "object", "boolean", "integer", "number"})
    return declared if declared and declared <= compatible else frozenset()


def _matches_declared_json_type(value: object, expected: frozenset[str]) -> bool:
    """Return whether a decoded JSON value has one of the exact declared types."""

    if "array" in expected and isinstance(value, list):
        return True
    if "object" in expected and isinstance(value, Mapping):
        return True
    if "boolean" in expected and type(value) is bool:
        return True
    if "integer" in expected and type(value) is int:
        return True
    if "number" in expected:
        if type(value) is int:
            return True
        if type(value) is float and math.isfinite(value):
            return True
    return False


def _compatible_schema_type(schema: Mapping[str, object]) -> str | None:
    """Return the compatible projection's declared type for one schema fragment."""

    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        return raw_type
    if (
        isinstance(raw_type, Sequence)
        and not isinstance(raw_type, (str, bytes))
        and raw_type
        and all(isinstance(item, str) for item in raw_type)
    ):
        non_null = tuple(dict.fromkeys(item for item in raw_type if item != "null"))
        return non_null[0] if len(non_null) == 1 else None

    branch_types: list[str] = []
    for keyword in ("oneOf", "anyOf", "allOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, Sequence) or isinstance(branches, (str, bytes)):
            continue
        for branch in branches:
            if not isinstance(branch, Mapping):
                return None
            branch_type = _compatible_schema_type(branch)
            if branch_type is None and isinstance(branch.get("properties"), Mapping):
                branch_type = "object"
            if branch_type is None:
                return None
            branch_types.append(branch_type)
        if branch_types:
            return branch_types[0] if len(set(branch_types)) == 1 else None
    return None


def _allowed_values(schema: Mapping[str, object]) -> tuple[object, ...] | None:
    if "const" in schema:
        return (schema["const"],)
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)) and enum:
        return tuple(enum)
    return None


@dataclass
class _SchemaBudget:
    """Bounded property count projected onto a provider wire schema."""

    remaining: int = 40


def _compatible_property_schema(
    schemas: Sequence[Mapping[str, object]],
    budget: _SchemaBudget,
) -> dict[str, object]:
    if schemas and all(schema == schemas[0] for schema in schemas[1:]):
        return _compatible_tool_schema(schemas[0], budget)

    allowed = [_allowed_values(schema) for schema in schemas]
    if allowed and all(values is not None for values in allowed):
        combined: list[object] = []
        for values in allowed:
            assert values is not None
            for value in values:
                if value not in combined:
                    combined.append(value)
        return {"enum": combined}

    types = [_compatible_schema_type(schema) for schema in schemas]
    if types and types[0] is not None and all(value == types[0] for value in types):
        projected: dict[str, object] = {"type": types[0]}
        if types[0] == "array":
            item_schemas = [schema.get("items") for schema in schemas]
            if all(isinstance(items, Mapping) for items in item_schemas):
                projected["items"] = _compatible_tool_schema(
                    {"oneOf": item_schemas},
                    budget,
                )
        descriptions = tuple(
            dict.fromkeys(
                description
                for schema in schemas
                if isinstance((description := schema.get("description")), str)
            )
        )
        if descriptions:
            projected["description"] = " ".join(descriptions)
        return projected
    return {}


def _required_names(schema: Mapping[str, object]) -> tuple[str, ...]:
    required = schema.get("required")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return ()
    return tuple(item for item in required if isinstance(item, str))


def _compatible_composed_object(
    schema: Mapping[str, object],
    budget: _SchemaBudget,
) -> dict[str, object] | None:
    """Merge one closed object union when it fits the remaining property budget."""

    raw_branches: object | None = None
    for keyword in ("oneOf", "anyOf", "allOf"):
        candidate = schema.get(keyword)
        if candidate is not None:
            raw_branches = candidate
            break
    if not isinstance(raw_branches, Sequence) or isinstance(raw_branches, (str, bytes)):
        return None

    branches: list[Mapping[str, object]] = []
    for branch in raw_branches:
        if not isinstance(branch, Mapping):
            return None
        branches.append(branch)
    if not branches:
        return None

    property_maps: list[Mapping[str, object]] = []
    for branch in branches:
        properties = branch.get("properties")
        if (
            _compatible_schema_type(branch) != "object"
            or not isinstance(properties, Mapping)
            or branch.get("additionalProperties") is not False
        ):
            return None
        property_maps.append(properties)

    names: list[str] = []
    for properties in property_maps:
        for name in properties:
            if isinstance(name, str) and name not in names:
                names.append(name)
    if len(names) > budget.remaining:
        return {"type": "object"}
    budget.remaining -= len(names)

    projected_properties: dict[str, object] = {}
    for name in names:
        property_schemas: list[Mapping[str, object]] = []
        for properties in property_maps:
            property_schema = properties.get(name)
            if isinstance(property_schema, Mapping):
                property_schemas.append(property_schema)
        projected_properties[name] = _compatible_property_schema(property_schemas, budget)

    branch_required = [_required_names(branch) for branch in branches]
    required_sets = [set(names) for names in branch_required]
    required = [
        name
        for name in branch_required[0]
        if all(name in required_set for required_set in required_sets)
    ]
    projected: dict[str, object] = {
        "type": "object",
        "properties": projected_properties,
        "additionalProperties": False,
    }
    if required:
        projected["required"] = required
    return projected


def _numeric_bounds(schema: Mapping[str, object]) -> str | None:
    """A number's `minimum` and `maximum` in words, which the wire subsets drop."""

    bounds = [
        f"{word} {schema[keyword]}"
        for keyword, word in (("minimum", "at least"), ("maximum", "at most"))
        if isinstance(schema.get(keyword), int | float) and not isinstance(schema[keyword], bool)
    ]
    return f"{', '.join(bounds)}.".capitalize() if bounds else None


def _description_with_bounds(schema: Mapping[str, object]) -> str:
    """A fragment's description followed by its numeric bounds in words, or ``""``."""

    return " ".join(
        part
        for part in (schema.get("description"), _numeric_bounds(schema))
        if isinstance(part, str) and part
    )


def _compatible_tool_schema(
    schema: Mapping[str, object],
    budget: _SchemaBudget | None = None,
) -> dict[str, object]:
    """Project canonical validation schema onto one bounded provider wire subset."""

    if budget is None:
        budget = _SchemaBudget()
    composed = _compatible_composed_object(schema, budget)
    if composed is not None:
        return composed
    projected: dict[str, object] = {}
    projected_type = _compatible_schema_type(schema)
    if projected_type is not None:
        projected["type"] = projected_type

    description = _description_with_bounds(schema)
    if description:
        projected["description"] = description

    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)):
        projected["enum"] = list(enum)
    elif "const" in schema:
        projected["enum"] = [schema["const"]]

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        property_names = [name for name in properties if isinstance(name, str)]
        if len(property_names) > budget.remaining:
            return {"type": projected_type or "object"}
        budget.remaining -= len(property_names)
        projected_properties = {
            str(name): _compatible_tool_schema(value, budget)
            for name, value in properties.items()
            if isinstance(value, Mapping)
        }
        projected["properties"] = projected_properties
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
            projected_required = [
                name for name in required if isinstance(name, str) and name in projected_properties
            ]
            if projected_required:
                projected["required"] = projected_required

    items = schema.get("items")
    if isinstance(items, Mapping):
        projected["items"] = _compatible_tool_schema(items, budget)

    if schema.get("additionalProperties") is False:
        projected["additionalProperties"] = False
    return projected


#: Which projection of the canonical tool schemas a provider lane sends.
ToolProjection: TypeAlias = Literal["compatible", "canonical"]
#: Whose strict-mode rules a strict tool's schema follows.
StrictDialect: TypeAlias = Literal["anthropic", "openai"]

#: The operations a strict tool can be: those that leave the project as it is (the
#: read tools and the build plan's update).
_NON_MUTATING_OPERATION_IDS = frozenset(OPERATION_IDS) - MUTATING_OPERATION_IDS


def _tool_parts(
    tools: Sequence[Mapping[str, Any]],
) -> Iterator[tuple[str, str, Mapping[str, object]]]:
    """Each canonical tool's name, description and input schema."""

    for tool in tools:
        name = tool.get("name")
        schema = tool.get("input_schema")
        if not isinstance(name, str) or not isinstance(schema, Mapping):
            raise TypeError("Tool definitions require a string name and mapping input_schema")
        description = tool.get("description", "")
        yield name, description if isinstance(description, str) else "", schema


def _compatible_tools(
    tools: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    """The compatible projection: one flat, bounded wire schema per tool.

    The Databricks lane's default, on which its live baselines were measured.
    """

    return [
        {"name": name, "description": description, "input_schema": _compatible_tool_schema(schema)}
        for name, description, schema in _tool_parts(tools)
    ]


class _NotStrictError(Exception):
    """A canonical schema fragment the strict subset cannot express."""


def _json_type_name(value: object) -> str:
    """The JSON type of one enumerated scalar; strict decoding enumerates no other."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    raise _NotStrictError


def _strict_schema(
    schema: Mapping[str, object], dialect: StrictDialect, *, nullable: bool
) -> dict[str, object]:
    """Reduce one canonical fragment to the keywords strict decoding accepts.

    Types, descriptions, enumerations, properties, required fields, items and
    closure survive; every other validation keyword, which Anthropic's strict
    mode refuses with an HTTP 400, is dropped (the canonical validator still
    enforces it), and a number's bounds are stated in its description. An
    enumeration without a type declares its one JSON type. Under the OpenAI
    dialect every property is required and each optional one nullable, since
    OpenAI's strict mode requires every property. Raises `_NotStrictError` for a
    composition, an object that is not closed or declares no properties, a
    nullable or multi-typed value, and a value with neither a type nor an
    enumeration of one JSON type.
    """

    if any(keyword in schema for keyword in ("oneOf", "anyOf", "allOf", "$ref", "not")):
        raise _NotStrictError
    allowed = _allowed_values(schema)
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        json_type = raw_type
    elif raw_type is None and allowed is not None:
        types = {_json_type_name(value) for value in allowed}
        if len(types) != 1:
            raise _NotStrictError
        (json_type,) = types
    else:
        raise _NotStrictError
    projected: dict[str, object] = {"type": [json_type, "null"] if nullable else json_type}
    description = _description_with_bounds(schema)
    if description:
        projected["description"] = description
    if allowed is not None:
        projected["enum"] = [*allowed, None] if nullable else list(allowed)
    if json_type == "object":
        properties = schema.get("properties")
        if schema.get("additionalProperties") is not False or not isinstance(properties, Mapping):
            raise _NotStrictError
        required = _required_names(schema)
        projected_properties: dict[str, object] = {}
        for name, value in properties.items():
            if not isinstance(value, Mapping):
                raise _NotStrictError
            projected_properties[str(name)] = _strict_schema(
                value, dialect, nullable=dialect == "openai" and name not in required
            )
        projected["properties"] = projected_properties
        projected["required"] = (
            list(projected_properties)
            if dialect == "openai"
            else [name for name in required if name in projected_properties]
        )
        projected["additionalProperties"] = False
    elif json_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise _NotStrictError
        projected["items"] = _strict_schema(items, dialect, nullable=False)
    return projected


def _strict_tool_schema(
    schema: Mapping[str, object], dialect: StrictDialect
) -> dict[str, object] | None:
    """A tool's strict input schema, or None when its canonical schema cannot be strict."""

    try:
        return _strict_schema(schema, dialect, nullable=False)
    except _NotStrictError:
        return None


def _canonical_tools(
    tools: Sequence[Mapping[str, Any]],
    strict: StrictDialect | None,
) -> list[dict[str, object]]:
    """The canonical projection: each tool's canonical input schema itself.

    Under a *strict* dialect, an operation that leaves the project as it is, whose
    schema reduces to the strict subset, is sent that reduction with
    ``"strict": true``; no other tool carries a ``strict`` key.
    """

    projected: list[dict[str, object]] = []
    for name, description, schema in _tool_parts(tools):
        strict_schema = (
            _strict_tool_schema(schema, strict)
            if strict is not None and name in _NON_MUTATING_OPERATION_IDS
            else None
        )
        if strict_schema is None:
            projected.append(
                {
                    "name": name,
                    "description": description,
                    # Its own copy: the canonical definitions are the validator's.
                    "input_schema": copy.deepcopy(dict(schema)),
                }
            )
        else:
            projected.append(
                {
                    "name": name,
                    "description": description,
                    "input_schema": strict_schema,
                    "strict": True,
                }
            )
    return projected


def _without_strict_nulls(value: Any, schema: Mapping[str, object]) -> Any:
    """Omit each `null` an OpenAI strict schema allows only because a property is optional.

    The strict reduction made every optional property required and nullable and
    refuses a property that was nullable already, so such a `null` says only
    that the model left the property out. A `null` anywhere else is kept for
    the canonical validator.
    """

    if isinstance(value, Mapping):
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return value
        required = _required_names(schema)
        kept: dict[str, Any] = {}
        for key, item in value.items():
            declared = properties.get(key)
            if item is None and isinstance(declared, Mapping) and key not in required:
                continue
            kept[key] = (
                _without_strict_nulls(item, declared) if isinstance(declared, Mapping) else item
            )
        return kept
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            return [_without_strict_nulls(item, items) for item in value]
    return value


def _same_json_value(left: object, right: object) -> bool:
    """Compare JSON values exactly, so ``True`` never equals ``1``."""

    return type(left) is type(right) and left == right


def _discriminated_branch_properties(
    schema: Mapping[str, object],
    value: Mapping[str, Any],
) -> Mapping[str, object] | None:
    """Return the properties of the one closed-object branch *value* selects.

    A discriminator is a property every remaining branch declares with
    ``const``/``enum`` values. While several branches remain, the first
    discriminator whose values differ between them narrows them by the value's
    own; once one remains, every discriminator it still declares must admit
    the value too. An operation therefore selects its branch by ``op``, and a
    ``recipe`` operation then by ``recipe``. A missing or unmatched
    discriminator, or branches no discriminator separates, select nothing:
    there is no declared type to decode against, and the canonical validator
    names the problem.
    """

    raw_branches: object = None
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            raw_branches = schema[keyword]
            break
    if not isinstance(raw_branches, Sequence) or isinstance(raw_branches, (str, bytes)):
        return None
    remaining: list[Mapping[str, object]] = []
    for branch in raw_branches:
        properties = branch.get("properties") if isinstance(branch, Mapping) else None
        if not isinstance(properties, Mapping):
            return None
        remaining.append(properties)
    if not remaining:
        return None

    def allowed_values(properties: Mapping[str, object], name: str) -> tuple[object, ...] | None:
        property_schema = properties.get(name)
        if not isinstance(property_schema, Mapping):
            return None
        return _allowed_values(property_schema)

    used: set[str] = set()
    while True:
        candidates = [
            name
            for name in remaining[0]
            if name not in used
            and all(allowed_values(properties, name) is not None for properties in remaining)
            and (
                len(remaining) == 1
                or len({repr(allowed_values(properties, name)) for properties in remaining}) > 1
            )
        ]
        if not candidates:
            return remaining[0] if len(remaining) == 1 else None
        name = candidates[0]
        if name not in value:
            return None
        remaining = [
            properties
            for properties in remaining
            if any(
                _same_json_value(value[name], allowed)
                for allowed in allowed_values(properties, name) or ()
            )
        ]
        if not remaining:
            return None
        used.add(name)


def _decode_databricks_value(value: Any, schema: Mapping[str, object], path: str) -> Any:
    """Decode one value, and what it contains, by the canonical schema at its position.

    A string is decoded only when the schema exclusively declares a compatible
    JSON type and the decoded value has that type; otherwise it stays a
    string for the canonical validator. Two spellings decode by the declared
    type alone, logged: plain text where a list of text is declared becomes a
    one-item list (text opening like JSON never does), and `True`
    or `False` where a boolean is declared becomes that boolean. An object's declared properties, an
    object union's selected branch and an array's declared items are walked
    the same way, so a recipe operation's ``arguments`` decode by that
    recipe's argument schema. A schema that declares nothing below a value
    leaves it as sent.
    """

    if isinstance(value, str):
        expected = _declared_compatible_types(schema)
        if not expected:
            return value
        items = schema.get("items")
        if (
            expected == {"array"}
            and isinstance(items, Mapping)
            and items.get("type") == "string"
            and value.lstrip()[:1] not in {"[", "{", '"'}
        ):
            # A declared list of text sent as one plain text: the one item it is.
            # Text that opens like JSON is decoded below, or stays text and fails.
            logger.warning(
                "assistant_databricks_argument_wrapped_in_array",
                field=path,
                encoded_length=len(value),
            )
            return [value]
        if expected == {"boolean"} and value in {"True", "False"}:
            # Python's spelling of a JSON boolean, which `json.loads` refuses.
            logger.warning("assistant_databricks_argument_python_boolean", field=path)
            return value == "True"
        try:
            decoded = json.loads(value, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError):
            # An eligible field that will now certainly fail canonical
            # validation as `wrong_type`. Logging the shape — never the value —
            # is what distinguishes "the gateway sent a dialect we do not
            # decode" from "the model composed the wrong argument", which the
            # redacted session record cannot tell apart after the fact.
            logger.warning(
                "assistant_databricks_argument_decode_failed",
                field=path,
                declared_types=sorted(expected),
                encoded_length=len(value),
                looks_like_json_container=value.lstrip()[:1] in {"[", "{"},
            )
            return value
        if not _matches_declared_json_type(decoded, expected):
            logger.warning(
                "assistant_databricks_argument_decoded_wrong_type",
                field=path,
                declared_types=sorted(expected),
                decoded_type=type(decoded).__name__,
            )
            return value
        value = decoded
    if isinstance(value, Mapping):
        declared = schema.get("properties")
        properties = (
            declared
            if isinstance(declared, Mapping)
            else _discriminated_branch_properties(schema, value)
        )
        if properties is None:
            return value
        decoded_object: dict[str, Any] = {}
        for key, item in value.items():
            declared_schema = properties.get(key)
            decoded_object[key] = (
                _decode_databricks_value(item, declared_schema, f"{path}.{key}" if path else key)
                if isinstance(declared_schema, Mapping)
                else item
            )
        return decoded_object
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            return [
                _decode_databricks_value(item, items, f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
    return value


def _normalise_databricks_tool_arguments(
    arguments: Mapping[str, Any],
    schema: Mapping[str, object],
) -> dict[str, Any]:
    """Decode Databricks' stringified JSON values by the tool's canonical schema.

    Databricks-hosted Qwen models have been observed to return a valid outer
    function-arguments object while encoding container and scalar properties as
    JSON strings, at the top level and inside a recipe operation's arguments.
    Only values whose canonical schema exclusively declares a compatible JSON
    type are eligible, wherever the schema declares them. Strings, nulls,
    undeclared values, ambiguous schemas, and non-finite numbers are left
    untouched. Invalid or wrong-type encodings remain strings so canonical tool
    validation can reject them as recoverable invalid input.
    """

    return dict(_decode_databricks_value(arguments, schema, ""))


def _load_anthropic_client(config: AssistantConfig) -> Any:
    try:
        import anthropic
    except (ImportError, ModuleNotFoundError) as exc:
        raise _provider_error(
            "anthropic", "dependency", "the anthropic SDK is not installed"
        ) from exc
    try:
        return anthropic.AsyncAnthropic(api_key=config.api_key)
    except Exception as exc:
        raise _classify_sdk_error("anthropic", exc) from exc


def _load_openai_client(config: AssistantConfig, provider: str = "openai") -> Any:
    try:
        import httpx  # the openai SDK's own transport dependency
        import openai
    except (ImportError, ModuleNotFoundError) as exc:
        raise _provider_error(provider, "dependency", "the openai SDK is not installed") from exc
    # The read bound follows the turn timeout so a stalled stream cannot
    # outlive the turn that owns it.
    read_timeout = float(int_env(TURN_TIMEOUT_ENV, DEFAULT_TURN_TIMEOUT))
    kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "timeout": httpx.Timeout(read_timeout, connect=_PROVIDER_CONNECT_TIMEOUT_SECONDS),
    }
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url
    if provider == "databricks":
        kwargs["max_retries"] = 0
    try:
        return openai.AsyncOpenAI(**kwargs)
    except Exception as exc:
        raise _classify_sdk_error(provider, exc) from exc


def _json_string(value: object) -> str:
    """Encode one neutral JSON value for a provider string-content field."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


#: Anthropic models that accept a `system` message in the middle of the
#: conversation. Every other model reads a turn context as the leading text of
#: the user message it follows.
MID_CONVERSATION_SYSTEM_MODELS = frozenset(
    {
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-mythos-5",
        "claude-mythos-5-1",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
    }
)

#: The effort every Claude request carries: valid on every model in
#: `ADAPTIVE_THINKING_MODELS`, and Anthropic's starting point for multistep tool use.
ANTHROPIC_EFFORT = "medium"


def _with_leading_context(previous: dict[str, Any] | None, context: object) -> None:
    """Prepend a turn context to the user message it follows, in place."""

    if (
        previous is None
        or previous.get("role") != "user"
        or not isinstance(previous.get("content"), str)
        or not isinstance(context, str)
    ):
        raise RuntimeError("a turn context message must follow a user text message or tool results")
    previous["content"] = f"{context}\n\n## Analyst message\n{previous['content']}"


def _is_anthropic_tool_results(message: Mapping[str, Any] | None) -> bool:
    """Whether *message* is the user message carrying one round's tool results."""

    content = None if message is None else message.get("content")
    return (
        message is not None
        and message.get("role") == "user"
        and isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, Mapping) and block.get("type") == "tool_result" for block in content
        )
    )


def _anthropic_messages(
    messages: Sequence[Mapping[str, Any]], *, system_context: bool
) -> list[dict[str, Any]]:
    """Translate neutral history into Anthropic Messages content blocks.

    With *system_context* a turn context travels as a mid-conversation
    `system` message; otherwise it leads the user message it follows.
    """

    translated: list[dict[str, Any]] = []
    previous_role: object = None
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "context":
            previous = translated[-1] if translated else None
            if system_context:
                translated.append({"role": "system", "content": content})
            elif previous is not None and _is_anthropic_tool_results(previous):
                # A turn context update after an apply's round: text after the
                # round's results, in the same user message.
                if not isinstance(content, str):
                    raise RuntimeError("a turn context message must be text")
                previous["content"].append({"type": "text", "text": content})
            else:
                _with_leading_context(previous, content)
        elif role == "tool":
            result_block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": _json_string(content),
                "is_error": bool(message.get("is_error", False)),
            }
            # One round's results travel in one user message, as the
            # Messages API expects for parallel tool calls.
            if previous_role == "tool":
                translated[-1]["content"].append(result_block)
            else:
                translated.append({"role": "user", "content": [result_block]})
        elif role == "assistant" and message.get("provider_content") is not None:
            # Within a turn the message goes back exactly as the model produced
            # it, thinking blocks and their signatures included.
            replayed = [dict(block) for block in message["provider_content"]]
            replayed_ids = [block["id"] for block in replayed if block.get("type") == "tool_use"]
            call_ids = [call["id"] for call in message.get("tool_calls") or ()]
            if replayed_ids != call_ids:
                raise RuntimeError("replayed content must carry exactly the message's tool calls")
            translated.append({"role": "assistant", "content": replayed})
        elif role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if content not in (None, ""):
                blocks.append({"type": "text", "text": content})
            for call in message["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    }
                )
            translated.append({"role": "assistant", "content": blocks})
        elif role == "controller":
            translated.append({"role": "user", "content": content})
        else:
            translated.append({"role": role, "content": content})
        previous_role = role
    return translated


class AnthropicProvider:
    """Normalize the Anthropic Messages streaming API.

    Every request marks the tool definitions and the frozen system prompt as
    one prompt-cache breakpoint and runs adaptive thinking at
    `ANTHROPIC_EFFORT`, so only the models in `ADAPTIVE_THINKING_MODELS` are
    accepted. It sends the canonical tool projection, the closed read tools in
    strict mode. A round whose message holds a thinking block ends with its
    content blocks in stream order (`ReplayContent`), for the loop to send back
    verbatim within the turn.
    """

    def __init__(self, config: AssistantConfig, client: Any | None = None) -> None:
        unsupported = unsupported_anthropic_model(config.model)
        if unsupported is not None:
            raise ConfigError(unsupported)
        self.config = config
        self.client = _load_anthropic_client(config) if client is None else client

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        input_tokens = 0
        output_tokens = 0
        stop_reason: Literal["end", "tool_use"] | None = None
        stop_emitted = False
        pending_tools: dict[int, dict[str, Any]] = {}
        # Thinking and text blocks still streaming, by index, and every finished
        # content block by index: the round's replay content when it thinks.
        pending_thinking: dict[int, dict[str, list[str]]] = {}
        pending_text: dict[int, list[str]] = {}
        finished: dict[int, dict[str, Any]] = {}
        thought = False
        wire_tools = _canonical_tools(tools, "anthropic")

        try:
            stream = self.client.messages.stream(
                model=self.config.model,
                # The one cache breakpoint: tools render before the system
                # prompt, so it caches both. Everything per-turn is in messages.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=_anthropic_messages(
                    messages,
                    system_context=self.config.model in MID_CONVERSATION_SYSTEM_MODELS,
                ),
                tools=wire_tools,
                max_tokens=self.config.max_output_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": ANTHROPIC_EFFORT},
            )
            async with stream as response:
                async for event in response:
                    event_type = _attr(event, "type")
                    if event_type == "message_start":
                        usage = _attr(_attr(event, "message"), "usage")
                        input_tokens = _usage_value(
                            _attr(usage, "input_tokens"), "anthropic", "input_tokens"
                        )
                    elif event_type == "content_block_start":
                        index = _attr(event, "index")
                        block = _attr(event, "content_block")
                        block_type = _attr(block, "type")
                        if block_type in {"thinking", "redacted_thinking"}:
                            if not isinstance(index, int):
                                raise _provider_error(
                                    "anthropic", "malformed_stream", "thinking block has no index"
                                )
                            if index in finished or index in pending_thinking:
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "duplicate thinking block index",
                                )
                            thought = True
                            if block_type == "thinking":
                                pending_thinking[index] = {
                                    "thinking": [_text_value(_attr(block, "thinking", ""))],
                                    "signature": [_text_value(_attr(block, "signature", ""))],
                                }
                            else:
                                finished[index] = {
                                    "type": "redacted_thinking",
                                    "data": _text_value(_attr(block, "data")),
                                }
                            yield ThinkingStarted()
                        elif block_type == "text" and isinstance(index, int):
                            pending_text[index] = [_text_value(_attr(block, "text", ""))]
                        elif block_type == "tool_use":
                            if not isinstance(index, int):
                                raise _provider_error(
                                    "anthropic", "malformed_stream", "tool block has no index"
                                )
                            if index in pending_tools:
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "duplicate tool block index",
                                )
                            pending_tools[index] = {
                                "id": _attr(block, "id"),
                                "name": _attr(block, "name"),
                                "fragments": [],
                                "initial": _attr(block, "input", {}),
                            }
                    elif event_type == "content_block_delta":
                        index = _attr(event, "index")
                        delta = _attr(event, "delta")
                        delta_type = _attr(delta, "type")
                        if delta_type == "text_delta":
                            text = _attr(delta, "text")
                            if text is not None:
                                if not isinstance(text, str):
                                    raise _provider_error(
                                        "anthropic", "malformed_stream", "text delta is not text"
                                    )
                                if isinstance(index, int):
                                    pending_text.setdefault(index, []).append(text)
                                yield TextDelta(text=text)
                        elif delta_type in {"thinking_delta", "signature_delta"}:
                            if not isinstance(index, int) or index not in pending_thinking:
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "thinking fragment has no block",
                                )
                            field_name = (
                                "thinking" if delta_type == "thinking_delta" else "signature"
                            )
                            pending_thinking[index][field_name].append(
                                _text_value(_attr(delta, field_name))
                            )
                        elif delta_type == "input_json_delta":
                            if not isinstance(index, int) or index not in pending_tools:
                                raise _provider_error(
                                    "anthropic", "malformed_stream", "tool fragment has no block"
                                )
                            partial = _attr(delta, "partial_json", "")
                            if not isinstance(partial, str):
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "tool fragment is not text",
                                )
                            pending_tools[index]["fragments"].append(partial)
                    elif event_type == "content_block_stop":
                        index = _attr(event, "index")
                        if not isinstance(index, int):
                            continue
                        thinking = pending_thinking.pop(index, None)
                        if thinking is not None:
                            signature = "".join(thinking["signature"])
                            if not signature:
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "thinking block has no signature",
                                )
                            finished[index] = {
                                "type": "thinking",
                                "thinking": "".join(thinking["thinking"]),
                                "signature": signature,
                            }
                        text_parts = pending_text.pop(index, None)
                        if text_parts is not None and "".join(text_parts):
                            finished[index] = {"type": "text", "text": "".join(text_parts)}
                        tool = pending_tools.pop(index, None)
                        if tool is not None:
                            tool_id = tool["id"]
                            tool_name = tool["name"]
                            if not isinstance(tool_id, str) or not isinstance(tool_name, str):
                                raise _provider_error(
                                    "anthropic",
                                    "malformed_stream",
                                    "tool block is missing its id or name",
                                )
                            arguments = _parse_tool_arguments(
                                "anthropic",
                                "".join(tool["fragments"]),
                                initial=tool["initial"],
                            )
                            # Its own copy: the replay must not follow what a
                            # tool later does to the arguments it was given.
                            finished[index] = {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": copy.deepcopy(arguments),
                            }
                            yield ToolCallRequest(tool_id, tool_name, arguments)
                    elif event_type == "message_delta":
                        delta = _attr(event, "delta")
                        raw_reason = _attr(delta, "stop_reason")
                        usage = _attr(event, "usage")
                        if _attr(usage, "output_tokens") is not None:
                            output_tokens = _usage_value(
                                _attr(usage, "output_tokens"), "anthropic", "output_tokens"
                            )
                        if raw_reason is not None:
                            stop_reason = _map_stop_reason("anthropic", raw_reason)
                    elif event_type == "message_stop" and not stop_emitted:
                        if pending_tools:
                            raise _provider_error(
                                "anthropic", "malformed_stream", "stream ended inside a tool block"
                            )
                        if pending_thinking:
                            raise _provider_error(
                                "anthropic",
                                "malformed_stream",
                                "stream ended inside a thinking block",
                            )
                        if stop_reason is None:
                            raise _provider_error(
                                "anthropic", "malformed_stream", "message has no stop reason"
                            )
                        if thought:
                            yield ReplayContent(
                                tuple(finished[index] for index in sorted(finished))
                            )
                        yield TurnStop(
                            stop_reason,
                            ProviderUsage(input_tokens, output_tokens),
                        )
                        stop_emitted = True
            if not stop_emitted:
                raise _provider_error(
                    "anthropic", "malformed_stream", "stream ended without a stop event"
                )
        except AssistantProviderError:
            raise
        except Exception as exc:
            raise _classify_sdk_error("anthropic", exc) from exc


def _openai_messages(system: str, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Translate neutral history into OpenAI Chat Completions messages."""

    translated: list[dict[str, Any]] = [{"role": "system", "content": system}]
    previous_role: object = None
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if message.get("provider_content") is not None:
            raise RuntimeError("replay content belongs to the adapter that emitted it")
        if role == "user" and previous_role == "controller":
            # A compacted history's leading note leads the user message after
            # it: two consecutive user messages are refused by some gateways.
            if not isinstance(content, str):
                raise RuntimeError("a user message after a controller note must be text")
            translated[-1]["content"] = f"{translated[-1]['content']}\n\n{content}"
        elif role == "assistant" and message.get("tool_calls"):
            translated.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": _json_string(call["arguments"]),
                            },
                        }
                        for call in message["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            translated.append(
                {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": _json_string(content),
                }
            )
        elif role == "controller":
            translated.append({"role": "user", "content": content})
        elif role == "context":
            if translated[-1].get("role") == "tool":
                # A turn context update after an apply's round follows its results.
                if not isinstance(content, str):
                    raise RuntimeError("a turn context message must be text")
                translated.append({"role": "user", "content": content})
            else:
                _with_leading_context(translated[-1], content)
        else:
            translated.append({"role": role, "content": content})
        previous_role = role
    return translated


def _openai_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Wire tools as Chat Completions functions, `strict` beside the parameters."""

    functions: list[dict[str, Any]] = []
    for tool in tools:
        function: dict[str, Any] = {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        }
        if "strict" in tool:
            function["strict"] = tool["strict"]
        functions.append({"type": "function", "function": function})
    return functions


class OpenAIProvider:
    """Normalize the OpenAI Chat Completions streaming API.

    It sends the canonical tool projection, the closed read tools in strict
    mode, and omits the `null` a strict tool's optional property comes back as.
    """

    provider_name = "openai"
    #: Adapter-level pre-stream retry delays. Empty here: the direct OpenAI
    #: client keeps the SDK's own bounded request retries instead.
    pre_stream_retry_delays: tuple[float, ...] = ()
    #: The lane's projection of the tool schemas, and whose strict rules its
    #: strict tools follow (None: no tool is sent strict).
    tool_projection: ToolProjection = "canonical"
    strict_dialect: StrictDialect | None = "openai"

    def __init__(self, config: AssistantConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = _load_openai_client(config, self.provider_name) if client is None else client

    def _wire_tools(self, tools: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
        """The tools as this lane sends them."""

        if self.tool_projection == "compatible":
            return _compatible_tools(tools)
        return _canonical_tools(tools, self.strict_dialect)

    def _normalise_tool_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        input_schemas: Mapping[str, Mapping[str, object]],
        strict_tools: frozenset[str],
    ) -> dict[str, Any]:
        """Apply provider-specific wire normalisation before tool validation."""

        if name not in strict_tools:
            return arguments
        return dict(_without_strict_nulls(arguments, input_schemas[name]))

    async def _create_stream(self, request: Mapping[str, Any]) -> Any:
        """Open the response stream, retrying only failures raised before it exists."""

        delays = self.pre_stream_retry_delays
        for retry_index in range(len(delays) + 1):
            try:
                return await self.client.chat.completions.create(**request)
            except Exception as exc:
                category = _failure_category(exc)
                if retry_index >= len(delays) or category not in _PRE_STREAM_RETRYABLE_CATEGORIES:
                    raise
                delay = delays[retry_index]
                logger.warning(
                    "assistant_provider_request_retry",
                    provider=self.provider_name,
                    failure_class=category,
                    error_class=type(exc).__name__,
                    retry=retry_index + 1,
                    max_retries=len(delays),
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable provider retry state")

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None
        emitted_tools = False
        saw_usage = False
        saw_text = False
        calls: dict[int, dict[str, Any]] = {}
        stream: Any | None = None
        wire_tools = self._wire_tools(tools)
        strict_tools = frozenset(str(tool["name"]) for tool in wire_tools if "strict" in tool)
        input_schemas = _tool_input_schemas(tools)

        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": _openai_messages(system, messages),
            "tools": _openai_tools(wire_tools),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.config.base_url is None:
            request["max_completion_tokens"] = self.config.max_output_tokens
        else:
            request["max_tokens"] = self.config.max_output_tokens

        try:
            stream = await self._create_stream(request)
            async for chunk in stream:
                logger.debug(
                    "assistant_openai_chunk_shape",
                    provider=self.provider_name,
                    shape=_chunk_shape(chunk),
                )
                usage = _attr(chunk, "usage")
                if usage is not None:
                    saw_usage = True
                    input_tokens = _usage_value(
                        _attr(usage, "prompt_tokens"), self.provider_name, "prompt_tokens"
                    )
                    output_tokens = _usage_value(
                        _attr(usage, "completion_tokens"), self.provider_name, "completion_tokens"
                    )

                raw_choices = _attr(chunk, "choices", [])
                if raw_choices is None:
                    choices: Sequence[object] = ()
                elif isinstance(raw_choices, Sequence) and not isinstance(
                    raw_choices, (str, bytes)
                ):
                    choices = raw_choices
                else:
                    raise _provider_error(
                        self.provider_name,
                        "malformed_stream",
                        "choices is not a sequence",
                    )
                for choice in choices:
                    delta = _attr(choice, "delta")
                    content = _attr(delta, "content")
                    if content is not None:
                        for text in _iter_content_text(content, self.provider_name):
                            saw_text = True
                            yield TextDelta(text=text)

                    raw_tool_calls = _attr(delta, "tool_calls", [])
                    if raw_tool_calls is None:
                        tool_calls: Sequence[object] = ()
                    elif isinstance(raw_tool_calls, Sequence) and not isinstance(
                        raw_tool_calls, (str, bytes)
                    ):
                        tool_calls = raw_tool_calls
                    else:
                        raise _provider_error(
                            self.provider_name,
                            "malformed_stream",
                            "tool calls is not a sequence",
                        )
                    for fragment in tool_calls:
                        index = _attr(fragment, "index")
                        if not isinstance(index, int):
                            raise _provider_error(
                                self.provider_name,
                                "malformed_stream",
                                "tool fragment has no index",
                            )
                        call = calls.setdefault(
                            index,
                            {"id": None, "name": None, "arguments": []},
                        )
                        call_id = _attr(fragment, "id")
                        if call_id is not None:
                            call["id"] = call_id
                        function = _attr(fragment, "function")
                        name = _attr(function, "name")
                        if name is not None:
                            call["name"] = name
                        arguments = _attr(function, "arguments")
                        if arguments is not None:
                            if not isinstance(arguments, str):
                                raise _provider_error(
                                    self.provider_name,
                                    "malformed_stream",
                                    "tool fragment is not text",
                                )
                            call["arguments"].append(arguments)

                    choice_reason = _attr(choice, "finish_reason")
                    if choice_reason is not None:
                        if finish_reason is not None and choice_reason != finish_reason:
                            raise _provider_error(
                                self.provider_name,
                                "malformed_stream",
                                "multiple finish reasons",
                            )
                        finish_reason = str(choice_reason)
                        # Some OpenAI-compatible gateways finish a tool-calling
                        # reply with `stop`; its accumulated calls still run.
                        calls_finished = finish_reason in {"tool_calls", "function_call"} or (
                            finish_reason == "stop" and bool(calls)
                        )
                        if calls_finished and not emitted_tools:
                            for call in calls.values():
                                call_id = call["id"]
                                name = call["name"]
                                if not isinstance(call_id, str) or not isinstance(name, str):
                                    raise _provider_error(
                                        self.provider_name,
                                        "malformed_stream",
                                        "tool call is missing its id or name",
                                    )
                                arguments = _parse_tool_arguments(
                                    self.provider_name, "".join(call["arguments"])
                                )
                                arguments = self._normalise_tool_arguments(
                                    name,
                                    arguments,
                                    input_schemas,
                                    strict_tools,
                                )
                                yield ToolCallRequest(call_id, name, arguments)
                            emitted_tools = True
                        elif not calls_finished and finish_reason not in {
                            "stop",
                            "length",
                            "content_filter",
                        }:
                            raise _provider_error(
                                self.provider_name,
                                "malformed_stream",
                                "unsupported finish reason",
                            )

            if finish_reason is None:
                # Databricks intermittently omits finish_reason from a complete
                # reply's final text chunk (captured live 2026-07-19). Accept a
                # clean stream end as a natural stop ONLY when nothing suggests
                # a broken or truncated stream: no half-delivered tool call, a
                # text reply actually arrived, and per-chunk usage confirms the
                # output stayed under the budget. Every other shape fails loud.
                if calls and not emitted_tools:
                    raise _provider_error(
                        self.provider_name,
                        "malformed_stream",
                        "stream ended mid tool call",
                    )
                if not saw_usage or not saw_text:
                    raise _provider_error(
                        self.provider_name,
                        "malformed_stream",
                        "stream ended without a finish reason",
                    )
                if output_tokens >= self.config.max_output_tokens:
                    raise _provider_error(
                        self.provider_name,
                        "truncated",
                        "stream ended at the output token budget without a finish reason",
                    )
                logger.warning(
                    "assistant_openai_stream_missing_finish",
                    provider=self.provider_name,
                    output_tokens=output_tokens,
                    budget=self.config.max_output_tokens,
                )
                finish_reason = "stop"
            # length / content_filter raise typed truncated/filtered failures
            # here rather than masquerading as a natural end.
            yield TurnStop(
                "tool_use"
                if emitted_tools
                else _map_stop_reason(self.provider_name, finish_reason),
                ProviderUsage(input_tokens, output_tokens),
            )
        except AssistantProviderError:
            raise
        except Exception as exc:
            raise _classify_sdk_error(self.provider_name, exc) from exc
        finally:
            close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
            if callable(close):
                result = close()
                if isawaitable(result):
                    await result


class DatabricksProvider(OpenAIProvider):
    """Databricks identity over its OpenAI-compatible Chat Completions API.

    The lane sends the compatible tool projection, on which its live baselines
    were measured, unless built with ``tool_projection="canonical"``, which only
    the evaluation's `canonical_tools` variant does. It never sends a tool
    strict, and it decodes arguments by the canonical schema whichever
    projection it sent.
    """

    provider_name = "databricks"
    pre_stream_retry_delays = (1.0, 3.0)
    strict_dialect = None

    def __init__(
        self,
        config: AssistantConfig,
        client: Any | None = None,
        *,
        tool_projection: ToolProjection = "compatible",
    ) -> None:
        super().__init__(config, client)
        self.tool_projection = tool_projection

    def _normalise_tool_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        input_schemas: Mapping[str, Mapping[str, object]],
        strict_tools: frozenset[str],
    ) -> dict[str, Any]:
        schema = input_schemas.get(name)
        if schema is None:
            return arguments
        return _normalise_databricks_tool_arguments(arguments, schema)


def create_provider(config: AssistantConfig) -> AssistantProvider:
    """Construct the configured production adapter at one shared seam."""

    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "openai":
        return OpenAIProvider(config)
    if config.provider == "databricks":
        return DatabricksProvider(config)
    raise ConfigError(f"Unknown assistant provider: {config.provider!r}.")


__all__ = [
    "ANTHROPIC_EFFORT",
    "AnthropicProvider",
    "AssistantProvider",
    "AssistantProviderError",
    "DatabricksProvider",
    "MID_CONVERSATION_SYSTEM_MODELS",
    "create_provider",
    "OpenAIProvider",
    "ProviderEvent",
    "ProviderUsage",
    "ReplayContent",
    "TextDelta",
    "ThinkingStarted",
    "ToolCallRequest",
    "ToolProjection",
    "TurnStop",
]
