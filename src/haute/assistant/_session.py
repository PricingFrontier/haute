"""Process-local, turn-aware session records for the pricing assistant.

The assistant loop keeps provider-specific wire details at the provider adapter
boundary.  This module owns the small neutral representation that sits
between the loop and those adapters:

* :class:`AssistantMessage` stores a role, JSON content, and optional neutral
  tool-call and tool-result fields.  Assistant tool calls use
  ``tool_calls=[{"id", "name", "arguments"}]`` and their results use
  ``tool_results=[{"tool_call_id", "name", "content", "is_error"}]``.
* :class:`AssistantTurn` groups one user message with all assistant/tool
  messages produced while answering it.  Turns are the indivisible unit for
  both retention policies.
* :class:`SessionStore` owns process-local sessions, their one-turn locks,
  provider history windows, and bounded retention.

Messages and turns have explicit ``as_dict`` methods, so their serialized
form contains only ordinary JSON values.  A session itself is not
serialized wholesale because its ``asyncio.Lock`` is live runtime state;
``AssistantSession.as_dict`` intentionally omits that lock.

The default limits are deliberately fixed in this module: a provider window
contains at most 40 complete messages, stored history retains at most 200
messages where whole turns permit it, and the live-session LRU has capacity
for 32 sessions.  If every existing session is busy, creation temporarily
keeps those busy sessions rather than evicting one; the next creation or
lookup can evict an idle session.  This is the necessary consequence of the
invariant that an active turn is never evicted.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias
from uuid import uuid4

from haute._credential_security import redact_sensitive_text
from haute._logging import get_logger

logger = get_logger(component="assistant.session")

PROVIDER_WINDOW_MESSAGES = 40
"""Maximum number of complete historical messages sent to a provider."""

STORED_HISTORY_MESSAGES = 200
"""Maximum stored historical messages, subject to whole-turn retention."""

MAX_LIVE_SESSIONS = 32
"""Maximum number of idle live sessions retained by the LRU."""

MAX_PERSISTED_SESSIONS = 100
"""Maximum persisted session files; the oldest by last use are pruned at create."""

_SESSION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
"""Session ids are uuid4 hex; anything else never touches the filesystem."""

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]

_MESSAGE_ROLES = frozenset({"user", "assistant", "tool", "controller"})
_MISSING = object()


def _copy_json_value(value: object) -> JSONValue:
    """Validate and copy one JSON value into plain Python containers.

    The records are deliberately strict at their boundary.  Accepting a
    Python object that cannot cross a JSON boundary would make a later
    provider request fail far away from the history mutation that introduced
    it.
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("message values must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        copied: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            copied[key] = _copy_json_value(item)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_json_value(item) for item in value]
    raise TypeError(f"message value is not JSON-serialisable: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class AssistantToolCall:
    """A provider-neutral assistant tool call."""

    id: str
    name: str
    arguments: dict[str, JSONValue]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("tool call id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("tool call name must be a non-empty string")
        copied = _copy_json_value(self.arguments)
        if not isinstance(copied, dict):  # pragma: no cover - guarded by the field contract
            raise TypeError("tool call arguments must be a JSON object")
        object.__setattr__(self, "arguments", copied)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AssistantToolCall:
        """Build a tool call from its neutral JSON object shape."""

        if not isinstance(value, Mapping):
            raise TypeError("tool calls must be JSON objects")
        missing = [key for key in ("id", "name", "arguments") if key not in value]
        if missing:
            raise ValueError(f"tool call is missing required field(s): {', '.join(missing)}")
        arguments = value["arguments"]
        if not isinstance(arguments, Mapping):
            raise TypeError("tool call arguments must be a JSON object")
        return cls(
            id=value["id"],
            name=value["name"],
            arguments=dict(arguments),
        )

    def as_dict(self) -> dict[str, JSONValue]:
        """Return the JSON-shaped neutral tool call."""

        return {
            "id": self.id,
            "name": self.name,
            "arguments": _copy_json_value(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class AssistantToolResult:
    """A provider-neutral result for one assistant tool call."""

    tool_call_id: str
    name: str
    content: JSONValue
    is_error: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id:
            raise ValueError("tool result id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("tool result name must be a non-empty string")
        if not isinstance(self.is_error, bool):
            raise TypeError("tool result is_error must be a boolean")
        object.__setattr__(self, "content", _copy_json_value(self.content))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AssistantToolResult:
        """Build a tool result from the neutral JSON object shape."""

        if not isinstance(value, Mapping):
            raise TypeError("tool results must be JSON objects")
        missing = [
            key for key in ("tool_call_id", "name", "content", "is_error") if key not in value
        ]
        if missing:
            raise ValueError(f"tool result is missing required field(s): {', '.join(missing)}")
        return cls(
            tool_call_id=value["tool_call_id"],
            name=value["name"],
            content=value["content"],
            is_error=value["is_error"],
        )

    def as_dict(self) -> dict[str, JSONValue]:
        """Return the JSON-shaped neutral tool result."""

        return {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": _copy_json_value(self.content),
            "is_error": self.is_error,
        }


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """A JSON-serialisable, provider-neutral conversation message.

    ``tool_calls`` and ``tool_results`` are populated on assistant messages
    when a turn is represented as one neutral record.  A result may also be
    represented as a ``tool`` role message with its matching
    ``tool_call_id``.  ``content`` remains JSON-shaped rather than being
    restricted to text so structured results can be retained without
    provider-specific encoding.
    """

    role: str
    content: JSONValue = None
    tool_calls: tuple[AssistantToolCall, ...] = ()
    tool_results: tuple[AssistantToolResult, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    is_error: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in _MESSAGE_ROLES:
            valid = ", ".join(sorted(_MESSAGE_ROLES))
            raise ValueError(f"message role must be one of {valid}; got {self.role!r}")
        content = _copy_json_value(self.content)
        object.__setattr__(self, "content", content)

        calls: list[AssistantToolCall] = []
        for call in self.tool_calls:
            if isinstance(call, AssistantToolCall):
                calls.append(call)
            elif isinstance(call, Mapping):
                calls.append(AssistantToolCall.from_mapping(call))
            else:
                raise TypeError("tool_calls must contain AssistantToolCall records or JSON objects")
        object.__setattr__(self, "tool_calls", tuple(calls))

        results: list[AssistantToolResult] = []
        for result in self.tool_results:
            if isinstance(result, AssistantToolResult):
                results.append(result)
            elif isinstance(result, Mapping):
                results.append(AssistantToolResult.from_mapping(result))
            else:
                raise TypeError(
                    "tool_results must contain AssistantToolResult records or JSON objects"
                )
        object.__setattr__(self, "tool_results", tuple(results))

        for field_name in ("tool_call_id", "name"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field_name} must be a non-empty string when provided")
        if not isinstance(self.is_error, bool):
            raise TypeError("message is_error must be a boolean")
        if (
            self.role == "tool"
            and not self.is_error
            and isinstance(content, dict)
            and "graph_fingerprint" in content
            and not isinstance(content["graph_fingerprint"], str)
        ):
            raise TypeError("tool graph_fingerprint must be a string")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AssistantMessage:
        """Build a message from the neutral JSON object shape."""

        if not isinstance(value, Mapping):
            raise TypeError("messages must be JSON objects")
        if "role" not in value:
            raise ValueError("message is missing required field: role")
        raw_calls = value.get("tool_calls", ())
        if raw_calls is None:
            raw_calls = ()
        if isinstance(raw_calls, (str, bytes)):
            raise TypeError("message tool_calls must be a sequence of JSON objects")
        try:
            calls = tuple(
                call
                if isinstance(call, AssistantToolCall)
                else AssistantToolCall.from_mapping(call)
                for call in raw_calls
            )
        except TypeError:
            raise TypeError("message tool_calls must be a sequence of JSON objects") from None
        raw_results = value.get("tool_results", ())
        if raw_results is None:
            raw_results = ()
        if isinstance(raw_results, (str, bytes)):
            raise TypeError("message tool_results must be a sequence of JSON objects")
        try:
            results = tuple(
                result
                if isinstance(result, AssistantToolResult)
                else AssistantToolResult.from_mapping(result)
                for result in raw_results
            )
        except TypeError:
            raise TypeError("message tool_results must be a sequence of JSON objects") from None
        role = value["role"]
        if role == "tool" and "is_error" not in value:
            raise ValueError("tool message is missing required field: is_error")
        return cls(
            role=role,
            content=value.get("content"),
            tool_calls=calls,
            tool_results=results,
            tool_call_id=value.get("tool_call_id"),
            name=value.get("name"),
            is_error=value.get("is_error", False),
        )

    def as_dict(self) -> dict[str, JSONValue]:
        """Return the JSON-shaped neutral message."""

        result: dict[str, JSONValue] = {
            "role": self.role,
            "content": _copy_json_value(self.content),
        }
        if self.tool_calls:
            result["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        if self.tool_results:
            result["tool_results"] = [item.as_dict() for item in self.tool_results]
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            result["name"] = self.name
        if self.role == "tool":
            result["is_error"] = self.is_error
        return result


_PERSISTED_TOOL_EVIDENCE_KEYS = frozenset(
    {
        "applied_operations",
        "base_revision",
        "capability_hash",
        "git_sha",
        "graph_fingerprint",
        "operation_version",
        "plan_hash",
        "policy_hash",
        "project_revision",
        "result_revision",
        "source_digest",
        "verification_error_code",
        "verification_status",
        "verification_tier",
    }
)


_MAX_SESSION_TITLE_CHARS = 80


def _session_title(text: str) -> str:
    """Render one conversation title from its opening user message."""

    collapsed = " ".join(text.split())
    if len(collapsed) <= _MAX_SESSION_TITLE_CHARS:
        return collapsed
    return collapsed[: _MAX_SESSION_TITLE_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """One conversation as the chat list shows it, without its transcript.

    The store's own record. `schemas.AssistantSessionSummary` is the wire model
    the route converts this into; the two are deliberately named apart so a
    reader can tell which side of the transport boundary a value is on.
    """

    session_id: str
    title: str
    created_at: float
    last_used: float
    message_count: int


def _persisted_message(message: AssistantMessage) -> dict[str, JSONValue]:
    """Redact provider-working tool payloads for durable restart history."""

    wire: dict[str, JSONValue] = message.as_dict()
    if isinstance(message.content, str):
        wire["content"] = redact_sensitive_text(
            message.content,
            known_secrets=(
                env_secret
                for name in (
                    "ANTHROPIC_API_KEY",
                    "OPENAI_API_KEY",
                    "DATABRICKS_TOKEN",
                )
                if (env_secret := os.environ.get(name))
            ),
        )
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "name": call.name,
                "arguments": {"redacted": True},
            }
            for call in message.tool_calls
        ]
    if message.role == "tool":
        content = message.content
        redacted: dict[str, JSONValue] = {"redacted": True}
        if isinstance(content, dict):
            for key in sorted(_PERSISTED_TOOL_EVIDENCE_KEYS):
                evidence_value = content.get(key)
                if key in content and (
                    isinstance(evidence_value, (str, int, float, bool)) or evidence_value is None
                ):
                    redacted[key] = _copy_json_value(evidence_value)
            error = content.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                safe_error: dict[str, JSONValue] = {"code": error["code"]}
                for key in ("validation_path", "validation_reason"):
                    value = error.get(key)
                    if isinstance(value, str):
                        safe_error[key] = value
                redacted["error"] = safe_error
        wire["content"] = redacted
    return wire


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    """One complete user turn and all messages produced for it."""

    messages: tuple[AssistantMessage, ...]

    def __post_init__(self) -> None:
        normalized: list[AssistantMessage] = []
        for message in self.messages:
            if isinstance(message, AssistantMessage):
                normalized.append(message)
            elif isinstance(message, Mapping):
                normalized.append(AssistantMessage.from_mapping(message))
            else:
                raise TypeError("turn messages must be AssistantMessage records or JSON objects")
        if not normalized:
            raise ValueError("an assistant turn must contain at least one message")
        if normalized[0].role != "user":
            raise ValueError("an assistant turn must begin with a user message")
        if sum(message.role == "user" for message in normalized) != 1:
            raise ValueError("an assistant turn must contain exactly one user message")
        object.__setattr__(self, "messages", tuple(normalized))

    @classmethod
    def from_messages(
        cls,
        messages: Iterable[AssistantMessage | Mapping[str, Any]],
    ) -> AssistantTurn:
        """Create a complete turn from an iterable of neutral messages."""

        return cls(
            tuple(
                message
                if isinstance(message, AssistantMessage)
                else AssistantMessage.from_mapping(message)
                for message in messages
            )
        )

    @property
    def message_count(self) -> int:
        """Number of provider messages in this complete turn."""

        return len(self.messages)

    def as_dict(self) -> dict[str, JSONValue]:
        """Return the JSON-shaped turn record."""

        return {"messages": [message.as_dict() for message in self.messages]}


@dataclass(slots=True)
class AssistantSession:
    """One process-local assistant session bound to a pipeline source file."""

    id: str
    source_file: str
    history: list[AssistantTurn] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("session id must be a non-empty string")
        source_file = os.fspath(self.source_file)
        if isinstance(source_file, bytes):
            raise TypeError("source_file must be text, not bytes")
        self.source_file = source_file
        self.history = [
            turn
            if isinstance(turn, AssistantTurn)
            else AssistantTurn.from_messages(
                turn["messages"] if isinstance(turn, Mapping) else turn
            )
            for turn in self.history
        ]

    @property
    def stored_message_count(self) -> int:
        """Return the number of messages currently retained in history."""

        return sum(turn.message_count for turn in self.history)

    @property
    def title(self) -> str:
        """Return this conversation's list title: its first user message."""

        for turn in self.history:
            for message in turn.messages:
                if message.role == "user" and isinstance(message.content, str):
                    return _session_title(message.content)
        return ""

    def as_dict(self) -> dict[str, JSONValue]:
        """Return the serializable session state, excluding the live lock."""

        return {
            "id": self.id,
            "source_file": self.source_file,
            "history": [turn.as_dict() for turn in self.history],
            "created_at": self.created_at,
            "last_used": self.last_used,
        }

    def as_persisted_dict(self) -> dict[str, JSONValue]:
        """Return restartable history with provider-working payloads redacted."""

        return {
            "id": self.id,
            "source_file": self.source_file,
            "history": [
                {"messages": [_persisted_message(message) for message in turn.messages]}
                for turn in self.history
            ],
            "created_at": self.created_at,
            "last_used": self.last_used,
        }


def _session_from_payload(payload: object, session_id: str) -> AssistantSession:
    """Validate and construct one persisted session without retaining it."""

    if not isinstance(payload, Mapping) or payload.get("id") != session_id:
        raise ValueError("session file does not describe the requested session")
    for key in ("created_at", "last_used"):
        stamp = payload.get(key)
        if (
            isinstance(stamp, bool)
            or not isinstance(stamp, (int, float))
            or not math.isfinite(stamp)
        ):
            raise ValueError(f"session file {key} must be a finite number")
    history = payload.get("history")
    if not isinstance(history, list):
        raise ValueError("session file history must be a list")
    source_file = payload.get("source_file")
    if not isinstance(source_file, str) or not source_file:
        raise ValueError("session file source_file must be a non-empty string")
    return AssistantSession(
        id=session_id,
        source_file=source_file,
        history=list(history),
        created_at=float(payload["created_at"]),
        last_used=float(payload["last_used"]),
    )


SessionRef: TypeAlias = str | AssistantSession
TurnInput: TypeAlias = (
    AssistantTurn | Mapping[str, Any] | Iterable[AssistantMessage | Mapping[str, Any]]
)


class SessionStore:
    """Bounded in-memory store for :class:`AssistantSession` records.

    ``lookup`` is intentionally non-creating: an unknown id returns ``None``
    so a route can map it to a 404.  ``append`` and the retention methods use
    ``KeyError`` for an unknown id, which keeps accidental writes to an
    evicted session visible to the caller.

    The store is used on the asyncio event-loop thread, so its synchronous
    mutations are atomic with respect to other store calls.  The per-session
    :attr:`AssistantSession.lock` remains the one-turn guard for async work.
    """

    def __init__(
        self,
        *,
        max_provider_messages: int = PROVIDER_WINDOW_MESSAGES,
        max_stored_messages: int = STORED_HISTORY_MESSAGES,
        max_live_sessions: int = MAX_LIVE_SESSIONS,
        max_persisted_sessions: int = MAX_PERSISTED_SESSIONS,
        clock: Callable[[], float] = time.time,
        storage_dir: Callable[[], Path] | None = None,
    ) -> None:
        self._validate_limit("max_provider_messages", max_provider_messages)
        self._validate_limit("max_stored_messages", max_stored_messages)
        self._validate_limit("max_live_sessions", max_live_sessions)
        self._validate_limit("max_persisted_sessions", max_persisted_sessions)
        self.max_provider_messages = max_provider_messages
        self.max_stored_messages = max_stored_messages
        self.max_live_sessions = max_live_sessions
        self.max_persisted_sessions = max_persisted_sessions
        self._clock = clock
        # A factory, not a Path: the project root is the server's cwd, which
        # is resolved per call like the tool layer does, never at import.
        self._storage_dir = storage_dir
        self._sessions: OrderedDict[str, AssistantSession] = OrderedDict()

    @staticmethod
    def _validate_limit(name: str, value: int) -> None:
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    @staticmethod
    def _source_file_text(source_file: str | os.PathLike[str]) -> str:
        value = os.fspath(source_file)
        if isinstance(value, bytes):
            raise TypeError("source_file must be text, not bytes")
        return value

    def _touch(self, session: AssistantSession) -> None:
        session.last_used = self._clock()
        self._sessions.move_to_end(session.id)

    def _require(self, session_ref: SessionRef) -> AssistantSession:
        if isinstance(session_ref, AssistantSession):
            session = self._sessions.get(session_ref.id)
            if session is not session_ref:
                raise KeyError(session_ref.id)
            return session
        if not isinstance(session_ref, str):
            raise TypeError("session reference must be a session id or AssistantSession")
        session = self._sessions.get(session_ref)
        if session is None:
            raise KeyError(session_ref)
        return session

    def _evict_idle(self, *, exclude: frozenset[str] = frozenset()) -> None:
        """Evict oldest idle sessions until the configured bound is met."""

        while len(self._sessions) > self.max_live_sessions:
            candidate_id: str | None = None
            for session_id, session in self._sessions.items():
                if session_id in exclude or session.lock.locked():
                    continue
                candidate_id = session_id
                break
            if candidate_id is None:
                # All retained sessions are active.  Keeping them is required
                # to avoid invalidating a turn that is already in flight.
                return
            del self._sessions[candidate_id]

    def _persist(self, session: AssistantSession) -> None:
        """Write one session's file atomically; failure warns, never raises.

        By the time a persist runs, the turn has already streamed to the user
        and committed to the in-memory session — failing it would discard a
        delivered conversation over a disk hiccup. The warning keeps the
        degradation operator-visible.
        """

        if self._storage_dir is None:
            return
        try:
            path = self._storage_dir() / f"{session.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(session.as_persisted_dict()), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            logger.warning("assistant_session_persist_failed", session_id=session.id, exc_info=True)

    def _prune_persisted(self, keep_id: str) -> None:
        """Prune oldest session files past the cap; never the one just made."""

        if self._storage_dir is None:
            return
        try:
            base = self._storage_dir()
            for path in base.glob("*.json.tmp"):
                session_id = path.name.removesuffix(".json.tmp")
                if _SESSION_ID_PATTERN.fullmatch(session_id) is not None:
                    path.unlink(missing_ok=True)
            others = sorted(
                (p for p in base.glob("*.json") if p.stem != keep_id and not p.is_symlink()),
                key=lambda p: p.stat().st_mtime,
            )
            excess = len(others) + 1 - self.max_persisted_sessions
            if excess <= 0:
                # Clamp before slicing: a negative slice would take from the
                # END of the list and delete the oldest sessions while UNDER
                # the cap (regression-pinned).
                return
            for path in others[:excess]:
                path.unlink(missing_ok=True)
        except OSError:
            logger.warning("assistant_session_prune_failed", exc_info=True)

    def _revive(
        self, session_id: str, *, expected_source_file: str | None = None
    ) -> AssistantSession | None:
        """Load a persisted session into the store, or None with a warning.

        Ids are uuid4 hex; anything else (including path-traversal attempts)
        never reaches the filesystem. Unreadable, corrupt, or invalid files
        follow the `.haute/` posture: logged and treated as absent.
        """

        if self._storage_dir is None or _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        path = self._storage_dir() / f"{session_id}.json"
        if path.is_symlink():
            logger.warning(
                "assistant_session_unreadable",
                session_id=session_id,
                detail="session file must not be a symbolic link",
            )
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            logger.warning("assistant_session_unreadable", session_id=session_id, exc_info=True)
            return None
        try:
            data = json.loads(raw)
            session = _session_from_payload(data, session_id)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "assistant_session_unreadable",
                session_id=session_id,
                detail=str(exc),
            )
            return None
        if expected_source_file is not None and session.source_file != expected_source_file:
            return None
        self._sessions[session_id] = session
        self._sessions.move_to_end(session_id)
        self._evict_idle(exclude=frozenset({session_id}))
        return session

    def create(self, source_file: str | os.PathLike[str]) -> AssistantSession:
        """Create, retain, persist, and return a new uuid4-hex session."""

        source = self._source_file_text(source_file)
        session_id = uuid4().hex
        while session_id in self._sessions:  # defensive against UUID collision
            session_id = uuid4().hex
        now = self._clock()
        session = AssistantSession(
            id=session_id,
            source_file=source,
            created_at=now,
            last_used=now,
        )
        self._sessions[session_id] = session
        # Never evict the object just returned.  It is the caller's newly
        # created session even when every older session is currently busy.
        self._evict_idle(exclude=frozenset({session_id}))
        self._persist(session)
        self._prune_persisted(session_id)
        return session

    def _persisted_summary(self, path: Path) -> tuple[str, SessionSummary] | None:
        """Summarise one persisted session file without reviving it.

        Reading the file directly rather than through `_revive` is deliberate:
        listing must not pull every stored conversation into memory, where it
        would evict live sessions through the LRU bound it shares.
        """

        session_id = path.stem
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        if path.is_symlink():
            logger.warning(
                "assistant_session_list_unreadable",
                session_id=session_id,
                detail="session file must not be a symbolic link",
            )
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            session = _session_from_payload(payload, session_id)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "assistant_session_list_unreadable",
                session_id=session_id,
                detail=str(exc),
            )
            return None

        return session.source_file, SessionSummary(
            session_id=session_id,
            title=session.title,
            created_at=session.created_at,
            last_used=session.last_used,
            message_count=session.stored_message_count,
        )

    def list_sessions(self, source_file: str | os.PathLike[str]) -> tuple[SessionSummary, ...]:
        """Return this pipeline's conversations, most recently used first.

        A session with no messages is omitted: `create` persists immediately,
        so an abandoned "new chat" would otherwise sit in the list as an
        untitled empty row. Live sessions take precedence over their persisted
        copy, which can lag by one turn.
        """

        source = self._source_file_text(source_file)
        summaries: dict[str, SessionSummary] = {}
        if self._storage_dir is not None:
            try:
                paths = sorted(self._storage_dir().glob("*.json"))
            except OSError:
                logger.warning("assistant_session_list_failed", exc_info=True)
                paths = []
            for path in paths:
                summary = self._persisted_summary(path)
                if summary is not None and summary[0] == source:
                    summaries[summary[1].session_id] = summary[1]
        for session in self._sessions.values():
            if session.source_file != source:
                continue
            summaries[session.id] = SessionSummary(
                session_id=session.id,
                title=session.title,
                created_at=session.created_at,
                last_used=session.last_used,
                message_count=session.stored_message_count,
            )
        return tuple(
            sorted(
                (item for item in summaries.values() if item.message_count > 0),
                key=lambda item: (-item.last_used, item.session_id),
            )
        )

    def lookup(self, session_id: str) -> AssistantSession | None:
        """Return a live session and mark it recently used, or return ``None``.

        A miss in memory revives the session's persisted file when storage is
        configured, so LRU eviction and server restarts are invisible to a
        client holding a persisted id. Lookup never evicts retained sessions:
        the spec bounds the store at *create* time only. Evicting here could
        remove a just-created idle session the moment a busy over-cap session
        is looked up, making its id 404 on first use.
        """

        if not isinstance(session_id, str):
            raise TypeError("session id must be a string")
        session = self._sessions.get(session_id)
        if session is None:
            session = self._revive(session_id)
        if session is None:
            return None
        self._touch(session)
        return session

    def resume(
        self, session_id: str, source_file: str | os.PathLike[str]
    ) -> AssistantSession | None:
        """Resume a session only when it is bound to ``source_file``.

        A source mismatch is deliberately observational: it must not refresh
        the live LRU or promote a persisted record into it.
        """

        if not isinstance(session_id, str):
            raise TypeError("session id must be a string")
        source = self._source_file_text(source_file)
        session = self._sessions.get(session_id)
        if session is not None and session.source_file != source:
            return None
        if session is None:
            session = self._revive(session_id, expected_source_file=source)
        if session is None:
            return None
        self._touch(session)
        return session

    def append(self, session_ref: SessionRef, turn: TurnInput) -> AssistantTurn:
        """Append one complete turn, prune whole oldest turns, and persist.

        A turn that is itself larger than ``max_stored_messages`` remains
        intact; splitting it would violate the provider conversation shape.
        The same rule applies when building a provider window: an oversized
        newest turn is omitted rather than sliced.
        """

        session = self._require(session_ref)
        record = self._coerce_turn(turn)
        session.history.append(record)
        self.prune(session)
        self._persist(session)
        return record

    def prune(self, session_ref: SessionRef) -> None:
        """Prune a session's history at complete-turn boundaries."""

        session = self._require(session_ref)
        while session.stored_message_count > self.max_stored_messages and len(session.history) > 1:
            session.history.pop(0)
        self._touch(session)

    def history_window(
        self,
        session_ref: SessionRef,
        *,
        max_messages: int | None = None,
    ) -> list[dict[str, JSONValue]]:
        """Return the newest contiguous complete-turn provider history window."""

        session = self._require(session_ref)
        limit = self.max_provider_messages if max_messages is None else max_messages
        if type(limit) is not int or limit < 0:
            raise ValueError("max_messages must be a non-negative integer")

        selected: list[AssistantTurn] = []
        count = 0
        for turn in reversed(session.history):
            if count + turn.message_count > limit:
                break
            selected.append(turn)
            count += turn.message_count
        selected.reverse()
        self._touch(session)
        return [message.as_dict() for turn in selected for message in turn.messages]

    def _coerce_turn(self, turn: TurnInput) -> AssistantTurn:
        if isinstance(turn, AssistantTurn):
            return turn
        if isinstance(turn, Mapping):
            messages = turn.get("messages", _MISSING)
            if messages is _MISSING:
                raise ValueError("turn record is missing required field: messages")
            if isinstance(messages, (str, bytes)):
                raise TypeError("turn messages must be a sequence of JSON objects")
            return AssistantTurn.from_messages(messages)
        return AssistantTurn.from_messages(turn)

    def __len__(self) -> int:
        """Return the number of currently retained sessions."""

        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        """Return whether *session_id* is currently retained."""

        return session_id in self._sessions


__all__ = [
    "AssistantMessage",
    "AssistantSession",
    "AssistantToolCall",
    "AssistantToolResult",
    "AssistantTurn",
    "JSONValue",
    "MAX_LIVE_SESSIONS",
    "MAX_PERSISTED_SESSIONS",
    "PROVIDER_WINDOW_MESSAGES",
    "STORED_HISTORY_MESSAGES",
    "SessionStore",
    "SessionSummary",
]
