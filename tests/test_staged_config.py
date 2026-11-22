"""Stage settings are replayable and cannot silently enter the GBM parameter space."""

import pytest

from haute.errors import HauteValidationError
from haute.modelling._staged_config import StagedConfig


def test_defaults_and_applied_recipe_are_independent_of_draft_controls():
    config = StagedConfig.from_plain_data({"schema_version": 1, "pruning": {"max_tables": 8}})
    assert config.applied_recipe == []
    assert config.operation("pruned") == {
        "stage": "pruned",
        "parent": "full",
        "settings": {"fraction": 0.995, "tolerance": 0.001, "max_tables": 8},
    }
    payload = config.to_plain_data()
    payload["applied_recipe"] = [config.operation("pruned"), config.operation("banded")]
    applied = StagedConfig.from_plain_data(payload)
    assert applied.output_stage == "banded"
    assert StagedConfig.from_plain_data(applied.to_plain_data()) == applied
    payload["applied_recipe"][0]["settings"]["max_tables"] = 1
    assert applied.applied_recipe[0]["settings"]["max_tables"] == 8


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"extra": 1},
        {"path": {"steps": 1}},
        {"path": {"main_effects": 1}},
        {"pruning": {"max_tables": True}},
        {"pruning": {"max_tables": 2, "fraction": 0.9}},
        {"banding": {"tolerance": float("nan")}},
        {"banding": {"parent": "smoothed"}},
        {"smoothing": {"high_order_alpha": 1.1}},
        {"smoothing": {"alpha": True}},
        {"applied_recipe": [{"stage": "smoothed", "parent": "banded", "settings": {}}]},
        {"applied_recipe": [{"stage": "pruned", "parent": "full", "settings": {"alpha": 1}}]},
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


def test_applied_path_is_independent_of_pending_path_settings():
    recipe = [{"stage": "pruned", "parent": "full", "settings": {}}]
    config = StagedConfig.from_plain_data(
        {
            "applied_recipe": recipe,
            "applied_path": {"steps": 16, "min_rows": 20, "main_effects": True},
            "path": {"steps": 32, "min_rows": 99999, "main_effects": False},
        }
    )
    assert config.path_params == {
        "prune_path_steps": 16,
        "prune_guard_min_rows": 20,
        "prune_main_effects": True,
    }
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
            assert config.operation(stage)["parent"] == parent
        else:
            with pytest.raises(HauteValidationError, match="disabled"):
                config.operation(stage)


@pytest.mark.parametrize("enabled", [{"pruned": 1}, {"unknown": True}, None])
def test_phase_flags_refuse_invalid_values(enabled):
    with pytest.raises(HauteValidationError, match="enabled"):
        StagedConfig.from_plain_data({"enabled": enabled})


def test_phase_drafts_leave_existing_applied_recipe_unchanged():
    recipe = [{"stage": "pruned", "parent": "full", "settings": {}}]
    config = StagedConfig.from_plain_data({"enabled": {"pruned": False}, "applied_recipe": recipe})
    assert config.output_stage == "pruned"
    assert config.applied_recipe[0]["stage"] == "pruned"
    legacy = StagedConfig.from_plain_data({"smoothing": {"parent": "full"}})
    assert legacy.operation("smoothed")["parent"] == "full"
