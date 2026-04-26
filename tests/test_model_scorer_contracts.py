"""Focused contract tests for haute._model_scorer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute._model_scorer import (
    _predict_positive_proba,
    _scenario_ctx,
    score_frame,
    score_from_config,
)
from haute.errors import ConfigError


def test_predict_positive_proba_uses_positive_class_column_when_two_dimensional() -> None:
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.2, 0.8], [0.7, 0.3]])

    result = _predict_positive_proba(model, np.array([[1.0], [2.0]]))

    assert result is not None
    assert result.tolist() == [0.8, 0.3]


def test_predict_positive_proba_accepts_single_column_two_dimensional_output() -> None:
    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.8], [0.3]])

    result = _predict_positive_proba(model, np.array([[1.0], [2.0]]))

    assert result is not None
    assert result.tolist() == [0.8, 0.3]


def test_predict_positive_proba_flattens_one_dimensional_output() -> None:
    model = MagicMock()
    model.predict_proba.return_value = np.array([0.8, 0.3])

    result = _predict_positive_proba(model, np.array([[1.0], [2.0]]))

    assert result is not None
    assert result.tolist() == [0.8, 0.3]


def test_score_frame_rejects_unsupported_flavor() -> None:
    with pytest.raises(ConfigError, match="Unsupported scoring flavor"):
        score_frame(
            model=MagicMock(),
            lf=pl.DataFrame({"a": [1.0]}).lazy(),
            features=["a"],
            cat_feature_names=frozenset(),
            flavor="made_up",
        )


def test_score_from_config_respects_scenario_context(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "model_scoring" / "score.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "sourceType": "run",
                "run_id": "run-123",
                "artifact_path": "model.cbm",
                "task": "regression",
                "output_column": "pred",
            }
        ),
        encoding="utf-8",
    )
    scorer = MagicMock()
    scorer.score.return_value = pl.DataFrame({"pred": [0.5]}).lazy()
    token = _scenario_ctx.set("nightly_batch")

    try:
        with patch("haute._model_scorer.ModelScorer", return_value=scorer) as ctor:
            result = score_from_config(
                pl.DataFrame({"a": [1.0]}).lazy(),
                config=str(config_path),
                base_dir=str(tmp_path),
            )
    finally:
        _scenario_ctx.reset(token)

    assert result.collect().columns == ["pred"]
    assert ctor.call_args.kwargs["source"] == "nightly_batch"


def test_score_from_config_rejects_symlink_escape(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / "outside_cfg"
    outside_dir.mkdir(exist_ok=True)
    outside_cfg = outside_dir / "score.json"
    outside_cfg.write_text("{}", encoding="utf-8")

    config_dir = tmp_path / "config" / "model_scoring"
    config_dir.mkdir(parents=True)
    link = config_dir / "score.json"
    try:
        link.symlink_to(outside_cfg)
    except OSError:
        pytest.skip("symlink creation not supported in this environment")

    with pytest.raises(ValueError, match="outside project root"):
        score_from_config(
            pl.DataFrame({"a": [1.0]}).lazy(),
            config=os.path.join("config", "model_scoring", "score.json"),
            base_dir=str(tmp_path),
        )
