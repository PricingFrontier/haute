"""Native structural checks for records whose inference evidence is already known.

The ordinary inference walker remains the authority. Only records admitted by
this conservative filter skip that walk; a mismatch is new evidence, not a
validation error to expose to the caller. Parallel JSONL can combine parsing
and checking; rejected bytes still use the original parser and inference walk.
"""

from __future__ import annotations

import operator
from copy import deepcopy
from dataclasses import dataclass, field
from functools import reduce
from types import GenericAlias
from typing import Annotated, Any

import msgspec

_INITIAL_SAMPLE_RECORDS = 10_000
_RELEARN_RECORDS = 100
_MAX_COMPILATIONS = 8
_MAX_SHAPE_DEPTH = 64
# orjson converts integers beyond its 64-bit range to float. msgspec accepts
# larger Python ints, so its JSON fast path must not hide that type widening.
# msgspec's bounds themselves are signed int64: positive uint64 values also
# conservatively use the original parser.
_JSON_NATIVE_INT = Annotated[int, msgspec.Meta(ge=-(2**63), le=2**63 - 1)]


def _native_scalar(kind: type, json_mode: bool) -> Any:
    return _JSON_NATIVE_INT if json_mode and kind is int else kind


@dataclass(slots=True)
class _ObservedValue:
    scalar_types: set[type] = field(default_factory=set)
    object_fields: dict[str, _ObservedValue] | None = None
    array: _ObservedArray | None = None
    unsupported: bool = False

    def observe(self, value: Any, depth: int = 0) -> None:
        if self.unsupported:
            return
        if depth > _MAX_SHAPE_DEPTH:
            self.unsupported = True
            return
        if isinstance(value, dict):
            if self.object_fields is None:
                self.object_fields = {}
            for key, child in value.items():
                observed = self.object_fields.get(key)
                if observed is None:
                    observed = self.object_fields[key] = _ObservedValue()
                observed.observe(child, depth + 1)
        elif isinstance(value, list):
            if self.array is None:
                self.array = _ObservedArray()
            self.array.observe(value, depth)
        elif type(value) in (str, int, float, bool, type(None)):
            self.scalar_types.add(type(value))
        else:
            self.unsupported = True

    def native_type(self, *, json_mode: bool = False) -> Any:
        if self.unsupported:
            return None
        choices: list[Any] = [_native_scalar(kind, json_mode) for kind in self.scalar_types]
        if self.object_fields is not None:
            # Source keys need not be safe Python attribute names (e.g. class
            # or __dict__). Generated attributes have explicit wire aliases.
            fields = []
            for index, (key, child) in enumerate(self.object_fields.items()):
                child_type = child.native_type(json_mode=json_mode)
                if child_type is None:
                    return None
                fields.append(
                    (
                        f"field_{index}",
                        child_type,
                        msgspec.field(default=msgspec.UNSET, name=key),
                    )
                )
            choices.append(msgspec.defstruct("InferenceObject", fields, forbid_unknown_fields=True))
        if self.array is not None:
            array_type = self.array.native_type(json_mode=json_mode)
            if array_type is None:
                return None
            choices.append(array_type)
        return reduce(operator.or_, choices)


@dataclass(slots=True)
class _ObservedArray:
    objects: _ObservedValue | None = None
    scalar_types: set[type] = field(default_factory=set)
    empty_seen: bool = False
    null_only_seen: bool = False

    def observe(self, values: list[Any], depth: int) -> None:
        if not values:
            self.empty_seen = True
        elif any(isinstance(value, dict) for value in values):
            if self.objects is None:
                self.objects = _ObservedValue()
            # The walker ignores scalars in object-array mode. They must not
            # teach the filter that those types were inferred as scalar-array
            # evidence. Nested lists remain on the ordinary walker as well.
            for value in values:
                if isinstance(value, dict):
                    self.objects.observe(value, depth + 1)
        else:
            self.scalar_types.update(type(value) for value in values if value is not None)
            if all(value is None for value in values):
                self.null_only_seen = True

    def native_type(self, *, json_mode: bool = False) -> Any:
        if self.objects is not None:
            # Keep mixed/scalar/null array forms on the ordinary walker once
            # object items have been observed. In particular, accepting an
            # arbitrary union of item types could hide an invalid nested array.
            item_type = self.objects.native_type(json_mode=json_mode)
            if item_type is None:
                return None
        else:
            choices: list[Any] = [_native_scalar(kind, json_mode) for kind in self.scalar_types]
            if self.null_only_seen:
                choices.append(type(None))
            # A [1, null] observation infers int, but a later [null] widens it
            # to str. Until null-only evidence is collected, nullable scalar
            # arrays deliberately fail the filter and use the exact walker.
            if not choices:
                return Annotated[list[None], msgspec.Meta(max_length=0)]
            item_type = reduce(operator.or_, choices)
        array_type = GenericAlias(list, item_type)
        if not self.empty_seen:
            return Annotated[array_type, msgspec.Meta(min_length=1)]
        return array_type


@dataclass(frozen=True)
class InferenceSeed:
    """Picklable observations, without native types or retained input records."""

    observed: _ObservedValue


class InferenceFilter:
    """Learn bounded prefixes of unmatched records and filter known structures.

    At most eight compilations per stream bound native type construction even
    for schema-drift-heavy data. After the budget is used, unmatched records
    still receive full inference; no additional observation tree is retained.
    """

    def __init__(self, seed: InferenceSeed | None = None, *, json_mode: bool = False) -> None:
        observed = _ObservedValue() if seed is None else deepcopy(seed.observed)
        self._observed: _ObservedValue | None = observed
        self._json_mode = json_mode
        self._native_type: Any = None
        self._decoder: msgspec.json.Decoder | None = None
        self._remaining = _INITIAL_SAMPLE_RECORDS if seed is None else _RELEARN_RECORDS
        self._compilations = 0 if seed is None else 1
        if seed is not None:
            self._compile(observed)

    def _compile(self, observed: _ObservedValue) -> None:
        self._native_type = observed.native_type(json_mode=self._json_mode)
        self._decoder = (
            msgspec.json.Decoder(self._native_type, strict=True)
            if self._json_mode and self._native_type is not None
            else None
        )

    def snapshot(self) -> InferenceSeed | None:
        """Share a learned prefix; each receiving stream owns its later changes."""
        if self._native_type is None or self._observed is None:
            return None
        return InferenceSeed(deepcopy(self._observed))

    def matches(self, record: dict[str, Any]) -> bool:
        if self._native_type is None:
            return False
        try:
            # Discard the converted object: only the original parsed records
            # may supply inference evidence. Strict conversion does not turn
            # strings or booleans into numeric evidence or ignore extra keys.
            msgspec.convert(record, self._native_type, strict=True)
        except msgspec.ValidationError:
            return False
        return True

    def observe(self, record: dict[str, Any]) -> None:
        if self._observed is None:
            return
        self._observed.observe(record)
        self._remaining -= 1
        if self._remaining == 0:
            self._compile(self._observed)
            self._compilations += 1
            self._remaining = _RELEARN_RECORDS
            if self._compilations == _MAX_COMPILATIONS:
                self._observed = None

    def matches_json(self, raw: bytes) -> bool:
        """Check JSON in one native pass; rejected bytes need the original parser."""
        if self._decoder is None:
            return False
        try:
            self._decoder.decode(raw)
        except (msgspec.DecodeError, UnicodeDecodeError):
            # msgspec may reject an earlier duplicate value that orjson would
            # overwrite, or report malformed input differently. Reparse the
            # original bytes to retain the existing input/error contract.
            return False
        return True
