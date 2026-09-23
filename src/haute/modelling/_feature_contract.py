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

Version 2 adds an optional :class:`ModelIdentity` section recording which
model the schema belongs to (algorithm, loss and link, binary class mapping,
engine and Haute versions). Training always writes it; a contract supplied
for a generic MLflow model may omit it. Only the schema fields are compared
against live data; the identity is checked against the loaded model.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from haute._stat_gated_cache import StatGatedCache, artifact_cache_key, resolve_artifact_path
from haute.errors import FeatureMismatchError

Task = Literal["classification", "regression"]

_FIELDS: tuple[str, ...] = (
    "features",
    "feature_types",
    "categorical_features",
    "categorical_levels",
    "target_name",
    "target_type",
    "task",
    "offset_column",
)
_ALL_KEYS: frozenset[str] = frozenset((*_FIELDS, "contract_hash", "contract_version", "model"))

#: The only contract format this release reads or writes.
CONTRACT_VERSION = 2
_LINKS = frozenset({"identity", "log", "logit"})
ClassLabel = bool | int | str

CONTRACT_FILENAME = "feature_contract.json"


@dataclasses.dataclass(frozen=True)
class ModelIdentity:
    """Which trained model a contract's schema belongs to."""

    algorithm: str
    link: str
    engine_name: str
    engine_version: str
    haute_version: str
    loss: str | None = None
    glm_family: str | None = None
    variance_power: float | None = None
    #: ``(negative, positive)`` for a binary classifier, else ``None``.
    class_labels: tuple[ClassLabel, ClassLabel] | None = None
    #: Original-to-native feature names where an engine restricts names.
    native_feature_names: dict[str, str] | None = None

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "link": self.link,
            "engine": {"name": self.engine_name, "version": self.engine_version},
            "haute_version": self.haute_version,
            "loss": self.loss,
            "glm_family": self.glm_family,
            "variance_power": self.variance_power,
            "class_labels": list(self.class_labels) if self.class_labels is not None else None,
            "native_feature_names": (
                dict(self.native_feature_names) if self.native_feature_names is not None else None
            ),
        }

    @classmethod
    def from_plain_data(cls, raw: Any, *, path: Path | str = "<memory>") -> ModelIdentity:
        def fail(message: str, **context: Any) -> FeatureMismatchError:
            return FeatureMismatchError(
                f"contract model identity {message}", path=str(path), **context
            )

        expected = {
            "algorithm",
            "link",
            "engine",
            "haute_version",
            "loss",
            "glm_family",
            "variance_power",
            "class_labels",
            "native_feature_names",
        }
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise fail("must hold exactly its documented fields", expected=sorted(expected))
        for key in ("algorithm", "link", "haute_version"):
            if not isinstance(raw[key], str) or not raw[key]:
                raise fail(f"field {key!r} must be a non-empty string")
        if raw["link"] not in _LINKS:
            raise fail("link must be identity, log, or logit", link=raw["link"])
        engine = raw["engine"]
        if (
            not isinstance(engine, Mapping)
            or set(engine) != {"name", "version"}
            or not all(isinstance(engine[k], str) and engine[k] for k in ("name", "version"))
        ):
            raise fail("engine must name its distribution and exact version")
        for key in ("loss", "glm_family"):
            if raw[key] is not None and (not isinstance(raw[key], str) or not raw[key]):
                raise fail(f"field {key!r} must be a non-empty string or null")
        power = raw["variance_power"]
        if power is not None and (isinstance(power, bool) or not isinstance(power, (int, float))):
            raise fail("variance_power must be a number or null")
        labels = raw["class_labels"]
        class_labels: tuple[ClassLabel, ClassLabel] | None = None
        if labels is not None:
            if (
                not isinstance(labels, list)
                or len(labels) != 2
                or not all(isinstance(label, (bool, int, str)) for label in labels)
                or type(labels[0]) is not type(labels[1])
                or labels[0] == labels[1]
            ):
                raise fail("class_labels must be two distinct labels of one type")
            class_labels = (labels[0], labels[1])
        names = raw["native_feature_names"]
        if names is not None and (
            not isinstance(names, Mapping)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in names.items())
        ):
            raise fail("native_feature_names must map strings to strings")
        return cls(
            algorithm=raw["algorithm"],
            link=raw["link"],
            engine_name=engine["name"],
            engine_version=engine["version"],
            haute_version=raw["haute_version"],
            loss=raw["loss"],
            glm_family=raw["glm_family"],
            variance_power=float(power) if power is not None else None,
            class_labels=class_labels,
            native_feature_names=dict(names) if names is not None else None,
        )


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
    # Offset/exposure column the model was trained with, or ``None``.  Not a
    # feature: it never enters the design matrix / pool, but every scoring
    # frame MUST carry it — served predictions include the offset effect.
    offset_column: str | None = None
    #: The trained model this schema belongs to; ``None`` for a contract
    #: supplied with a generic MLflow model or rebuilt from live data.
    model: ModelIdentity | None = None
    contract_version: int = CONTRACT_VERSION


