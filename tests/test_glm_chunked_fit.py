"""MOD-M01: a GLM fitted out of core is the in-memory fit, bit for bit.

Each job runs through ``TrainingJob`` twice on the same rows, once per fit mode,
with a source batch small enough that every pass reads several batches. Every
reported quantity must be identical; the agreement tolerance is zero.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

rs = pytest.importorskip("rustystats", reason="rustystats not installed")

from haute._execution_context import (  # noqa: E402
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionProfile,
)
from haute.errors import HauteValidationError  # noqa: E402
from haute.modelling._rustystats import GLMMemoryPlanError  # noqa: E402
from haute.modelling._training_job import TrainingJob  # noqa: E402

_EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}
_ROWS = 9_000
_TERMS: dict[str, dict[str, Any]] = {
    "x": {"type": "linear"},
    "age": {"type": "bs", "df": 4},
    "region": {"type": "categorical"},
}
_INTERACTIONS = [{"factors": ["region", "x"], "include_main": False}]
#: Reported quantities that must agree exactly between the two modes.
_COMPARED = (
    "glm_coefficients",
    "glm_relativities",
    "glm_fit_statistics",
    "glm_inference",
    "glm_smooth_terms",
    "glm_regularization",
    "metrics",
    "holdout_metrics",
    "actual_vs_predicted",
    "residuals_stats",
    "lorenz_curve",
)


@pytest.fixture(autouse=True)
def _small_source_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several source batches per pass, none aligned with RustyStats' 4,096-row blocks."""
    monkeypatch.setattr("haute.modelling._rustystats.GLM_SOURCE_BATCH_ROWS", 1_500)


def _frame(family: str, seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=_ROWS)
    age = rng.uniform(18.0, 80.0, size=_ROWS)
    region = rng.choice(["north", "south", "east", "west", "central"], size=_ROWS)
    exposure = rng.uniform(0.2, 1.0, size=_ROWS)
    weight = rng.uniform(0.5, 2.0, size=_ROWS)
    effect = {"north": 0.1, "south": -0.2, "east": 0.05, "west": 0.0, "central": 0.2}
    eta = 0.3 * x + 0.01 * (age - 45.0) + np.array([effect[r] for r in region])
    if family == "gaussian":
        y = 2.0 + eta + rng.normal(size=_ROWS)
    elif family in ("poisson", "negbinomial"):
        y = rng.poisson(exposure * np.exp(-1.0 + eta)).astype(float)
    elif family == "gamma":
        y = rng.gamma(2.0, np.exp(1.0 + eta) / 2.0)
    elif family == "tweedie":
        counts = rng.poisson(np.exp(-0.5 + eta))
        y = np.array([rng.gamma(2.0, 1.0, size=n).sum() if n else 0.0 for n in counts])
    elif family == "binomial":
        y = (rng.uniform(size=_ROWS) < 1.0 / (1.0 + np.exp(-eta))).astype(np.int64)
    else:
        raise AssertionError(family)
    return pl.DataFrame(
        {"x": x, "age": age, "region": region, "exposure": exposure, "w": weight, "y": y}
    )


def _run(
    tmp_path: Path,
    frame: pl.DataFrame,
    params: dict[str, Any],
    *,
    fit_mode: str,
    task: str = "regression",
    offset: str | None = None,
    weight: str | None = "w",
    **run_kwargs: Any,
) -> Any:
    name = f"glm_{fit_mode}"
    return TrainingJob(
        name=name,
        data=frame,
        target="y",
        weight=weight,
        offset=offset,
        algorithm="glm",
        task=task,
        params=params,
        evaluation=_EVALUATION,
        output_dir=str(tmp_path / name),
        fit_mode=fit_mode,  # type: ignore[arg-type]
        scratch_dir=str(tmp_path / "scratch"),
    ).run(**run_kwargs)


def _assert_identical(tmp_path: Path, frame: pl.DataFrame, params: dict[str, Any], **kw: Any):
    chunked = _run(tmp_path, frame, params, fit_mode="out_of_core", **kw)
    memory = _run(tmp_path, frame, params, fit_mode="in_memory", **kw)
    for field in _COMPARED:
        assert getattr(chunked, field) == getattr(memory, field), field
    assert chunked.diagnostics_errors == memory.diagnostics_errors == []
    from haute._mlflow_io import load_local_model

    np.testing.assert_array_equal(
        load_local_model(chunked.model_path).predict(frame),
        load_local_model(memory.model_path).predict(frame),
    )
    assert chunked.fit_evidence["mode"] == "out_of_core"
    assert memory.fit_evidence["mode"] == "in_memory"
    for cap in ("max_iter", "tol", "max_half_steps"):
        assert chunked.fit_evidence[cap] == memory.fit_evidence[cap]
    assert chunked.fit_evidence["passes"] >= 2
    assert "passes" not in memory.fit_evidence
    assert not list((tmp_path / "scratch").iterdir())
    return chunked, memory


