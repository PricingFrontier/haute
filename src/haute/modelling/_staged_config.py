"""Versioned t-boost stage settings shared by jobs, scripts and the editor.

The draft controls and the enabled phases are Haute's. What a draft means is t-boost's:
each one becomes a t-boost operation, which validates itself, and the applied policy is a
t-boost ``Recipe``. Haute keeps only its own selection tolerances.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from haute.errors import HauteValidationError

if TYPE_CHECKING:
    from t_boost import Operation, Recipe

SCHEMA_VERSION = 2
STAGE_NAMES = ("full", "pruned", "banded", "smoothed")
_DEFAULTS: dict[str, dict[str, Any]] = {
    "path": {"steps": 32, "main_effects": False, "min_rows": 500},
    "pruning": {"fraction": 0.995, "tolerance": 0.001, "max_tables": None},
    "banding": {"parent": "pruned", "tolerance": 0.75, "deviance_cap": 0.001},
    "smoothing": {"parent": "banded", "alpha": None, "high_order_alpha": 0.0, "strength": 1.0},
    "selection": {"relative_tolerance": 0.001, "absolute_tolerance": 1e-10},
}
_SECTIONS = {"pruned": "pruning", "banded": "banding", "smoothed": "smoothing"}


def _fail(message: str) -> None:
    raise HauteValidationError(f"t-boost stages: {message}")


def _mapping(raw: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) - keys:
        _fail(f"{label} must be an object containing only {', '.join(sorted(keys))}")
    assert isinstance(raw, Mapping)
    return copy.deepcopy(dict(raw))


def _tolerance(value: object, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        _fail(f"{label} must be a finite number >= 0")


def _operation(section: str, values: Mapping[str, Any], path: Mapping[str, Any]) -> Operation:
    """The t-boost operation a draft section describes; t-boost validates it."""
    from t_boost import Band, Prune, PruningPathSettings, Smooth

    try:
        if section == "pruning":
            return Prune(path=PruningPathSettings(**path), **values)
        if section == "banding":
            return Band(**values)
        return Smooth(**values)
    except ValueError as exc:
        _fail(f"{section}: {exc}")
        raise  # unreachable: _fail raises


def _draft(section: str, raw: object, path: Mapping[str, Any]) -> dict[str, Any]:
    values = {**_DEFAULTS[section], **_mapping(raw, set(_DEFAULTS[section]), section)}
    if section == "selection":
        _tolerance(values["relative_tolerance"], "selection.relative_tolerance")
        _tolerance(values["absolute_tolerance"], "selection.absolute_tolerance")
    elif section == "path":
        from t_boost import PruningPathSettings

        try:
            PruningPathSettings(**values)
        except ValueError as exc:
            _fail(f"path: {exc}")
    else:
        _operation(section, values, path)
    return values


def _recipe(raw: object) -> Recipe:
    from t_boost import Recipe

    try:
        return Recipe.from_plain_data(raw)
    except ValueError as exc:
        _fail(f"applied_recipe: {exc}")
        raise  # unreachable: _fail raises


@dataclass(frozen=True)
class StagedConfig:
    """Validated draft controls and the applied recipe; projections are independent copies."""

    _value: dict[str, Any]
    _applied: Recipe

    @classmethod
    def from_plain_data(cls, raw: object) -> StagedConfig:
        from t_boost import Recipe

        value = _mapping(
            raw, {"schema_version", *_DEFAULTS, "applied_recipe", "enabled"}, "configuration"
        )
        version = value.get("schema_version", SCHEMA_VERSION)
        if type(version) is not int or version != SCHEMA_VERSION:
            _fail(f"unsupported schema_version; expected {SCHEMA_VERSION}")
        path = _draft("path", value.get("path", {}), {})
        sections = {
            name: path if name == "path" else _draft(name, value.get(name, {}), path)
            for name in _DEFAULTS
        }
        enabled: dict[str, Any] | None = None
        if "enabled" in value:
            enabled = {
                **dict.fromkeys(_SECTIONS, True),
                **_mapping(value["enabled"], set(_SECTIONS), "enabled"),
            }
            if any(type(flag) is not bool for flag in enabled.values()):
                _fail("enabled phase values must be booleans")
            sections["banding"]["parent"] = "pruned" if enabled["pruned"] else "full"
            sections["smoothing"]["parent"] = (
                "banded" if enabled["banded"] else "pruned" if enabled["pruned"] else "full"
            )
        applied = _recipe(value.get("applied_recipe", Recipe().to_plain_data()))
        return cls(
            {
                "schema_version": SCHEMA_VERSION,
                **sections,
                **({"enabled": enabled} if enabled is not None else {}),
            },
            applied,
        )

    def to_plain_data(self) -> dict[str, Any]:
        return {**copy.deepcopy(self._value), "applied_recipe": self._applied.to_plain_data()}

    @property
    def applied_recipe(self) -> Recipe:
        """The applied policy: what a final or fold fit replays."""
        return self._applied

    @property
    def output_stage(self) -> str:
        return self._applied.stage

    def without_applied_recipe(self) -> StagedConfig:
        """The same drafts with nothing applied: a fresh fit's full GBM."""
        from t_boost import Recipe

        return StagedConfig(copy.deepcopy(self._value), Recipe())

    def operation(self, stage: str) -> Operation:
        """The draft operation for ``stage``; pruning carries the draft path settings."""
        if stage not in _SECTIONS:
            _fail(f"unknown post-fit stage {stage!r}")
        if not self._value.get("enabled", {}).get(stage, True):
            _fail(f"{stage} is disabled; enable that phase before running it")
        section = _SECTIONS[stage]
        return _operation(section, self._value[section], self._value["path"])

    def require_parent(self, parent: str, recipe: Recipe) -> None:
        """Refuse a parent stage that was not built with the enabled phases."""
        enabled = self._value.get("enabled")
        if enabled is None:
            return
        expected = [
            stage for stage in STAGE_NAMES[1 : STAGE_NAMES.index(parent) + 1] if enabled[stage]
        ]
        if [operation.stage for operation in recipe.operations] != expected:
            _fail(f"Rebuild {parent} with the enabled phases before using it as a parent")

    def validate_gbm_params(self, params: Mapping[str, Any]) -> None:
        for name in params:
            if name == "prune" or name.startswith("prune_"):
                _fail(f"{name} belongs to pruning, not GBM parameters or its tuning space")
            if name.startswith("band_"):
                _fail(f"{name} belongs to banding, not GBM parameters or its tuning space")
            if name == "graduate" or name.startswith("graduation_"):
                _fail(f"{name} belongs to smoothing, not GBM parameters or its tuning space")


def resolve_stages(raw: object, algorithm: str, params: Mapping[str, Any]) -> StagedConfig | None:
    """Validate opt-in without reinterpreting legacy flat configurations."""
    if raw is None:
        return None
    from haute.modelling._descriptors import algorithm_descriptor

    if not algorithm_descriptor(algorithm).staged_training:
        _fail(f"{algorithm} does not support staged training")
    config = StagedConfig.from_plain_data(raw)
    config.validate_gbm_params(params)
    return config