def _canonical_payload(
    features: list[str],
    feature_types: Mapping[str, str],
    categorical_features: list[str],
    categorical_levels: Mapping[str, Iterable[str | None]] | None,
    target_name: str,
    target_type: str,
    task: str,
    offset_column: str | None = None,
    model: ModelIdentity | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "model": model.to_plain_data() if model is not None else None,
        "features": list(features),
        "feature_types": dict(feature_types),
        "categorical_features": list(categorical_features),
        "categorical_levels": {
            str(column): list(levels) for column, levels in (categorical_levels or {}).items()
        },
        "target_name": target_name,
        "target_type": target_type,
        "task": task,
        "offset_column": offset_column,
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
    offset_column: str | None = None,
    model: ModelIdentity | None = None,
) -> FeatureContract:
    """Construct a contract and compute its content hash.

    Inputs are accepted as plain lists / dicts and normalised to the
    contract's read-only forms.  ``contract_hash`` is the sha256 of the
    canonical-JSON representation of every field except itself.
    """
    if offset_column and offset_column in features:
        raise FeatureMismatchError(
            "offset_column shadows a feature name; the offset is a separate "
            "model input, not a design-matrix feature",
            field="offset_column",
            offset_column=offset_column,
        )
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
        offset_column or None,
        model,
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
        offset_column=offset_column or None,
        model=model,
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
        "offset_column": contract.offset_column,
        "contract_version": contract.contract_version,
        "model": contract.model.to_plain_data() if contract.model is not None else None,
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

    if raw.get("contract_version") != CONTRACT_VERSION:
        raise FeatureMismatchError(
            f"contract file is not a version-{CONTRACT_VERSION} feature contract; retrain the "
            "model to write a current contract",
            path=str(path),
            contract_version=raw.get("contract_version"),
        )
    keys = set(raw)
    missing = _ALL_KEYS - keys
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
    _check_type(raw, "categorical_levels", dict, path)
    _check_type(raw, "target_name", str, path)
    _check_type(raw, "target_type", str, path)
    _check_type(raw, "task", str, path)
    _check_type(raw, "contract_hash", str, path)
    if raw["offset_column"] is not None:
        _check_type(raw, "offset_column", str, path)

    categorical_levels = normalise_categorical_levels(
        raw["categorical_levels"],
        features=raw["features"],
        categorical_features=raw["categorical_features"],
        path=path,
    )
    model = (
        ModelIdentity.from_plain_data(raw["model"], path=path) if raw["model"] is not None else None
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
                raw["offset_column"],
                model,
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
        offset_column=raw["offset_column"],
        model=model,
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
    # The SLOT key is case-folded (normcase; a no-op on POSIX, so a macOS
    # case-variant spelling still gets its own slot — accepted, as in
    # haute._json_flatten._path_hash). The stat/open path keeps the on-disk
    # case: a folded spelling need not exist on a case-sensitive filesystem.
    io_path = resolve_artifact_path(path)
    return _contract_cache.get_or_load(
        artifact_cache_key(io_path),
        io_path,
        lambda: load_contract(io_path),
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

    examples = streaming_collect(lazy.select(invalid_example_exprs))
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