@pytest.mark.parametrize(
    ("family", "extra", "task", "offset"),
    [
        ("gaussian", {}, "regression", None),
        ("poisson", {}, "regression", "exposure"),
        ("gamma", {}, "regression", None),
        ("tweedie", {"var_power": 1.5}, "regression", None),
        ("binomial", {}, "classification", None),
        ("negbinomial", {"theta": 2.0}, "regression", "exposure"),
    ],
)
def test_every_family_fits_the_same_model_out_of_core(
    tmp_path: Path, family: str, extra: dict[str, Any], task: str, offset: str | None
) -> None:
    params = {"family": family, "terms": _TERMS, "interactions": _INTERACTIONS, **extra}

    _assert_identical(tmp_path, _frame(family), params, task=task, offset=offset)


def test_a_cross_validated_ridge_penalty_is_chosen_identically(tmp_path: Path) -> None:
    params = {
        "family": "poisson",
        "terms": _TERMS,
        "regularization": "ridge",
        "cv_folds": 3,
        "cv_selection": "min",
        "cv_seed": 7,
    }

    chunked, _ = _assert_identical(tmp_path, _frame("poisson"), params, offset="exposure")
    assert chunked.glm_regularization["mode"] == "cross_validation"


def test_robust_standard_errors_are_identical(tmp_path: Path) -> None:
    params = {"family": "gamma", "terms": _TERMS, "robust_standard_errors": "HC1"}

    chunked, _ = _assert_identical(tmp_path, _frame("gamma"), params)
    assert chunked.glm_inference["standard_errors"] == "HC1"


def test_an_ill_conditioned_design_is_identical(tmp_path: Path) -> None:
    frame = _frame("gaussian").with_columns(
        x_twin=pl.col("x") * 1e6 + pl.col("age") * 1e-9,
    )
    params = {
        "family": "gaussian",
        "terms": {"x": {"type": "linear"}, "x_twin": {"type": "linear"}, "age": {"type": "linear"}},
    }

    _assert_identical(tmp_path, frame, params)


def test_a_fit_that_rejects_a_step_halving_candidate_is_identical(tmp_path: Path) -> None:
    # RustyStats' golden overshoot: a heavy-tailed Gamma whose full Newton steps overshoot.
    rng = np.random.default_rng(0)
    x = rng.normal(0.0, 1.0, _ROWS)
    y = rng.gamma(0.3, np.exp(0.3 + 3.0 * x) / 0.3)
    frame = pl.DataFrame({"x": x, "w": np.ones(_ROWS), "y": y})
    params = {"family": "gamma", "terms": {"x": {"type": "linear"}}, "max_iter": 100}
    probe = rs.glm_dict(response="y", terms=params["terms"], data=frame, family="gamma").fit(
        max_iter=100
    )
    assert probe.step_halving_used, "the fixture must make the solver reject a full step"

    chunked, memory = _assert_identical(tmp_path, frame, params)
    assert chunked.glm_fit_statistics["iterations"] == memory.glm_fit_statistics["iterations"]


def test_a_monotone_penalised_spline_without_a_cap_records_the_native_default(
    tmp_path: Path,
) -> None:
    params = {
        "family": "poisson",
        "terms": {"age": {"type": "bs", "k": 8, "monotonicity": "increasing"}},
    }

    chunked, memory = _assert_identical(tmp_path, _frame("poisson"), params, offset="exposure")
    assert chunked.fit_evidence["max_iter"] == memory.fit_evidence["max_iter"] == 2_000


def _context(memory_limit_bytes: int) -> ExecutionContext:
    """A context whose remaining headroom is exactly its limit (constant RSS)."""
    return ExecutionContext(
        operation="training_job",
        profile=ExecutionProfile.TRAINING_PREP,
        memory_limit_bytes=memory_limit_bytes,
        memory_baseline_bytes=10**9,
        memory_sampler=lambda: 10**9,
    )


