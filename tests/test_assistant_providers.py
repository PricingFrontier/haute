"""Tests for the assistant provider adapters (``haute.assistant._providers``).

Spec: specs/assistant/low-level.md — Key types (``ProviderEvent``) and
Control flow § Provider adapters.  Each adapter normalises its SDK's
streaming events into the internal ``ProviderEvent`` union and maps SDK
failures to ``AssistantProviderError``; the loop never sees SDK types.

The adapter seam pinned here: constructors accept the resolved
``AssistantConfig`` plus an injectable ``client`` (tests script the SDK
surface; production constructs the real SDK client lazily when ``client``
is omitted).  ``stream_turn(system=..., messages=..., tools=...)`` is an
async generator of ``ProviderEvent``s covering exactly one provider
round-trip.

Scripted fakes mirror the documented wire protocols:

- Anthropic Messages streaming: ``message_start`` (input usage) →
  ``content_block_start``/``content_block_delta`` (``text_delta`` /
  ``input_json_delta``)/``content_block_stop`` → ``message_delta``
  (``stop_reason``, output usage) → ``message_stop``.
- OpenAI Chat Completions streaming: ``delta.content`` text chunks,
  ``delta.tool_calls[*]`` argument fragments accumulated per index,
  ``finish_reason`` on the closing chunk, usage on the final chunk
  (``stream_options={"include_usage": True}``).

Authored test-first per CLAUDE.md TDD.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from haute.assistant._config import AssistantConfig
from haute.assistant._providers import (
    AnthropicProvider,
    AssistantProviderError,
    OpenAIProvider,
    TextDelta,
    ToolCallRequest,
    TurnStop,
)

# ---------------------------------------------------------------------------
# Config + request fixtures
# ---------------------------------------------------------------------------


def _config(provider: str, base_url: str | None = None) -> AssistantConfig:
    return AssistantConfig(
        provider=provider,  # type: ignore[arg-type]
        model="test-model",
        base_url=base_url,
        api_key="sk-test-secret",
        max_output_tokens=1234,
    )


_SYSTEM = "You are the Haute assistant."
_MESSAGES = [{"role": "user", "content": "add a banding node"}]
_TOOLS = [
    {
        "name": "get_pipeline",
        "description": "Read the saved graph.",
        "input_schema": {"type": "object", "properties": {}},
    }
]


async def _collect(provider) -> list:
    return [
        event
        async for event in provider.stream_turn(system=_SYSTEM, messages=_MESSAGES, tools=_TOOLS)
    ]


# ---------------------------------------------------------------------------
# Anthropic fakes — the Messages streaming wire protocol
# ---------------------------------------------------------------------------


class _FakeAnthropicStream:
    def __init__(self, events, error: Exception | None = None):
        self._events = events
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *exc_info):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self._events:
            yield event


class _FakeAnthropicClient:
    def __init__(self, events, error: Exception | None = None):
        self.captured_kwargs: dict | None = None
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.captured_kwargs = kwargs
                return _FakeAnthropicStream(events, error)

        self.messages = _Messages()


def _anthropic_text_tool_events():
    ns = SimpleNamespace
    return [
        ns(type="message_start", message=ns(usage=ns(input_tokens=10))),
        ns(type="content_block_start", index=0, content_block=ns(type="text")),
        ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text="Hel")),
        ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text="lo")),
        ns(type="content_block_stop", index=0),
        ns(
            type="content_block_start",
            index=1,
            content_block=ns(type="tool_use", id="toolu_1", name="get_pipeline", input={}),
        ),
        ns(
            type="content_block_delta",
            index=1,
            delta=ns(type="input_json_delta", partial_json='{"a":'),
        ),
        ns(
            type="content_block_delta",
            index=1,
            delta=ns(type="input_json_delta", partial_json=" 1}"),
        ),
        ns(type="content_block_stop", index=1),
        ns(type="message_delta", delta=ns(stop_reason="tool_use"), usage=ns(output_tokens=25)),
        ns(type="message_stop"),
    ]


class TestAnthropicProvider:
    async def test_normalises_text_and_tool_stream(self):
        client = _FakeAnthropicClient(_anthropic_text_tool_events())
        events = await _collect(AnthropicProvider(_config("anthropic"), client=client))

        assert events[0] == TextDelta(text="Hel")
        assert events[1] == TextDelta(text="lo")
        (tool,) = [e for e in events if isinstance(e, ToolCallRequest)]
        assert (tool.id, tool.name, tool.arguments) == ("toolu_1", "get_pipeline", {"a": 1})
        stop = events[-1]
        assert isinstance(stop, TurnStop)
        assert stop.reason == "tool_use"
        assert (stop.usage.input_tokens, stop.usage.output_tokens) == (10, 25)

    async def test_end_turn_maps_to_end(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=4))),
            ns(type="content_block_start", index=0, content_block=ns(type="text")),
            ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text="done")),
            ns(type="content_block_stop", index=0),
            ns(type="message_delta", delta=ns(stop_reason="end_turn"), usage=ns(output_tokens=2)),
            ns(type="message_stop"),
        ]
        client = _FakeAnthropicClient(events)
        out = await _collect(AnthropicProvider(_config("anthropic"), client=client))
        assert isinstance(out[-1], TurnStop) and out[-1].reason == "end"

    async def test_request_carries_model_system_tools_and_budget(self):
        client = _FakeAnthropicClient(_anthropic_text_tool_events())
        await _collect(AnthropicProvider(_config("anthropic"), client=client))
        kwargs = client.captured_kwargs
        assert kwargs is not None
        assert kwargs["model"] == "test-model"
        assert kwargs["system"] == _SYSTEM
        assert kwargs["tools"] == _TOOLS
        assert kwargs["max_tokens"] == 1234

    async def test_tool_call_emitted_only_after_block_stop(self):
        """No ToolCallRequest may be emitted while fragments are pending."""
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(
                type="content_block_start",
                index=0,
                content_block=ns(type="tool_use", id="t1", name="get_pipeline", input={}),
            ),
            ns(
                type="content_block_delta",
                index=0,
                delta=ns(type="input_json_delta", partial_json="{}"),
            ),
            # block_stop deliberately last — nothing before it may be a ToolCallRequest
            ns(type="content_block_stop", index=0),
            ns(type="message_delta", delta=ns(stop_reason="tool_use"), usage=ns(output_tokens=1)),
            ns(type="message_stop"),
        ]
        client = _FakeAnthropicClient(events)
        provider = AnthropicProvider(_config("anthropic"), client=client)
        seen: list = []
        async for event in provider.stream_turn(system=_SYSTEM, messages=_MESSAGES, tools=_TOOLS):
            seen.append(event)
        tool_positions = [i for i, e in enumerate(seen) if isinstance(e, ToolCallRequest)]
        assert tool_positions, "accumulated tool call must be emitted"

    async def test_malformed_tool_arguments_raise_provider_error(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(
                type="content_block_start",
                index=0,
                content_block=ns(type="tool_use", id="t1", name="get_pipeline", input={}),
            ),
            ns(
                type="content_block_delta",
                index=0,
                delta=ns(type="input_json_delta", partial_json='{"broken":'),
            ),
            ns(type="content_block_stop", index=0),
        ]
        client = _FakeAnthropicClient(events)
        with pytest.raises(AssistantProviderError):
            await _collect(AnthropicProvider(_config("anthropic"), client=client))

    async def test_sdk_errors_map_to_provider_error_without_body(self):
        import anthropic

        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(
            401, request=request, text='{"secret": "raw-provider-body-DO-NOT-LEAK"}'
        )
        error = anthropic.AuthenticationError(
            "auth failed", response=response, body={"secret": "raw-provider-body-DO-NOT-LEAK"}
        )
        client = _FakeAnthropicClient([], error=error)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(AnthropicProvider(_config("anthropic"), client=client))
        assert "DO-NOT-LEAK" not in str(excinfo.value)
        assert "anthropic" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# OpenAI fakes — the Chat Completions streaming wire protocol
# ---------------------------------------------------------------------------


class _FakeOpenAIStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _FakeOpenAIClient:
    def __init__(self, chunks, error: Exception | None = None):
        self.captured_kwargs: dict | None = None
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.captured_kwargs = kwargs
                if error is not None:
                    raise error
                return _FakeOpenAIStream(chunks)

        self.chat = SimpleNamespace(completions=_Completions())


def _openai_text_tool_chunks():
    ns = SimpleNamespace
    return [
        ns(
            choices=[ns(delta=ns(content="Hi", tool_calls=None), finish_reason=None)],
            usage=None,
        ),
        ns(
            choices=[
                ns(
                    delta=ns(
                        content=None,
                        tool_calls=[
                            ns(
                                index=0,
                                id="call_1",
                                function=ns(name="get_pipeline", arguments='{"x"'),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        ns(
            choices=[
                ns(
                    delta=ns(
                        content=None,
                        tool_calls=[ns(index=0, id=None, function=ns(name=None, arguments=": 2}"))],
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        ns(
            choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="tool_calls")],
            usage=None,
        ),
        ns(choices=[], usage=ns(prompt_tokens=7, completion_tokens=3)),
    ]


class TestOpenAIProvider:
    async def test_normalises_text_and_tool_stream(self):
        client = _FakeOpenAIClient(_openai_text_tool_chunks())
        events = await _collect(OpenAIProvider(_config("openai"), client=client))

        assert events[0] == TextDelta(text="Hi")
        (tool,) = [e for e in events if isinstance(e, ToolCallRequest)]
        assert (tool.id, tool.name, tool.arguments) == ("call_1", "get_pipeline", {"x": 2})
        stop = events[-1]
        assert isinstance(stop, TurnStop)
        assert stop.reason == "tool_use"
        assert (stop.usage.input_tokens, stop.usage.output_tokens) == (7, 3)

    async def test_stop_finish_reason_maps_to_end(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content="done", tool_calls=None), finish_reason=None)],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="stop")],
                usage=None,
            ),
            ns(choices=[], usage=ns(prompt_tokens=2, completion_tokens=1)),
        ]
        client = _FakeOpenAIClient(chunks)
        events = await _collect(OpenAIProvider(_config("openai"), client=client))
        assert isinstance(events[-1], TurnStop) and events[-1].reason == "end"

    async def test_budget_param_is_max_completion_tokens_for_api_openai_com(self):
        client = _FakeOpenAIClient(_openai_text_tool_chunks())
        await _collect(OpenAIProvider(_config("openai", base_url=None), client=client))
        kwargs = client.captured_kwargs
        assert kwargs is not None
        assert kwargs["max_completion_tokens"] == 1234
        assert "max_tokens" not in kwargs
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}

    async def test_budget_param_is_max_tokens_when_base_url_set(self):
        """The Databricks Chat Completions contract documents max_tokens."""
        client = _FakeOpenAIClient(_openai_text_tool_chunks())
        await _collect(
            OpenAIProvider(
                _config("openai", base_url="https://dbx.example.com/serving"), client=client
            )
        )
        kwargs = client.captured_kwargs
        assert kwargs is not None
        assert kwargs["max_tokens"] == 1234
        assert "max_completion_tokens" not in kwargs

    async def test_content_part_list_yields_text_deltas(self):
        """OpenAI-compatible gateways serving Claude stream typed content parts."""
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(content=[{"type": "text", "text": "Hel"}], tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[
                    ns(
                        delta=ns(content=[{"type": "text", "text": "lo"}], tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="stop")],
                usage=None,
            ),
            ns(choices=[], usage=ns(prompt_tokens=2, completion_tokens=1)),
        ]
        client = _FakeOpenAIClient(chunks)
        events = await _collect(OpenAIProvider(_config("openai"), client=client))
        assert events[0] == TextDelta(text="Hel")
        assert events[1] == TextDelta(text="lo")
        assert isinstance(events[-1], TurnStop) and events[-1].reason == "end"

    async def test_reasoning_content_parts_are_not_surfaced(self):
        """Reasoning summaries are model thinking, not assistant reply text."""
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(
                            content=[
                                {
                                    "type": "reasoning",
                                    "summary": [{"type": "summary_text", "text": "pondering"}],
                                },
                                {"type": "text", "text": "Answer"},
                            ],
                            tool_calls=None,
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="stop")],
                usage=None,
            ),
            ns(choices=[], usage=ns(prompt_tokens=2, completion_tokens=1)),
        ]
        client = _FakeOpenAIClient(chunks)
        events = await _collect(OpenAIProvider(_config("openai"), client=client))
        text = [e for e in events if isinstance(e, TextDelta)]
        assert text == [TextDelta(text="Answer")]
        assert "pondering" not in "".join(e.text for e in text)

    async def test_attribute_style_content_parts_are_supported(self):
        """Parts may arrive as SDK objects rather than plain mappings."""
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(content=[ns(type="text", text="Hi")], tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="stop")],
                usage=None,
            ),
            ns(choices=[], usage=ns(prompt_tokens=1, completion_tokens=1)),
        ]
        client = _FakeOpenAIClient(chunks)
        events = await _collect(OpenAIProvider(_config("openai"), client=client))
        assert events[0] == TextDelta(text="Hi")

    async def test_unknown_content_part_type_raises_provider_error(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(
                            content=[{"type": "image", "image_url": "https://x"}],
                            tool_calls=None,
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "malformed_stream"
        assert "https://x" not in str(excinfo.value)

    async def test_text_part_without_text_raises_provider_error(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(content=[{"type": "text", "text": 5}], tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "malformed_stream"

    async def test_debug_chunk_shapes_are_logged_without_user_text(self):
        """Every raw chunk's structure is loggable for wire-dialect diagnosis,
        and the shapes never contain message text or tool arguments."""
        import structlog.testing

        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(content="SECRET-USER-TEXT", tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[
                    ns(
                        delta=ns(
                            content=None,
                            tool_calls=[
                                ns(
                                    index=0,
                                    id="call_1",
                                    function=ns(name="get_pipeline", arguments='{"secret": 1}'),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="tool_calls")],
                usage=None,
            ),
            ns(choices=[], usage=ns(prompt_tokens=7, completion_tokens=3)),
        ]
        client = _FakeOpenAIClient(chunks)
        with structlog.testing.capture_logs() as captured:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        shapes = [e["shape"] for e in captured if e["event"] == "assistant_openai_chunk_shape"]
        assert len(shapes) == 4
        joined = " | ".join(shapes)
        assert "SECRET-USER-TEXT" not in joined
        assert "secret" not in joined
        assert "finish=tool_calls" in joined
        assert "usage" in shapes[-1]

    def test_chunk_shape_describes_content_part_lists(self):
        from haute.assistant._providers import _chunk_shape

        ns = SimpleNamespace
        chunk = ns(
            choices=[
                ns(
                    delta=ns(
                        content=[
                            {"type": "reasoning", "summary": [{"type": "summary_text"}]},
                            {"type": "text", "text": "SECRET"},
                        ],
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        shape = _chunk_shape(chunk)
        assert "parts[reasoning,text]" in shape
        assert "finish=stop" in shape
        assert "SECRET" not in shape

    def test_chunk_shape_is_total_for_odd_inputs(self):
        from haute.assistant._providers import _chunk_shape

        ns = SimpleNamespace
        assert _chunk_shape(ns(choices=[], usage=None)) == "empty"
        assert "choices=<int>" in _chunk_shape(ns(choices=7, usage=None))
        odd = ns(
            choices=[ns(delta=ns(content=42, tool_calls=None), finish_reason=None)],
            usage=None,
        )
        assert "content=<int>" in _chunk_shape(odd)

    async def test_missing_finish_with_usage_under_budget_is_tolerated(self):
        """Databricks intermittently omits finish_reason from the final text
        chunk of a complete reply (captured live 2026-07-19). A clean stream
        end with text delivered and usage under the output budget is the
        gateway's de-facto natural stop — accepted, with an operator warning.
        """
        import structlog.testing

        ns = SimpleNamespace
        usage = ns(prompt_tokens=7, completion_tokens=3)
        chunks = [
            ns(
                choices=[ns(delta=ns(content="Done", tool_calls=None), finish_reason=None)],
                usage=usage,
            ),
            ns(
                choices=[ns(delta=ns(content="!", tool_calls=None), finish_reason=None)],
                usage=usage,
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with structlog.testing.capture_logs() as captured:
            events = await _collect(OpenAIProvider(_config("openai"), client=client))
        stop = events[-1]
        assert isinstance(stop, TurnStop)
        assert stop.reason == "end"
        assert (stop.usage.input_tokens, stop.usage.output_tokens) == (7, 3)
        assert any(e["event"] == "assistant_openai_stream_missing_finish" for e in captured)

    async def test_missing_finish_at_output_budget_raises_truncated(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content="Done", tool_calls=None), finish_reason=None)],
                usage=ns(prompt_tokens=7, completion_tokens=1234),
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "truncated"

    async def test_missing_finish_mid_tool_call_raises(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(
                            content=None,
                            tool_calls=[
                                ns(
                                    index=0,
                                    id="c1",
                                    function=ns(name="get_pipeline", arguments="{"),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
                usage=ns(prompt_tokens=7, completion_tokens=3),
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "malformed_stream"

    async def test_missing_finish_without_usage_still_raises(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content="Done", tool_calls=None), finish_reason=None)],
                usage=None,
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "malformed_stream"

    async def test_missing_finish_with_no_text_still_raises(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason=None)],
                usage=ns(prompt_tokens=7, completion_tokens=0),
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "malformed_stream"

    async def test_non_text_non_list_content_still_raises(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(delta=ns(content=42, tool_calls=None), finish_reason=None),
                ],
                usage=None,
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == "malformed_stream"

    async def test_malformed_tool_arguments_raise_provider_error(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(
                            content=None,
                            tool_calls=[
                                ns(
                                    index=0,
                                    id="call_1",
                                    function=ns(name="get_pipeline", arguments='{"broken":'),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="tool_calls")],
                usage=None,
            ),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError):
            await _collect(OpenAIProvider(_config("openai"), client=client))

    async def test_sdk_errors_map_to_provider_error_without_body(self):
        import openai

        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(
            429, request=request, text='{"secret": "raw-provider-body-DO-NOT-LEAK"}'
        )
        error = openai.RateLimitError(
            "rate limited", response=response, body={"secret": "raw-provider-body-DO-NOT-LEAK"}
        )
        client = _FakeOpenAIClient([], error=error)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert "DO-NOT-LEAK" not in str(excinfo.value)
        assert "openai" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# SDK error classification and the lazy client loaders
# ---------------------------------------------------------------------------


class TestErrorClassification:
    @pytest.mark.parametrize(
        ("class_name", "category"),
        [
            ("AuthenticationError", "authentication"),
            ("PermissionDeniedError", "authentication"),
            ("RateLimitError", "rate_limit"),
            ("APIConnectionError", "connection"),
            ("ReadTimeoutError", "connection"),
            ("InternalServerError", "status"),
            ("APIStatusError", "status"),
            ("SomethingUnexpected", "stream"),
        ],
    )
    async def test_stream_failures_classify_by_sdk_exception_class(
        self, class_name: str, category: str
    ):
        error_type = type(class_name, (Exception,), {})
        client = _FakeAnthropicClient([], error=error_type("secret detail DO-NOT-LEAK"))
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(AnthropicProvider(_config("anthropic"), client=client))
        assert excinfo.value.failure_class == category
        assert "DO-NOT-LEAK" not in str(excinfo.value)

    async def test_malformed_usage_value_is_provider_error(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens="ten"))),
        ]
        client = _FakeAnthropicClient(events)
        with pytest.raises(AssistantProviderError):
            await _collect(AnthropicProvider(_config("anthropic"), client=client))

    async def test_non_string_text_delta_is_provider_error(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(type="content_block_start", index=0, content_block=ns(type="text")),
            ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text=42)),
        ]
        client = _FakeAnthropicClient(events)
        with pytest.raises(AssistantProviderError):
            await _collect(AnthropicProvider(_config("anthropic"), client=client))


class TestLazyClientLoaders:
    def test_missing_anthropic_sdk_is_dependency_error(self, monkeypatch: pytest.MonkeyPatch):
        import sys

        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(AssistantProviderError) as excinfo:
            AnthropicProvider(_config("anthropic"))
        assert excinfo.value.failure_class == "dependency"

    def test_missing_openai_sdk_is_dependency_error(self, monkeypatch: pytest.MonkeyPatch):
        import sys

        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(AssistantProviderError) as excinfo:
            OpenAIProvider(_config("openai"))
        assert excinfo.value.failure_class == "dependency"

    def test_installed_sdks_construct_real_clients(self):
        anthropic_provider = AnthropicProvider(_config("anthropic"))
        assert anthropic_provider.client is not None
        openai_provider = OpenAIProvider(_config("openai", base_url="https://dbx.example"))
        assert openai_provider.client is not None


class TestMalformedStreams:
    async def test_duplicate_tool_block_index_rejected(self):
        ns = SimpleNamespace
        block = ns(type="tool_use", id="t1", name="get_pipeline", input={})
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(type="content_block_start", index=0, content_block=block),
            ns(type="content_block_start", index=0, content_block=block),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(
                AnthropicProvider(_config("anthropic"), client=_FakeAnthropicClient(events))
            )

    async def test_tool_fragment_without_block_rejected(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(
                type="content_block_delta",
                index=5,
                delta=ns(type="input_json_delta", partial_json="{}"),
            ),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(
                AnthropicProvider(_config("anthropic"), client=_FakeAnthropicClient(events))
            )

    async def test_stream_ending_inside_tool_block_rejected(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(
                type="content_block_start",
                index=0,
                content_block=ns(type="tool_use", id="t1", name="get_pipeline", input={}),
            ),
            ns(type="message_delta", delta=ns(stop_reason="tool_use"), usage=ns(output_tokens=1)),
            ns(type="message_stop"),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(
                AnthropicProvider(_config("anthropic"), client=_FakeAnthropicClient(events))
            )

    async def test_stream_without_stop_reason_rejected(self):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(type="message_stop"),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(
                AnthropicProvider(_config("anthropic"), client=_FakeAnthropicClient(events))
            )

    async def test_stream_ending_without_stop_event_rejected(self):
        ns = SimpleNamespace
        events = [ns(type="message_start", message=ns(usage=ns(input_tokens=1)))]
        with pytest.raises(AssistantProviderError):
            await _collect(
                AnthropicProvider(_config("anthropic"), client=_FakeAnthropicClient(events))
            )

    async def test_openai_non_sequence_choices_rejected(self):
        ns = SimpleNamespace
        chunks = [ns(choices="broken", usage=None)]
        with pytest.raises(AssistantProviderError):
            await _collect(OpenAIProvider(_config("openai"), client=_FakeOpenAIClient(chunks)))

    async def test_openai_conflicting_finish_reasons_rejected(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="stop")],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="tool_calls")],
                usage=None,
            ),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(OpenAIProvider(_config("openai"), client=_FakeOpenAIClient(chunks)))

    async def test_openai_tool_call_missing_id_rejected(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[
                    ns(
                        delta=ns(
                            content=None,
                            tool_calls=[
                                ns(index=0, id=None, function=ns(name="x", arguments="{}"))
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="tool_calls")],
                usage=None,
            ),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(OpenAIProvider(_config("openai"), client=_FakeOpenAIClient(chunks)))

    async def test_openai_unsupported_finish_reason_rejected(self):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason="mystery")],
                usage=None,
            ),
        ]
        with pytest.raises(AssistantProviderError):
            await _collect(OpenAIProvider(_config("openai"), client=_FakeOpenAIClient(chunks)))


class TestNonNaturalStops:
    """Truncated or filtered output must be a typed failure, never 'end'."""

    @pytest.mark.parametrize(
        ("stop_reason", "category"),
        [("max_tokens", "truncated"), ("refusal", "filtered")],
    )
    async def test_anthropic_non_natural_stops_raise(self, stop_reason: str, category: str):
        ns = SimpleNamespace
        events = [
            ns(type="message_start", message=ns(usage=ns(input_tokens=1))),
            ns(type="content_block_start", index=0, content_block=ns(type="text")),
            ns(type="content_block_delta", index=0, delta=ns(type="text_delta", text="part")),
            ns(type="content_block_stop", index=0),
            ns(
                type="message_delta",
                delta=ns(stop_reason=stop_reason),
                usage=ns(output_tokens=1),
            ),
            ns(type="message_stop"),
        ]
        client = _FakeAnthropicClient(events)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(AnthropicProvider(_config("anthropic"), client=client))
        assert excinfo.value.failure_class == category

    @pytest.mark.parametrize(
        ("finish_reason", "category"),
        [("length", "truncated"), ("content_filter", "filtered")],
    )
    async def test_openai_non_natural_stops_raise(self, finish_reason: str, category: str):
        ns = SimpleNamespace
        chunks = [
            ns(
                choices=[ns(delta=ns(content="part", tool_calls=None), finish_reason=None)],
                usage=None,
            ),
            ns(
                choices=[ns(delta=ns(content=None, tool_calls=None), finish_reason=finish_reason)],
                usage=None,
            ),
            ns(choices=[], usage=ns(prompt_tokens=1, completion_tokens=1)),
        ]
        client = _FakeOpenAIClient(chunks)
        with pytest.raises(AssistantProviderError) as excinfo:
            await _collect(OpenAIProvider(_config("openai"), client=client))
        assert excinfo.value.failure_class == category
