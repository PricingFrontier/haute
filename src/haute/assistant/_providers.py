"""Provider-neutral streaming adapters for the assistant agent loop.

The optional provider SDKs are deliberately imported only when an adapter has
to construct its production client.  The rest of Haute can therefore import
the assistant package without requiring either SDK, while tests and callers can
inject a client at the adapter seam.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Literal, Protocol, TypeAlias

from haute._logging import get_logger
from haute.assistant._config import AssistantConfig
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


ProviderEvent: TypeAlias = TextDelta | ToolCallRequest | TurnStop


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


def _is_rate_limit_error(error: Exception) -> bool:
    return "rate" in type(error).__name__.lower()


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
    class_name = type(error).__name__.lower()
    if "auth" in class_name or "permission" in class_name:
        category = "authentication"
    elif "rate" in class_name or "ratelimit" in class_name:
        category = "rate_limit"
    elif any(word in class_name for word in ("connection", "timeout", "network")):
        category = "connection"
    elif "status" in class_name or class_name in {
        "badrequesterror",
        "internalservererror",
        "apierror",
    }:
        category = "status"
    else:
        category = "stream"
    return _provider_error(provider, category, "the provider request could not be completed")


def _usage_value(value: object, provider: str, field: str) -> int:
    if value is None:
        return 0
    if type(value) is not int:
        raise _provider_error(provider, "malformed_stream", f"invalid {field} usage")
    result = value
    if result < 0:
        raise _provider_error(provider, "malformed_stream", f"invalid {field} usage")
    return result


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


def _portable_schema_type(schema: Mapping[str, object]) -> str | None:
    """Return a provider-portable declared type for one schema fragment."""

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
            branch_type = _portable_schema_type(branch)
            if branch_type is None and isinstance(branch.get("properties"), Mapping):
                branch_type = "object"
            if branch_type is None:
                return None
            branch_types.append(branch_type)
        if branch_types:
            return branch_types[0] if len(set(branch_types)) == 1 else None
    return None


def _portable_allowed_values(schema: Mapping[str, object]) -> tuple[object, ...] | None:
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


def _portable_property_schema(
    schemas: Sequence[Mapping[str, object]],
    budget: _SchemaBudget,
) -> dict[str, object]:
    if schemas and all(schema == schemas[0] for schema in schemas[1:]):
        return _portable_tool_schema(schemas[0], budget)

    allowed = [_portable_allowed_values(schema) for schema in schemas]
    if allowed and all(values is not None for values in allowed):
        combined: list[object] = []
        for values in allowed:
            assert values is not None
            for value in values:
                if value not in combined:
                    combined.append(value)
        return {"enum": combined}

    types = [_portable_schema_type(schema) for schema in schemas]
    if types and types[0] is not None and all(value == types[0] for value in types):
        projected: dict[str, object] = {"type": types[0]}
        if types[0] == "array":
            item_schemas = [schema.get("items") for schema in schemas]
            if all(isinstance(items, Mapping) for items in item_schemas):
                projected["items"] = _portable_tool_schema(
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


def _portable_required_names(schema: Mapping[str, object]) -> tuple[str, ...]:
    required = schema.get("required")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return ()
    return tuple(item for item in required if isinstance(item, str))


def _portable_composed_object(
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
            _portable_schema_type(branch) != "object"
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
        projected_properties[name] = _portable_property_schema(property_schemas, budget)

    branch_required = [_portable_required_names(branch) for branch in branches]
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


def _portable_tool_schema(
    schema: Mapping[str, object],
    budget: _SchemaBudget | None = None,
) -> dict[str, object]:
    """Project canonical validation schema onto one bounded provider wire subset."""

    if budget is None:
        budget = _SchemaBudget()
    composed = _portable_composed_object(schema, budget)
    if composed is not None:
        return composed
    projected: dict[str, object] = {}
    projected_type = _portable_schema_type(schema)
    if projected_type is not None:
        projected["type"] = projected_type

    description = schema.get("description")
    if isinstance(description, str):
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
            str(name): _portable_tool_schema(value, budget)
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
        projected["items"] = _portable_tool_schema(items, budget)

    if schema.get("additionalProperties") is False:
        projected["additionalProperties"] = False
    return projected


def _portable_tools(
    tools: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    """Return one common, conservative tool contract for every provider wire API."""

    projected: list[dict[str, object]] = []
    for tool in tools:
        name = tool.get("name")
        schema = tool.get("input_schema")
        if not isinstance(name, str) or not isinstance(schema, Mapping):
            raise TypeError("Tool definitions require a string name and mapping input_schema")
        description = tool.get("description", "")
        projected.append(
            {
                "name": name,
                "description": description if isinstance(description, str) else "",
                "input_schema": _portable_tool_schema(schema),
            }
        )
    return projected


def _normalise_databricks_tool_arguments(
    arguments: Mapping[str, Any],
    schema: Mapping[str, object],
) -> dict[str, Any]:
    """Decode Databricks' stringified top-level JSON values by schema.

    Databricks-hosted Qwen models have been observed to return a valid outer
    function-arguments object while encoding container and scalar properties as
    JSON strings. Only fields whose canonical schema exclusively declares a
    compatible JSON type are eligible. Strings, nulls, nested values, ambiguous
    schemas, and non-finite numbers are left untouched. Invalid or wrong-type
    encodings remain strings so canonical tool validation can reject them as
    recoverable invalid input.
    """

    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(arguments)
    normalised = dict(arguments)
    for field, value in arguments.items():
        field_schema = properties.get(field)
        if not isinstance(value, str) or not isinstance(field_schema, Mapping):
            continue
        expected = _declared_compatible_types(field_schema)
        if not expected:
            continue
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
                field=field,
                declared_types=sorted(expected),
                encoded_length=len(value),
                looks_like_json_container=value.lstrip()[:1] in {"[", "{"},
            )
            continue
        if _matches_declared_json_type(decoded, expected):
            normalised[field] = decoded
        else:
            logger.warning(
                "assistant_databricks_argument_decoded_wrong_type",
                field=field,
                declared_types=sorted(expected),
                decoded_type=type(decoded).__name__,
            )
    return normalised


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
        import openai
    except (ImportError, ModuleNotFoundError) as exc:
        raise _provider_error(provider, "dependency", "the openai SDK is not installed") from exc
    kwargs: dict[str, Any] = {"api_key": config.api_key}
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


def _anthropic_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Translate neutral history into Anthropic Messages content blocks."""

    translated: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant" and message.get("tool_calls"):
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
        elif role == "tool":
            translated.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message["tool_call_id"],
                            "content": _json_string(content),
                            "is_error": bool(message.get("is_error", False)),
                        }
                    ],
                }
            )
        elif role == "controller":
            translated.append({"role": "user", "content": content})
        else:
            translated.append({"role": role, "content": content})
    return translated


