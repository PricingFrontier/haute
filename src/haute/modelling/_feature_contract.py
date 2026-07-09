"""Train-to-deploy feature contract artifact.

A :class:`FeatureContract` pins the exact feature schema a model was
trained against — the ordered feature list, dtypes, categorical set,
target name/type, task, and a content hash over all of those.  At
score/deploy time the same artifact is rebuilt from the live data and
compared via :func:`assert_contracts_match`; any structural drift
raises :class:`haute.errors.FeatureMismatchError` naming the field that
disagreed, so the operator sees the actual problem rather than a
downstream library error.

The contract round-trips through pretty-printed, sort-keyed JSON so the
artifact is human-readable in code review and byte-deterministic for
downstream content hashing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from haute._stat_gated_cache import StatGatedCache, artifact_cache_key
from haute.errors import FeatureMismatchError

Task = Literal["classification", "regression"]

_REQUIRED_FIELDS: tuple[str, ...] = (
    "features",
    "feature_types",
    "categorical_features",
    "target_name",
    "target_type",
    "task",
)
_FIELDS: tuple[str, ...] = (
    "features",
    "feature_types",
    "categorical_features",
    "categorical_levels",
    "target_name",
    "target_type",
    "task",
)
_ALL_KEYS: frozenset[str] = frozenset((*_FIELDS, "contract_hash"))

CONTRACT_FILENAME = "feature_contract.json"


@dataclasses.dataclass(frozen=True)
class FeatureContract:
    """Immutable record of the feature schema a model was trained against."""

    features: list[str]
    feature_types: dict[str, str]
    categorical_features: list[str]
    categorical_levels: dict[str, list[str | None]]
    target_name: str
    target_type: str
    task: Task
    contract_hash: str


def _canonical_payload(
    features: list[str],
    feature_types: Mapping[str, str],
    categorical_features: list[str],
    categorical_levels: Mapping[str, Iterable[str | None]] | None,
    target_name: str,
    target_type: str,
    task: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "features": list(features),
        "feature_types": dict(feature_types),
        "categorical_features": list(categorical_features),
        "target_name": target_name,
        "target_type": target_type,
        "task": task,
    }
    if categorical_levels:
        payload["categorical_levels"] = {
            str(column): list(levels) for column, levels in categorical_levels.items()
        }
    return payload


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_contract(
    features: list[str],
    feature_types: Mapping[str, str],
    categorical_features: list[str],
    target_name: str,
    target_type: str,
    task: Task,
    categorical_levels: Mapping[str, Iterable[str | None]] | None = None,
) -> FeatureContract:
    """Construct a contract and compute its content hash.

    Inputs are accepted as plain lists / dicts and normalised to the
    contract's read-only forms.  ``contract_hash`` is the sha256 of the
    canonical-JSON representation of every field except itself.
    """
    normalised_levels = normalise_categorical_levels(
        categorical_levels,
        features=features,
        categorical_features=categorical_features,
    )
    payload = _canonical_payload(
        features,
        feature_types,
        categorical_features,
        normalised_levels,
        target_name,
        target_type,
        task,
    )
    return FeatureContract(
        features=list(features),
        feature_types=dict(feature_types),
        categorical_features=list(categorical_features),
        categorical_levels=normalised_levels,
        target_name=target_name,
        target_type=target_type,
        task=task,
        contract_hash=_hash_payload(payload),
    )


def save_contract(contract: FeatureContract, path: Path | str) -> None:
    """Write the contract to *path* as pretty JSON with sorted keys."""
    path = Path(path)
    payload: dict[str, Any] = {
        "features": list(contract.features),
        "feature_types": dict(contract.feature_types),
        "categorical_features": list(contract.categorical_features),
        "categorical_levels": {
            column: list(levels) for column, levels in contract.categorical_levels.items()
        },
        "target_name": contract.target_name,
        "target_type": contract.target_type,
        "task": contract.task,
        "contract_hash": contract.contract_hash,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_contract(path: Path | str, *, verify_hash: bool = True) -> FeatureContract:
    """Read and validate a contract written by :func:`save_contract`.

    When ``verify_hash`` is True (the default), recomputes the canonical
    payload hash and raises :class:`FeatureMismatchError` if it disagrees
    with the stored ``contract_hash`` — catching hand-edited or partially-
    corrupted artifacts. Pass ``verify_hash=False`` only when rehydrating
    a contract you just modified in memory.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FeatureMismatchError(
            "contract file must contain a JSON object",
            path=str(path),
            actual_type=type(raw).__name__,
        )

    keys = set(raw)
    missing = (frozenset(_REQUIRED_FIELDS) | {"contract_hash"}) - keys
    if missing:
        raise FeatureMismatchError(
            "contract file missing required field(s)",
            path=str(path),
            missing=sorted(missing),
        )
    unknown = keys - _ALL_KEYS
    if unknown:
        raise FeatureMismatchError(
            "contract file has unknown top-level field(s)",
            path=str(path),
            unknown=sorted(unknown),
        )

    _check_type(raw, "features", list, path)
    _check_type(raw, "feature_types", dict, path)
    _check_type(raw, "categorical_features", list, path)
    if "categorical_levels" in raw:
        _check_type(raw, "categorical_levels", dict, path)
    _check_type(raw, "target_name", str, path)
    _check_type(raw, "target_type", str, path)
    _check_type(raw, "task", str, path)
    _check_type(raw, "contract_hash", str, path)

    categorical_levels = normalise_categorical_levels(
        raw.get("categorical_levels"),
        features=raw["features"],
        categorical_features=raw["categorical_features"],
        path=path,
    )

    if verify_hash:
        recomputed = _hash_payload(
            _canonical_payload(
                raw["features"],
                raw["feature_types"],
                raw["categorical_features"],
                categorical_levels,
                raw["target_name"],
                raw["target_type"],
                raw["task"],
            )
        )
        stored = raw["contract_hash"]
        if recomputed != stored:
            raise FeatureMismatchError(
                "contract file hash does not match its content; file has been edited or corrupted",
                path=str(path),
                expected_hash=stored,
                actual_hash=recomputed,
            )

    return FeatureContract(
        features=list(raw["features"]),
        feature_types=dict(raw["feature_types"]),
        categorical_features=list(raw["categorical_features"]),
        categorical_levels=categorical_levels,
        target_name=raw["target_name"],
        target_type=raw["target_type"],
        task=raw["task"],
        contract_hash=raw["contract_hash"],
    )


