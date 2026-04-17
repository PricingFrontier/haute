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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from haute.errors import FeatureMismatchError

Task = Literal["classification", "regression"]

_FIELDS: tuple[str, ...] = (
    "features",
    "feature_types",
    "categorical_features",
    "target_name",
    "target_type",
    "task",
)
_ALL_KEYS: frozenset[str] = frozenset((*_FIELDS, "contract_hash"))


@dataclasses.dataclass(frozen=True)
class FeatureContract:
    """Immutable record of the feature schema a model was trained against."""

    features: list[str]
    feature_types: dict[str, str]
    categorical_features: list[str]
    target_name: str
    target_type: str
    task: Task
    contract_hash: str


def _canonical_payload(
    features: list[str],
    feature_types: Mapping[str, str],
    categorical_features: list[str],
    target_name: str,
    target_type: str,
    task: str,
) -> dict[str, Any]:
    return {
        "features": list(features),
        "feature_types": dict(feature_types),
        "categorical_features": list(categorical_features),
        "target_name": target_name,
        "target_type": target_type,
        "task": task,
    }


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
) -> FeatureContract:
    """Construct a contract and compute its content hash.

    Inputs are accepted as plain lists / dicts and normalised to the
    contract's read-only forms.  ``contract_hash`` is the sha256 of the
    canonical-JSON representation of every field except itself.
    """
    payload = _canonical_payload(
        features,
        feature_types,
        categorical_features,
        target_name,
        target_type,
        task,
    )
    return FeatureContract(
        features=list(features),
        feature_types=dict(feature_types),
        categorical_features=list(categorical_features),
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
        "target_name": contract.target_name,
        "target_type": contract.target_type,
        "task": contract.task,
        "contract_hash": contract.contract_hash,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_contract(path: Path | str) -> FeatureContract:
    """Read and validate a contract written by :func:`save_contract`."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FeatureMismatchError(
            f"contract file {path} must contain a JSON object, got {type(raw).__name__}",
        )

    keys = set(raw)
    missing = _ALL_KEYS - keys
    if missing:
        raise FeatureMismatchError(
            f"contract file {path} is missing required field(s): {sorted(missing)}",
        )
    unknown = keys - _ALL_KEYS
    if unknown:
        raise FeatureMismatchError(
            f"contract file {path} has unknown top-level field(s): {sorted(unknown)}",
        )

    _check_type(raw, "features", list, path)
    _check_type(raw, "feature_types", dict, path)
    _check_type(raw, "categorical_features", list, path)
    _check_type(raw, "target_name", str, path)
    _check_type(raw, "target_type", str, path)
    _check_type(raw, "task", str, path)
    _check_type(raw, "contract_hash", str, path)

    return FeatureContract(
        features=list(raw["features"]),
        feature_types=dict(raw["feature_types"]),
        categorical_features=list(raw["categorical_features"]),
        target_name=raw["target_name"],
        target_type=raw["target_type"],
        task=raw["task"],
        contract_hash=raw["contract_hash"],
    )


def _check_type(payload: dict[str, Any], key: str, expected: type, path: Path) -> None:
    if not isinstance(payload[key], expected):
        actual = type(payload[key]).__name__
        raise FeatureMismatchError(
            f"contract file {path}: field {key!r} must be "
            f"{expected.__name__}, got {actual}",
        )


def assert_contracts_match(expected: FeatureContract, actual: FeatureContract) -> None:
    """Raise :class:`FeatureMismatchError` if any contract field differs.

    The error message names the offending field and shows expected vs
    actual values so the operator can act on the diff directly.
    """
    for field in _FIELDS:
        exp_val = getattr(expected, field)
        act_val = getattr(actual, field)
        if _normalise(exp_val) != _normalise(act_val):
            raise FeatureMismatchError(
                f"contract mismatch: {field}: expected={_show(exp_val)}, "
                f"actual={_show(act_val)}",
            )


def _normalise(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _show(value: Any) -> str:
    if isinstance(value, Mapping):
        return repr(dict(value))
    return repr(value)