def test_options_take_half_the_headroom_and_record_the_plan(tmp_path: Path) -> None:
    params = {"family": "gaussian", "terms": _TERMS}

    result = _run(
        tmp_path,
        _frame("gaussian"),
        params,
        fit_mode="out_of_core",
        execution_context=_context(400 * 1024**2),
    )

    assert result.fit_evidence["max_bytes"] == 200 * 1024**2
    assert result.fit_evidence["encode_rows"] >= 4_096


def test_a_budget_below_the_fixed_state_is_a_curated_memory_failure(tmp_path: Path) -> None:
    params = {"family": "gaussian", "terms": _TERMS}

    with pytest.raises(GLMMemoryPlanError) as raised:
        _run(
            tmp_path,
            _frame("gaussian"),
            params,
            fit_mode="out_of_core",
            execution_context=_context(1024**2),
        )

    assert str(raised.value).startswith("Training could not fit the GLM within its memory budget:")
    assert "max_bytes" in raised.value.detail
    assert not list((tmp_path / "scratch").iterdir())


@pytest.mark.parametrize("labelled", [False, True], ids=["freeze-pass", "labels-pass"])
def test_learned_state_beyond_the_budget_is_a_curated_memory_failure(
    tmp_path: Path, labelled: bool
) -> None:
    rng = np.random.default_rng(4)
    rows = 300_000
    frame = pl.DataFrame(
        {
            "policy": [f"policy-{index:026d}" for index in range(rows)],
            "x": rng.normal(size=rows),
            "w": np.ones(rows),
            "y": rng.normal(size=rows),
        }
    )
    spec: dict[str, Any] = {"type": "categorical"}
    if labelled:
        spec["reference"] = "policy-" + "0" * 26
    params = {"family": "gaussian", "terms": {"x": {"type": "linear"}, "policy": spec}}

    # max_bytes 100 MiB leaves about 10 MiB of learned state beside the freeze pass.
    with pytest.raises(GLMMemoryPlanError) as raised:
        _run(
            tmp_path,
            frame,
            params,
            fit_mode="out_of_core",
            execution_context=_context(200 * 1024**2),
        )

    assert isinstance(raised.value.__cause__, rs.exceptions.LearnedStateTooLargeError)
    assert not list((tmp_path / "scratch").iterdir())


def test_cancelling_mid_pass_stops_at_the_next_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation is checked at every source batch, not only when a pass starts.

    A pass reads 5 batches of 1,500 rows (7,200 training rows). Cancelling once the
    7th batch, mid-way through the 2nd pass, has been read stops there: a check made
    only per pass would read the rest of that pass (10 batches) first.
    """
    import haute.modelling._rustystats as adapter

    served: list[int] = []
    read_batches = adapter.bounded_collect_batches

    def counting(*args: Any, **kwargs: Any) -> Any:
        for batch in read_batches(*args, **kwargs):
            served.append(batch.height)
            yield batch

    monkeypatch.setattr(adapter, "bounded_collect_batches", counting)

    def check_cancelled() -> None:
        if len(served) >= 7:
            raise ExecutionCancelledError("training_job")

    with pytest.raises(ExecutionCancelledError):
        _run(
            tmp_path,
            _frame("gaussian"),
            {"family": "gaussian", "terms": _TERMS},
            fit_mode="out_of_core",
            check_cancelled=check_cancelled,
        )

    assert served[:5] == [1_500, 1_500, 1_500, 1_500, 1_200]
    assert len(served) == 7
    assert not list((tmp_path / "scratch").iterdir())


def test_target_encoding_is_identical_and_reproducible(tmp_path: Path) -> None:
    """The job's fit seed keys target encoding's order, in both modes and on reruns."""
    rng = np.random.default_rng(31)
    frame = _frame("poisson").with_columns(
        postcode=pl.Series([f"pc{code:04d}" for code in rng.integers(0, 400, size=_ROWS)])
    )
    params = {
        "family": "poisson",
        "terms": {"x": {"type": "linear"}, "postcode": {"type": "target_encoding"}},
    }

    chunked, memory = _assert_identical(tmp_path, frame, params, offset="exposure")
    rerun = _run(tmp_path / "rerun", frame, params, fit_mode="in_memory", offset="exposure")
    assert rerun.glm_coefficients == memory.glm_coefficients


def test_only_a_glm_fits_out_of_core(tmp_path: Path) -> None:
    with pytest.raises(HauteValidationError, match="trains in memory only"):
        TrainingJob(
            name="trees",
            data=_frame("gaussian"),
            target="y",
            algorithm="catboost",
            feature_columns=["x"],
            loss_function="RMSE",
            evaluation=_EVALUATION,
            output_dir=str(tmp_path),
            fit_mode="out_of_core",
        )