def _check_type(payload: dict[str, Any], key: str, expected: type, path: Path) -> None:
    if not isinstance(payload[key], expected):
        raise FeatureMismatchError(
            f"contract field {key!r} has wrong type",
            path=str(path),
            field=key,
            expected_type=expected.__name__,
            actual_type=type(payload[key]).__name__,
        )


# ---------------------------------------------------------------------------
# Stat-gated contract cache
# ---------------------------------------------------------------------------
#
# Contract reads sit on per-request paths — every deployed ``/quote``
# checks the bundled contract, and the executor's column-contract planner
# loads it during graph construction.  Re-reading and re-hashing the same
# unchanged JSON per request is pure latency, so repeated loads of an
# unchanged file are served from a process-wide cache gated on
# ``(st_mtime_ns, st_size)``.  A changed file (retrain, redeploy) reloads
# and re-verifies on the next call.  Contract MATCHING against live data
# is intentionally NOT cached — only the disk read + hash verification.

_contract_cache: StatGatedCache[str, FeatureContract] = StatGatedCache(
    artifact_kind="feature contract"
)


def load_contract_cached(path: Path | str) -> FeatureContract:
    """Stat-gated, single-flight cache over :func:`load_contract`.

    Hash verification runs on every actual disk load (first call and
    after any mtime/size change) but is skipped on cache hits.  Failed
    loads are never cached.  The returned :class:`FeatureContract` is
    shared across callers and threads — treat it as immutable.
    """
    # Key = normcase(expanduser(resolve())) — normcase is a no-op on POSIX,
    # so a macOS case-variant spelling still gets its own slot (the same
    # accepted posture as haute._json_flatten._path_hash).
    resolved = artifact_cache_key(path)
    return _contract_cache.get_or_load(
        resolved,
        resolved,
        lambda: load_contract(path),
    )


def _clear_contract_cache() -> None:
    """Drop every cached contract (test isolation / targeted resets)."""
    _contract_cache.clear()


