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

Version 2 added an optional :class:`ModelIdentity` section recording which
model the schema belongs to (algorithm, loss and link, binary class mapping,
engine and Haute versions). Training always writes it; a contract supplied
for a generic MLflow model may omit it. Only the schema fields are compared
against live data; the identity is checked against the loaded model.

Version 3 adds ``offset_link``: how the offset column enters the model's raw
score (``log`` or ``identity``), so a contract can declare the baseline of a
model whose file does not record it.

Version 4 adds ``training_levels``: the values each categorical column held in
the rows the model was fitted on. They are never compared against live data
and never refuse a value; scoring counts and reports the values outside them
(:func:`count_unseen_categorical_values`).
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
    "training_levels",
    "target_name",
    "target_type",
    "task",
    "offset_column",
    "offset_link",
)
_ALL_KEYS: frozenset[str] = frozenset((*_FIELDS, "contract_hash", "contract_version", "model"))
#: The fields live data can disagree with; the offset link, like the model
#: identity, declares how the model applies its offset and is checked against
#: the loaded model instead, and the training levels only drive the
#: unseen-value report.
_LIVE_FIELDS: tuple[str, ...] = tuple(
    field for field in _FIELDS if field not in {"offset_link", "training_levels"}
)

#: The only contract format this release reads or writes.
CONTRACT_VERSION = 4

#: The log event and execution-warning code of a categorical value the
#: model's training rows never held.
UNSEEN_CATEGORICAL_VALUES_CODE = "categorical_values_not_in_training"
#: Examples an unseen-value report names per feature.
_UNSEEN_EXAMPLES = 5
_LINKS = frozenset({"identity", "log", "logit"})
#: How an offset column can enter a model's raw score.
OFFSET_LINKS = frozenset({"identity", "log"})
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
    #: The values each categorical column held in the rows the model was
    #: fitted on (``None`` when they held nulls). Only the unseen-value report
    #: reads them.
    training_levels: dict[str, list[str | None]]
    target_name: str
    target_type: str
    task: Task
    contract_hash: str
    # Offset/exposure column the model was trained with, or ``None``.  Not a
    # feature: it never enters the design matrix / pool, but every scoring
    # frame MUST carry it — served predictions include the offset effect.
    offset_column: str | None = None
    #: How the offset enters the raw score (``log`` or ``identity``). ``None``
    #: without an offset, or beside one for a model that applies its own.
    offset_link: str | None = None
    #: The trained model this schema belongs to; ``None`` for a contract
    #: supplied with a generic MLflow model or rebuilt from live data.
    model: ModelIdentity | None = None
    contract_version: int = CONTRACT_VERSION


def _canonical_payload(
    features: list[str],
    feature_types: Mapping[str, str],
    categorical_features: list[str],
    categorical_levels: Mapping[str, Iterable[str | None]] | None,
    training_levels: Mapping[str, Iterable[str | None]],
    target_name: str,
    target_type: str,
    task: str,
    offset_column: str | None = None,
    offset_link: str | None = None,
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
        "training_levels": {
            str(column): list(levels) for column, levels in training_levels.items()
        },
        "target_name": target_name,
        "target_type": target_type,
        "task": task,
        "offset_column": offset_column,
        "offset_link": offset_link,
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
    offset_link: str | None = None,
    training_levels: Mapping[str, Iterable[str | None]] | None = None,
) -> FeatureContract:
    """Construct a contract and compute its content hash.

    Inputs are accepted as plain lists / dicts and normalised to the
    contract's read-only forms.  ``contract_hash`` is the sha256 of the
    canonical-JSON representation of every field except itself. A contract
    rebuilt from live data, or supplied for a generic model, passes no
    ``training_levels`` and records none.
    """
    _check_offset_link(offset_column or None, offset_link, "<memory>")
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
    normalised_training_levels = normalise_training_levels(training_levels, features=features)
    payload = _canonical_payload(
        features,
        feature_types,
        categorical_features,
        normalised_levels,
        normalised_training_levels,
        target_name,
        target_type,
        task,
        offset_column or None,
        offset_link,
        model,
    )
    return FeatureContract(
        features=list(features),
        feature_types=dict(feature_types),
        categorical_features=list(categorical_features),
        categorical_levels=normalised_levels,
        training_levels=normalised_training_levels,
        target_name=target_name,
        target_type=target_type,
        task=task,
        contract_hash=_hash_payload(payload),
        offset_column=offset_column or None,
        offset_link=offset_link,
        model=model,
    )


