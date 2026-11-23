"""Stage settings are replayable and cannot silently enter the GBM parameter space."""

import json

import pytest
from t_boost import Band, Prune, PruningPathSettings, Recipe, Smooth

from haute.errors import HauteValidationError
from haute.modelling._staged_config import StagedConfig


def test_defaults_and_applied_recipe_are_independent_of_draft_controls():
    config = StagedConfig.from_plain_data({"schema_version": 2, "pruning": {"max_tables": 8}})
    assert config.applied_recipe == Recipe()
    assert config.output_stage == "full"
    assert config.operation("pruned") == Prune(max_tables=8)
    assert config.operation("banded") == Band()
    assert config.operation("smoothed") == Smooth()
    payload = config.to_plain_data()
    payload["applied_recipe"] = Recipe(
        (config.operation("pruned"), config.operation("banded"))
    ).to_plain_data()
    applied = StagedConfig.from_plain_data(json.loads(json.dumps(payload)))
    assert applied.output_stage == "banded"
    assert StagedConfig.from_plain_data(applied.to_plain_data()) == applied
    payload["applied_recipe"]["operations"][0]["settings"]["max_tables"] = 1
    assert applied.applied_recipe.operations[0].max_tables == 8
    assert applied.without_applied_recipe().applied_recipe == Recipe()
    assert applied.without_applied_recipe().to_plain_data()["pruning"]["max_tables"] == 8


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1},
        {"schema_version": 3},
        {"schema_version": True},
        {"extra": 1},
        {"applied_path": None},
        {"path": {"steps": 1}},
        {"path": {"main_effects": 1}},
        {"pruning": {"max_tables": True}},
        {"pruning": {"max_tables": 2, "fraction": 0.9}},
        {"banding": {"tolerance": float("nan")}},
        {"banding": {"parent": "smoothed"}},
        {"smoothing": {"high_order_alpha": 1.1}},
        {"smoothing": {"alpha": True}},
        {"smoothing": {"alpha": 1.0, "strength": 2.0}},
        {"selection": {"relative_tolerance": -1}},
        {"applied_recipe": []},
        {
            "applied_recipe": {
                "schema_version": 1,
                "operations": [Smooth("banded").to_plain_data()],
            }
        },
        {
            "applied_recipe": {
                "schema_version": 1,
                "operations": [{"stage": "pruned", "parent": "full", "settings": {"alpha": 1}}],
            }
        },
    ],
)
def test_invalid_settings_are_actionable(payload):
    with pytest.raises(HauteValidationError, match="stage"):
        StagedConfig.from_plain_data(payload)


def test_postfit_settings_are_not_gbm_or_tuning_parameters():
    config = StagedConfig.from_plain_data({})
    config.validate_gbm_params({"n_trees": 20, "learning_rate": 0.1})
    for key in ("prune", "prune_n_folds", "band_tolerance", "graduate", "graduation_alpha"):
        with pytest.raises(HauteValidationError, match=key):
            config.validate_gbm_params({key: 1})


def test_applied_recipe_carries_its_path_independently_of_pending_path_settings():
    applied = PruningPathSettings(steps=16, main_effects=True, min_rows=20)
    config = StagedConfig.from_plain_data(
        {
            "applied_recipe": Recipe((Prune(path=applied),)).to_plain_data(),
            "path": {"steps": 32, "min_rows": 99999, "main_effects": False},
        }
    )
    assert config.applied_recipe.operations[0].path == applied
    assert config.operation("pruned").path == PruningPathSettings(min_rows=99999)
    assert config.to_plain_data()["path"]["min_rows"] == 99999


@pytest.mark.parametrize("pruning", [False, True])
@pytest.mark.parametrize("banding", [False, True])
@pytest.mark.parametrize("smoothing", [False, True])
def test_enabled_phases_resolve_the_previous_enabled_parent(pruning, banding, smoothing):
    enabled = {"pruned": pruning, "banded": banding, "smoothed": smoothing}
    config = StagedConfig.from_plain_data({"enabled": enabled})
    assert config.to_plain_data()["enabled"] == enabled
    for stage, parent in (
        ("pruned", "full"),
        ("banded", "pruned" if pruning else "full"),
        ("smoothed", "banded" if banding else "pruned" if pruning else "full"),
    ):
        if enabled[stage]:
            assert config.operation(stage).parent == parent
        else:
            with pytest.raises(HauteValidationError, match="disabled"):
                config.operation(stage)


def test_a_parent_must_have_been_built_with_the_enabled_phases():
    config = StagedConfig.from_plain_data({"enabled": {"pruned": False}})
    config.require_parent("banded", Recipe((Band("full"),)))
    with pytest.raises(HauteValidationError, match="Rebuild banded"):
        config.require_parent("banded", Recipe((Prune(), Band("pruned"))))


@pytest.mark.parametrize("enabled", [{"pruned": 1}, {"unknown": True}, None])
def test_phase_flags_refuse_invalid_values(enabled):
    with pytest.raises(HauteValidationError, match="enabled"):
        StagedConfig.from_plain_data({"enabled": enabled})


def test_phase_drafts_leave_existing_applied_recipe_unchanged():
    recipe = Recipe((Prune(),)).to_plain_data()
    config = StagedConfig.from_plain_data({"enabled": {"pruned": False}, "applied_recipe": recipe})
    assert config.output_stage == "pruned"
    assert config.applied_recipe.operations[0] == Prune()
    legacy = StagedConfig.from_plain_data({"smoothing": {"parent": "full"}})
    assert legacy.operation("smoothed").parent == "full"