class AnthropicProvider:
    """Normalize the Anthropic Messages streaming API."""

    def __init__(self, config: AssistantConfig, client: Any | None = None) -> None:
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
        wire_tools = _portable_tools(tools)

        try:
            stream = self.client.messages.stream(
                model=self.config.model,
                system=system,
                messages=_anthropic_messages(messages),
                tools=wire_tools,
                max_tokens=self.config.max_output_tokens,
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
                        if _attr(block, "type") == "tool_use":
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
                                yield TextDelta(text=text)
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
                        if stop_reason is None:
                            raise _provider_error(
                                "anthropic", "malformed_stream", "message has no stop reason"
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
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant" and message.get("tool_calls"):
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
        else:
            translated.append({"role": role, "content": content})
    return translated


def _openai_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


class OpenAIProvider:
    """Normalize the OpenAI Chat Completions streaming API."""

    provider_name = "openai"
    rate_limit_retry_delays: tuple[float, ...] = ()

    def __init__(self, config: AssistantConfig, client: Any | None = None) -> None:
        self.config = config
        self.client = _load_openai_client(config, self.provider_name) if client is None else client

    def _normalise_tool_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        input_schemas: Mapping[str, Mapping[str, object]],
    ) -> dict[str, Any]:
        """Apply provider-specific wire normalisation before tool validation."""

        return arguments

    async def _create_stream(self, request: Mapping[str, Any]) -> Any:
        for retry_index in range(len(self.rate_limit_retry_delays) + 1):
            try:
                return await self.client.chat.completions.create(**request)
            except Exception as exc:
                if retry_index >= len(self.rate_limit_retry_delays) or not _is_rate_limit_error(
                    exc
                ):
                    raise
                delay = self.rate_limit_retry_delays[retry_index]
                logger.warning(
                    "assistant_provider_request_retry",
                    provider=self.provider_name,
                    failure_class="rate_limit",
                    retry=retry_index + 1,
                    max_retries=len(self.rate_limit_retry_delays),
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
        wire_tools = _portable_tools(tools)
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
                        if finish_reason in {"tool_calls", "function_call"} and not emitted_tools:
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
                                )
                                yield ToolCallRequest(call_id, name, arguments)
                            emitted_tools = True
                        elif finish_reason not in {"stop", "length", "content_filter"}:
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
                _map_stop_reason(self.provider_name, finish_reason),
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
    """Databricks identity over its OpenAI-compatible Chat Completions API."""

    provider_name = "databricks"
    rate_limit_retry_delays = (1.0, 3.0)

    def _normalise_tool_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
        input_schemas: Mapping[str, Mapping[str, object]],
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
    "AnthropicProvider",
    "AssistantProvider",
    "AssistantProviderError",
    "DatabricksProvider",
    "create_provider",
    "OpenAIProvider",
    "ProviderEvent",
    "ProviderUsage",
    "TextDelta",
    "ToolCallRequest",
    "TurnStop",
]