def normalise_categorical_levels(
    raw: Mapping[str, Iterable[str | None]] | None,
    *,
    features: Iterable[str] | None = None,
    categorical_features: Iterable[str] | None = None,
    path: Path | None = None,
) -> dict[str, list[str | None]]:
    """Validate and normalise declared categorical value domains.

    Domains are explicit metadata.  They are never inferred from row values.
    ``None`` is an explicit level for null values; all other levels must be
    non-empty strings.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise FeatureMismatchError(
            "categorical_levels must be a mapping of column name to level list",
            path=str(path) if path is not None else None,
            field="categorical_levels",
            expected_type="dict",
            actual_type=type(raw).__name__,
        )

    feature_set = set(features) if features is not None else None
    categorical_set = set(categorical_features) if categorical_features is not None else None
    normalised: dict[str, list[str | None]] = {}
    for column, levels in raw.items():
        if not isinstance(column, str) or not column:
            raise FeatureMismatchError(
                "categorical_levels column names must be non-empty strings",
                path=str(path) if path is not None else None,
                field="categorical_levels",
                column=column,
            )
        if feature_set is not None and column not in feature_set:
            raise FeatureMismatchError(
                "categorical_levels references a column outside the model features",
                path=str(path) if path is not None else None,
                field="categorical_levels",
                column=column,
                features=sorted(feature_set),
            )
        if categorical_set is not None and column not in categorical_set:
            raise FeatureMismatchError(
                "categorical_levels references a non-categorical feature",
                path=str(path) if path is not None else None,
                field="categorical_levels",
                column=column,
                categorical_features=sorted(categorical_set),
            )
        if isinstance(levels, (str, bytes)) or not isinstance(levels, Sequence):
            raise FeatureMismatchError(
                "categorical_levels values must be lists of non-empty strings or null",
                path=str(path) if path is not None else None,
                field="categorical_levels",
                column=column,
                expected_type="list",
                actual_type=type(levels).__name__,
            )
        ordered: list[str | None] = []
        seen: set[str | None] = set()
        for level in levels:
            if level is not None and (not isinstance(level, str) or not level):
                raise FeatureMismatchError(
                    "categorical levels must be non-empty strings or null",
                    path=str(path) if path is not None else None,
                    field="categorical_levels",
                    column=column,
                    level=level,
                )
            if level in seen:
                raise FeatureMismatchError(
                    "categorical_levels contains a duplicate level",
                    path=str(path) if path is not None else None,
                    field="categorical_levels",
                    column=column,
                    duplicate_level=level,
                )
            seen.add(level)
            ordered.append(level)
        if not ordered:
            raise FeatureMismatchError(
                "categorical_levels entries must declare at least one level",
                path=str(path) if path is not None else None,
                field="categorical_levels",
                column=column,
            )
        normalised_levels: list[str | None] = list(
            sorted(level for level in ordered if level is not None)
        )
        if None in seen:
            normalised_levels.append(None)
        normalised[column] = normalised_levels
    return normalised


def merge_categorical_level_declarations(
    declarations: Iterable[tuple[str, Mapping[str, Iterable[str | None]] | None]],
) -> dict[str, list[str | None]]:
    """Merge categorical level declarations from a graph boundary.

    Each declaration is explicit metadata supplied by a named owner, usually
    a source node or a modelScore node. Missing declarations are ignored, but
    two owners declaring different domains for the same column is a boundary
    error.
    """
    merged: dict[str, list[str | None]] = {}
    for owner, raw in declarations:
        for column, levels in normalise_categorical_levels(raw).items():
            existing = merged.get(column)
            if existing is not None and existing != levels:
                raise FeatureMismatchError(
                    "Conflicting categorical_levels declarations at modelScore boundary",
                    field="categorical_levels",
                    column=column,
                    existing_levels=existing,
                    conflicting_levels=levels,
                    source_node=owner,
                )
            merged[column] = levels
    return merged


def validate_categorical_value_domains(
    frame: Any,
    categorical_levels: Mapping[str, Iterable[str | None]],
    *,
    max_examples: int = 10,
) -> None:
    """Raise if observed categorical values fall outside declared domains.

    The validator never infers domains; it only checks rows against an
    explicit declaration.  Lazy inputs are collected through a narrow
    streaming projection per declared column so failures include examples
    without materialising the full frame.
    """
    import polars as pl

    from haute._execution_context import ExecutionProfile
    from haute._polars_utils import streaming_collect

    levels = normalise_categorical_levels(categorical_levels)
    if not levels:
        return
    lazy = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
    schema = lazy.collect_schema()
    invalid_example_exprs: list[pl.Expr] = []
    for column, allowed in levels.items():
        if column not in schema:
            raise FeatureMismatchError(
                "categorical_levels references a column missing from the input data",
                field="categorical_levels",
                column=column,
                missing=[column],
            )
        allow_null = any(level is None for level in allowed)
        allowed_strings = [level for level in allowed if level is not None]
        column_expr = pl.col(column)
        value_expr = column_expr.cast(pl.String)
        invalid_expr = column_expr.is_not_null() & ~value_expr.is_in(allowed_strings)
        if not allow_null:
            invalid_expr = column_expr.is_null() | invalid_expr
        invalid_example_exprs.append(
            value_expr.filter(invalid_expr)
            .unique(maintain_order=True)
            .head(max_examples)
            .implode()
            .alias(column)
        )

    examples = streaming_collect(
        lazy.select(invalid_example_exprs),
        profile=ExecutionProfile.TRAINING_PREP,
    )
    for column, allowed in levels.items():
        invalid_series = examples[column][0]
        invalid_values = (
            invalid_series.to_list()
            if hasattr(invalid_series, "to_list")
            else list(invalid_series or [])
        )
        if invalid_values:
            raise FeatureMismatchError(
                "categorical value outside declared categorical_levels",
                field="categorical_levels",
                column=column,
                invalid_levels=invalid_values,
                allowed_levels=list(allowed),
                truncated=len(invalid_values) >= max_examples,
            )


def assert_contracts_match(expected: FeatureContract, actual: FeatureContract) -> None:
    """Raise :class:`FeatureMismatchError` if any contract field differs.

    The error message names the offending field and shows expected vs
    actual values so the operator can act on the diff directly. Structured
    ``field`` / ``expected`` / ``actual`` context is also attached so log
    consumers and tests can introspect without parsing the message.
    """
    for field in _FIELDS:
        exp_val = getattr(expected, field)
        act_val = getattr(actual, field)
        if _normalise(exp_val) != _normalise(act_val):
            raise FeatureMismatchError(
                f"contract mismatch: {field}: expected={_show(exp_val)}, actual={_show(act_val)}",
                field=field,
                expected=_normalise(exp_val),
                actual=_normalise(act_val),
            )


def _normalise(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _show(value: Any) -> str:
    if isinstance(value, Mapping):
        return repr(dict(value))
    return repr(value)
