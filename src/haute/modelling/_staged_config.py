"""Versioned, replayable t-boost stage settings shared by jobs, scripts and the editor."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from haute.errors import HauteValidationError

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


def _integer(value: object, minimum: int, label: str) -> None:
    if type(value) is not int or value < minimum:
        _fail(f"{label} must be an integer >= {minimum}")


def _number(
    value: object, minimum: float, maximum: float | None, label: str, *, positive: bool = False
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or (positive and value == minimum)
        or (maximum is not None and value > maximum)
    ):
        _fail(
            f"{label} must be a finite number {'>' if positive else '>='} {minimum}"
            + (f" and <= {maximum}" if maximum is not None else "")
        )


def _settings(section: str, raw: object) -> dict[str, Any]:
    values = {**_DEFAULTS[section], **_mapping(raw, set(_DEFAULTS[section]), section)}
    if section == "path":
        _integer(values["steps"], 2, "path.steps")
        _integer(values["min_rows"], 1, "path.min_rows")
        if type(values["main_effects"]) is not bool:
            _fail("path.main_effects must be a boolean")
    elif section == "pruning":
        _number(values["fraction"], 0, 1, "pruning.fraction", positive=True)
        _number(values["tolerance"], 0, None, "pruning.tolerance")
        if values["max_tables"] is not None:
            _integer(values["max_tables"], 0, "pruning.max_tables")
            if values["fraction"] != 0.995 or values["tolerance"] != 0.001:
                _fail("pruning.max_tables cannot be combined with non-default fraction/tolerance")
    elif section == "banding":
        if values["parent"] not in ("full", "pruned"):
            _fail("banding.parent must be full or pruned")
        _number(values["tolerance"], 0, None, "banding.tolerance", positive=True)
        _number(values["deviance_cap"], 0, None, "banding.deviance_cap", positive=True)
    elif section == "selection":
        _number(values["relative_tolerance"], 0, None, "selection.relative_tolerance")
        _number(values["absolute_tolerance"], 0, None, "selection.absolute_tolerance")
    else:
        if values["parent"] not in ("full", "pruned", "banded"):
            _fail("smoothing.parent must be full, pruned or banded")
        if values["alpha"] is not None:
            _number(values["alpha"], 0, None, "smoothing.alpha")
        _number(values["high_order_alpha"], 0, 1, "smoothing.high_order_alpha")
        _number(values["strength"], 0, None, "smoothing.strength", positive=True)
        if values["alpha"] is not None and values["strength"] != 1:
            _fail("manual smoothing alpha requires strength 1")
    return values


def _recipe(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 3:
        _fail("applied_recipe must be a list of at most three operations")
    assert isinstance(raw, list)
    result = []
    parent = "full"
    for item in raw:
        op = _mapping(item, {"stage", "parent", "settings"}, "recipe operation")
        if set(op) != {"stage", "parent", "settings"}:
            _fail("recipe operations require stage, parent and settings")
        name = op["stage"]
        if not isinstance(name, str) or name not in _SECTIONS or op["parent"] != parent:
            _fail("recipe operations must form a continuous stage chain starting from full")
        if STAGE_NAMES.index(name) <= STAGE_NAMES.index(parent):
            _fail("recipe stages must proceed from full to pruned, banded or smoothed")
        section = _SECTIONS[name]
        keys = set(_DEFAULTS[section]) - {"parent"}
        settings = _mapping(op["settings"], keys, f"{name} settings")
        if name != "pruned":
            settings["parent"] = parent
        settings = _settings(section, settings)
        settings.pop("parent", None)
        if name == "smoothed" and settings.get("strength") == 1:
            settings.pop("strength")
        result.append({"stage": name, "parent": parent, "settings": settings})
        parent = name
    return result


@dataclass(frozen=True)
class StagedConfig:
    """Validated settings; public projections are independent copies."""

    _value: dict[str, Any]

    @classmethod
    def from_plain_data(cls, raw: object) -> StagedConfig:
        value = _mapping(
            raw,
            {"schema_version", *_DEFAULTS, "applied_recipe", "applied_path", "enabled"},
            "configuration",
        )
        version = value.get("schema_version", 1)
        if type(version) is not int or version != 1:
            _fail("unsupported schema_version; expected 1")
        sections = {name: _settings(name, value.get(name, {})) for name in _DEFAULTS}
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
        return cls(
            {
                "schema_version": 1,
                **sections,
                **({"enabled": enabled} if enabled is not None else {}),
                "applied_recipe": _recipe(value.get("applied_recipe", [])),
                "applied_path": _settings("path", value["applied_path"])
                if value.get("applied_path") is not None
                else None,
            }
        )

    def to_plain_data(self) -> dict[str, Any]:
        return copy.deepcopy(self._value)

    @property
    def applied_recipe(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._value["applied_recipe"])

    @property
    def output_stage(self) -> str:
        recipe = self.applied_recipe
        return str(recipe[-1]["stage"]) if recipe else "full"

    @property
    def path_params(self) -> dict[str, Any]:
        value = (
            self._value["applied_path"]
            if self._value["applied_path"] is not None
            and any(op["stage"] == "pruned" for op in self.applied_recipe)
            else self._value["path"]
        )
        return {
            "prune_path_steps": value["steps"],
            "prune_main_effects": value["main_effects"],
            "prune_guard_min_rows": value["min_rows"],
        }

    def operation(self, stage: str) -> dict[str, Any]:
        if stage not in _SECTIONS:
            _fail(f"unknown post-fit stage {stage!r}")
        if not self._value.get("enabled", {}).get(stage, True):
            _fail(f"{stage} is disabled; enable that phase before running it")
        settings = copy.deepcopy(self._value[_SECTIONS[stage]])
        parent = settings.pop("parent", "full")
        return {"stage": stage, "parent": parent, "settings": settings}

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