def _check_offset_link(offset_column: str | None, offset_link: Any, path: Path | str) -> None:
    """Refuse an offset link that is not ``log``/``identity`` or names no column."""
    if offset_link is None:
        return
    if not isinstance(offset_link, str) or offset_link not in OFFSET_LINKS:
        raise FeatureMismatchError(
            "contract offset_link must be 'log', 'identity' or null",
            path=str(path),
            field="offset_link",
            offset_link=offset_link,
        )
    if offset_column is None:
        raise FeatureMismatchError(
            "contract offset_link is set but offset_column is null; an offset link "
            "describes an offset column",
            path=str(path),
            field="offset_link",
            offset_link=offset_link,
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
        "training_levels": {
            column: list(levels) for column, levels in contract.training_levels.items()
        },
        "target_name": contract.target_name,
        "target_type": contract.target_type,
        "task": contract.task,
        "contract_hash": contract.contract_hash,
        "offset_column": contract.offset_column,
        "offset_link": contract.offset_link,
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
    _check_type(raw, "training_levels", dict, path)
    _check_type(raw, "target_name", str, path)
    _check_type(raw, "target_type", str, path)
    _check_type(raw, "task", str, path)
    _check_type(raw, "contract_hash", str, path)
    if raw["offset_column"] is not None:
        _check_type(raw, "offset_column", str, path)
    _check_offset_link(raw["offset_column"], raw["offset_link"], path)

    categorical_levels = normalise_categorical_levels(
        raw["categorical_levels"],
        features=raw["features"],
        categorical_features=raw["categorical_features"],
        path=path,
    )
    training_levels = normalise_training_levels(
        raw["training_levels"], features=raw["features"], path=path
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
                training_levels,
                raw["target_name"],
                raw["target_type"],
                raw["task"],
                raw["offset_column"],
                raw["offset_link"],
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
        training_levels=training_levels,
        target_name=raw["target_name"],
        target_type=raw["target_type"],
        task=raw["task"],
        contract_hash=raw["contract_hash"],
        offset_column=raw["offset_column"],
        offset_link=raw["offset_link"],
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


def normalise_training_levels(
    raw: Mapping[str, Iterable[str | None]] | None,
    *,
    features: Iterable[str] | None = None,
    path: Path | None = None,
) -> dict[str, list[str | None]]:
    """Validate and normalise the values each column held in the training rows.

    Unlike a declared domain these are observed, so an empty string is a value
    like any other; ``None`` records that the rows held nulls. Levels are
    sorted, with ``None`` last.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise FeatureMismatchError(
            "training_levels must be a mapping of column name to level list",
            path=str(path) if path is not None else None,
            field="training_levels",
            expected_type="dict",
            actual_type=type(raw).__name__,
        )
    feature_set = set(features) if features is not None else None
    normalised: dict[str, list[str | None]] = {}
    for column, levels in raw.items():
        if not isinstance(column, str) or not column:
            raise FeatureMismatchError(
                "training_levels column names must be non-empty strings",
                path=str(path) if path is not None else None,
                field="training_levels",
                column=column,
            )
        if feature_set is not None and column not in feature_set:
            raise FeatureMismatchError(
                "training_levels references a column outside the model features",
                path=str(path) if path is not None else None,
                field="training_levels",
                column=column,
                features=sorted(feature_set),
            )
        if isinstance(levels, (str, bytes)) or not isinstance(levels, Sequence):
            raise FeatureMismatchError(
                "training_levels values must be lists of strings or null",
                path=str(path) if path is not None else None,
                field="training_levels",
                column=column,
                expected_type="list",
                actual_type=type(levels).__name__,
            )
        seen: set[str | None] = set()
        for level in levels:
            if level is not None and not isinstance(level, str):
                raise FeatureMismatchError(
                    "training levels must be strings or null",
                    path=str(path) if path is not None else None,
                    field="training_levels",
                    column=column,
                    level=level,
                )
            if level in seen:
                raise FeatureMismatchError(
                    "training_levels contains a duplicate level",
                    path=str(path) if path is not None else None,
                    field="training_levels",
                    column=column,
                    duplicate_level=level,
                )
            seen.add(level)
        values: list[str | None] = [*sorted(level for level in seen if level is not None)]
        if None in seen:
            values.append(None)
        normalised[column] = values
    return normalised


def observed_training_levels(frame: Any, columns: Iterable[str]) -> dict[str, list[str | None]]:
    """The values each of *columns* holds in the eager *frame*, as training levels.

    Values are compared as strings, so an integer column used as a category
    records ``"3"`` for ``3``; ``None`` records that the column holds nulls.
    """
    import polars as pl

    levels: dict[str, list[str | None]] = {}
    for column in dict.fromkeys(columns):
        series = frame.get_column(column).cast(pl.String)
        values: list[str | None] = sorted(
            value for value in series.unique().to_list() if value is not None
        )
        if series.null_count():
            values.append(None)
        levels[column] = values
    return levels


@dataclasses.dataclass(frozen=True)
class UnseenCategoricalValues:
    """One feature's values outside the levels its training rows held."""

    feature: str
    #: Each value the training rows never held, as a string, with its rows.
    value_rows: dict[str, int]
    #: Null rows, counted only when the training rows held no null.
    null_rows: int

    @property
    def rows(self) -> int:
        return sum(self.value_rows.values())

    @property
    def examples(self) -> list[str]:
        return sorted(self.value_rows)[:_UNSEEN_EXAMPLES]


def count_unseen_categorical_values(
    frame: Any,
    training_levels: Mapping[str, Iterable[str | None]],
) -> dict[str, UnseenCategoricalValues]:
    """Count, per feature, the values of *frame* its training rows never held.

    Never raises for a value: a categorical value outside the training levels
    is scored by the model's own mechanism and only reported. A lazy frame is
    read through one narrow streaming projection. A column the frame lacks is
    skipped, because feature validation refuses a missing feature first.
    Returns only the features with something to report.
    """
    import polars as pl

    from haute._polars_utils import streaming_collect

    lazy = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
    schema = lazy.collect_schema()
    present = {
        column: list(levels) for column, levels in training_levels.items() if column in schema
    }
    if not present:
        return {}
    exprs: list[pl.Expr] = []
    for index, (column, levels) in enumerate(present.items()):
        known = [level for level in levels if level is not None]
        value = pl.col(column).cast(pl.String)
        exprs.append(
            value.filter(value.is_not_null() & ~value.is_in(known))
            .value_counts(name="rows")
            .implode()
            .alias(f"values_{index}")
        )
        null_count = pl.col(column).is_null().sum() if None not in levels else pl.lit(0)
        exprs.append(null_count.cast(pl.Int64).alias(f"nulls_{index}"))
    counted = streaming_collect(lazy.select(exprs))
    unseen: dict[str, UnseenCategoricalValues] = {}
    for index, column in enumerate(present):
        value_rows = {
            str(entry[column]): int(entry["rows"])
            for entry in counted.get_column(f"values_{index}")[0].to_list()
        }
        null_rows = int(counted.get_column(f"nulls_{index}")[0])
        if value_rows or null_rows:
            unseen[column] = UnseenCategoricalValues(column, value_rows, null_rows)
    return unseen


def merge_unseen_categorical_values(
    total: Mapping[str, UnseenCategoricalValues],
    more: Mapping[str, UnseenCategoricalValues],
) -> dict[str, UnseenCategoricalValues]:
    """*total* with *more* added, for a score counted chunk by chunk."""
    merged = dict(total)
    for feature, unseen in more.items():
        previous = merged.get(feature)
        if previous is None:
            merged[feature] = unseen
            continue
        value_rows = dict(previous.value_rows)
        for value, rows in unseen.value_rows.items():
            value_rows[value] = value_rows.get(value, 0) + rows
        merged[feature] = UnseenCategoricalValues(
            feature, value_rows, previous.null_rows + unseen.null_rows
        )
    return merged


def report_unseen_categorical_values(
    unseen: Mapping[str, UnseenCategoricalValues],
    *,
    context: str,
    node_id: str | None = None,
) -> None:
    """Log each feature's unseen values and record them as an execution warning.

    The warning is what the editor's preview shows; a deployed model has only
    the log.
    """
    from haute._execution_context import current_execution_context
    from haute._logging import get_logger

    logger = get_logger(component="feature_contract")
    execution_context = current_execution_context()
    for feature, counts in unseen.items():
        logger.warning(
            UNSEEN_CATEGORICAL_VALUES_CODE,
            feature=feature,
            values=len(counts.value_rows),
            rows=counts.rows,
            null_rows=counts.null_rows,
            examples=counts.examples,
            context=context,
            node_id=node_id,
        )
        if execution_context is not None:
            execution_context.record_execution_warning(
                UNSEEN_CATEGORICAL_VALUES_CODE,
                node_id=node_id,
                reason=unseen_categorical_reason(counts, context),
            )


def unseen_categorical_reason(counts: UnseenCategoricalValues, context: str) -> str:
    """One feature's unseen values as the sentence the preview shows."""
    parts: list[str] = []
    if counts.value_rows:
        values = len(counts.value_rows)
        shown = ", ".join(repr(value) for value in counts.examples)
        more = ", ..." if values > len(counts.examples) else ""
        parts.append(
            f"{values} {'value' if values == 1 else 'values'} not in the training data, "
            f"in {counts.rows} {'row' if counts.rows == 1 else 'rows'} ({shown}{more})"
        )
    if counts.null_rows:
        parts.append(
            f"{counts.null_rows} missing {'value' if counts.null_rows == 1 else 'values'}, "
            "though the training data had none"
        )
    return f"{context}: '{counts.feature}' has {' and '.join(parts)}."


def assert_contracts_match(expected: FeatureContract, actual: FeatureContract) -> None:
    """Raise :class:`FeatureMismatchError` if any contract field differs.

    The error message names the offending field and shows expected vs
    actual values so the operator can act on the diff directly. Structured
    ``field`` / ``expected`` / ``actual`` context is also attached so log
    consumers and tests can introspect without parsing the message.
    """
    for field in _LIVE_FIELDS:
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
