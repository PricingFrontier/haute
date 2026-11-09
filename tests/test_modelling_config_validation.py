"""Validating a modelling config before a job is created.

``_validate_config`` and ``_validate_glm_config_values`` refuse malformed configs, GLM term columns
are checked against the unprojected input schema on ``/train`` and ``/dispersion/estimate``,
``_friendly_error`` translates exceptions into messages and ``_clamp_row_limit`` applies row
limits.
"""

from __future__ import annotations

import pytest

from haute.errors import HauteValidationError, RequestRefusedError
from haute.modelling._descriptors import DESCRIPTORS
from haute.routes._training_evaluation import _validate_glm_config_values
from haute.routes._training_lifecycle import TrainService
from haute.routes._training_preparation import _clamp_row_limit
from haute.routes._training_worker import _friendly_error
from tests import _modelling_route_helpers
from tests._modelling_route_helpers import (
    _MISSING_STEP_COUNT_PAYLOAD,
    _expander_without_step_count,
    _glm_schema_gate_graph,
    _random_evaluation_config,
)
from tests.conftest import (
    make_edge,
    make_graph,
    make_ready_file_input_config,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
_fast_optional_training_diagnostics = _modelling_route_helpers._fast_optional_training_diagnostics
glm_collision_data = _modelling_route_helpers.glm_collision_data


# ---------------------------------------------------------------------------
# Phase 1A: Pure function tests
# ---------------------------------------------------------------------------


class TestFriendlyError:
    """Unit tests for _friendly_error — translates exceptions into user messages."""

    def test_haute_validation_error_passthrough(self):
        exc = HauteValidationError("Target column 'z' not found")
        assert _friendly_error(exc) == "Target column 'z' not found"

    def test_plain_value_error_takes_the_type_only_fallback(self):
        """Provenance is enforced by the marker type: a dependency's bare
        ``ValueError`` body is never promoted verbatim."""
        exc = ValueError("could not parse '/private/tmp/leaky' as float")
        result = _friendly_error(exc)
        assert "leaky" not in result
        assert "ValueError" in result
        assert "unexpected internal error" in result

    def test_file_not_found_does_not_quote_the_path(self):
        """A fit-stage missing file is typically an internal staged asset —
        the path stays diagnostic, never in the terminal message."""
        exc = FileNotFoundError(2, "No such file or directory", "/data/missing.parquet")
        result = _friendly_error(exc)
        assert "could not find a file it needs" in result
        assert "missing.parquet" not in result
        assert "/data" not in result

    def test_catboost_nan(self):
        """CatBoost NaN/Inf errors should recommend upstream transforms."""
        # Simulate CatBoost error class
        exc = type("CatBoostError", (Exception,), {})("NaN values in features")
        result = _friendly_error(exc)
        assert "NaN" in result or "nan" in result.lower()
        assert "transform node" in result.lower()

    def test_catboost_feature_mismatch(self):
        exc = type("CatBoostError", (Exception,), {})("feature number mismatch: expected 10 got 8")
        result = _friendly_error(exc)
        assert "feature mismatch" in result.lower()
        # The third-party body is never interpolated.
        assert "expected 10" not in result

    def test_catboost_generic(self):
        """The generic CatBoost shape names the type, never the body."""
        exc = type("CatBoostError", (Exception,), {})("internal pool error at /tmp/pool")
        result = _friendly_error(exc)
        assert "CatBoostError" in result
        assert "internal pool error" not in result
        assert "/tmp" not in result

    def test_catboost_generic_names_training_context(self):
        exc = type("CatBoostError", (Exception,), {})("internal pool error")
        result = _friendly_error(exc, context="target 'sev' (objective 'RMSE')")
        assert "target 'sev' (objective 'RMSE')" in result
        assert "internal pool error" not in result

    def test_catboost_in_message_only_is_not_classified_catboost(self):
        """Classification keys on the exception TYPE — a non-CatBoost error
        that merely mentions catboost in its text takes the generic fallback
        and never embeds its body."""
        exc = RuntimeError("catboost cache failed at /var/lib/haute/token=abc")
        result = _friendly_error(exc)
        assert "RuntimeError" in result
        assert "CatBoost error" not in result
        assert "/var/lib" not in result
        assert "token" not in result

    def test_os_error(self):
        """The errno-derived OS reason is surfaced; the staging path is not."""
        exc = OSError(13, "Permission denied", "/models/model.cbm")
        result = _friendly_error(exc)
        assert result.startswith("Training could not save its output files")
        assert "Permission denied" in result
        assert "/models" not in result

    def test_os_error_strerror_is_rederived_from_errno(self):
        """OSError.strerror is constructor-supplied — a dependency can put
        arbitrary text there. The reason comes from os.strerror(errno)."""
        exc = OSError(5, "failed opening /tmp/secret credential=xyz")
        result = _friendly_error(exc)
        assert "/tmp/secret" not in result
        assert "credential" not in result
        assert "Input/output error" in result

    def test_os_error_without_errno_uses_generic_reason(self):
        exc = OSError("raw single-argument message")
        result = _friendly_error(exc)
        assert "file-system error" in result
        assert "raw single-argument message" not in result
        assert "OSError" not in result

    def test_fallback_includes_type_but_not_third_party_text(self):
        exc = RuntimeError("something unexpected")
        result = _friendly_error(exc)
        assert "RuntimeError" in result
        assert "something unexpected" not in result
        assert "error details" in result

    def test_fallback_names_training_context(self):
        exc = RuntimeError("boom")
        result = _friendly_error(exc, context="target 'freq' (objective 'Poisson')")
        assert result.startswith("Training of target 'freq' (objective 'Poisson') failed")

    def test_catboost_inf_message(self):
        """'inf' in message also triggers NaN/Inf advice."""
        exc = type("CatBoostError", (Exception,), {})("Found inf in column 3")
        result = _friendly_error(exc)
        assert "infinite" in result.lower() or "inf" in result.lower()
        assert "transform node" in result.lower()

    def test_empty_exception_message(self):
        exc = RuntimeError("")
        result = _friendly_error(exc)
        assert "RuntimeError" in result

    def test_catboost_nan_recommends_fill_null(self):
        exc = type("CatBoostError", (Exception,), {})("NaN values in column 'x'")
        result = _friendly_error(exc)
        assert ".fill_null()" in result or ".drop_nulls()" in result

    def test_catboost_feature_mismatch_excludes_original_message(self):
        """Flipped from the pre-curation pin: the CatBoost body is never
        interpolated, even in the feature-mismatch shape."""
        exc = type("CatBoostError", (Exception,), {})(
            "feature number mismatch: expected 5 but got 3"
        )
        result = _friendly_error(exc)
        assert "feature mismatch" in result.lower()
        assert "expected 5 but got 3" not in result


class TestClampRowLimit:
    """Unit tests for _clamp_row_limit — applies user row limits."""

    def test_none_user_limit_returns_current(self):
        assert _clamp_row_limit(1000, None) == 1000

    def test_zero_user_limit_returns_current(self):
        assert _clamp_row_limit(1000, 0) == 1000

    def test_negative_user_limit_returns_current(self):
        assert _clamp_row_limit(1000, -5) == 1000

    def test_user_smaller_than_current(self):
        assert _clamp_row_limit(1000, 500) == 500

    def test_user_larger_than_current(self):
        assert _clamp_row_limit(500, 1000) == 500

    def test_no_current_limit(self):
        assert _clamp_row_limit(None, 500) == 500

    def test_string_user_limit_ignored(self):
        assert _clamp_row_limit(1000, "abc") == 1000

    def test_float_user_limit_converted(self):
        assert _clamp_row_limit(1000, 500.7) == 500
        assert isinstance(_clamp_row_limit(1000, 500.7), int)

    def test_both_none(self):
        assert _clamp_row_limit(None, None) is None


# ---------------------------------------------------------------------------
# _validate_config unit tests
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_no_target_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config({"algorithm": "catboost"})
        assert exc_info.value.status_code == 400
        assert "No target column" in exc_info.value.detail

    def test_empty_target_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config({"target": "", "algorithm": "catboost"})
        assert exc_info.value.status_code == 400
        assert "No target column" in exc_info.value.detail

    def test_unknown_algorithm_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config({"target": "y", "algorithm": "unregistered"})
        assert exc_info.value.status_code == 400
        assert "unregistered" in exc_info.value.detail
        assert "Available algorithms" in exc_info.value.detail

    def test_glm_unknown_family_raises_with_suggestions(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "exponential",
                }
            )
        assert exc_info.value.status_code == 400
        assert "exponential" in exc_info.value.detail
        assert "gaussian" in exc_info.value.detail

    def test_glm_invalid_link_for_family_raises_with_valid_options(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                    "link": "logit",
                }
            )
        assert exc_info.value.status_code == 400
        assert "logit" in exc_info.value.detail
        assert "log" in exc_info.value.detail

    def test_valid_catboost_config_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "catboost",
                "loss_function": "RMSE",
                "feature_columns": ["x"],
                "params": {"iterations": 10},
                "evaluation": _random_evaluation_config(),
            }
        )

    @pytest.mark.parametrize(
        "cross_validation",
        [
            {"schema_version": 2, "strategy": "random", "fold_count": 3, "seed": 7},
            {"schema_version": 1, "strategy": "random", "fold_count": True, "seed": 7},
            {
                "schema_version": 1,
                "strategy": "group",
                "fold_count": 3,
                "seed": 7,
            },
        ],
    )
    def test_invalid_cross_validation_fails_before_job_creation(
        self, cross_validation: dict[str, object]
    ) -> None:
        with pytest.raises(RequestRefusedError) as raised:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "regression",
                    "loss_function": "RMSE",
                    "cross_validation": cross_validation,
                }
            )

        assert raised.value.status_code == 400
        assert "cross_validation" in str(raised.value.detail)

    def test_valid_glm_config_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "link": "log",
                "terms": {"age": {"type": "linear"}},
                "evaluation": _random_evaluation_config(),
            }
        )

    def test_glm_validation_uses_canonical_top_level_family_and_link(self):
        """Nested params are CatBoost config and cannot override GLM fields."""
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "poisson",
                "link": "log",
                "terms": {"age": {"type": "linear"}},
                "params": {"family": "binomial", "link": "identity"},
                "evaluation": _random_evaluation_config(),
            }
        )

    def test_catboost_missing_loss_raises_400(self):
        """Unset loss must not silently train under CatBoost's RMSE default."""
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "params": {"iterations": 10},
                }
            )
        assert exc_info.value.status_code == 400
        assert "loss function" in exc_info.value.detail.lower()

    @pytest.mark.parametrize("algorithm", ["catboost", "xgboost", "lightgbm", "ebm"])
    @pytest.mark.parametrize(
        "selection",
        [{}, {"feature_columns": []}, {"feature_columns": ["y"]}],
        ids=["absent", "empty", "target_only"],
    )
    def test_tree_config_without_a_selected_feature_raises_400(self, algorithm, selection):
        """Features are opt-in: a tree model with no ticked feature is refused, never
        trained on every column. A listed role column is dormant, so ticking only
        the target selects no feature either."""
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": algorithm,
                    "task": "regression",
                    "loss_function": "RMSE",
                    "params": {"max_rounds": 100} if algorithm == "ebm" else {},
                    "evaluation": _random_evaluation_config(),
                    **selection,
                }
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == (
            "Modelling config has no features. Tick at least one feature on the Features pane."
        )

    @pytest.mark.parametrize(
        ("algorithm", "task", "loss"),
        [
            (name, task, loss)
            for name, descriptor in sorted(DESCRIPTORS.items())
            if name != "glm"
            for task, losses in sorted(descriptor.losses.items())
            for loss in sorted(losses)
        ],
    )
    def test_each_family_accepts_every_loss_it_supports(self, algorithm, task, loss):
        """The loss is checked against the chosen family, not CatBoost's list: LightGBM,
        XGBoost and EBM train Gamma, which CatBoost lacks."""
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": algorithm,
                "task": task,
                "loss_function": loss,
                "feature_columns": ["x"],
                **({"variance_power": 1.5} if loss == "Tweedie" else {}),
                # EBM trains every round it is given and t-boost's ceiling is the
                # refit's fixed budget, so both need an explicit count.
                "params": {"max_rounds": 100}
                if algorithm == "ebm"
                else {"n_trees": 100}
                if algorithm == "tboost"
                else {},
                "evaluation": _random_evaluation_config(),
            }
        )

    def test_a_loss_the_family_lacks_is_refused_in_its_name(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "regression",
                    "loss_function": "Gamma",
                    "evaluation": _random_evaluation_config(),
                }
            )
        assert exc_info.value.status_code == 400
        assert "CatBoost does not support the Gamma loss" in exc_info.value.detail

    def test_catboost_loss_invalid_for_task_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "task": "classification",
                    "loss_function": "Poisson",
                }
            )
        assert exc_info.value.status_code == 400
        assert "Poisson" in exc_info.value.detail

    def test_glm_empty_family_raises_400(self):
        """Unset family must not silently train a gaussian GLM."""
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "",
                }
            )
        assert exc_info.value.status_code == 400
        assert "family" in exc_info.value.detail.lower()

    def test_glm_empty_terms_raises_400(self):
        """An empty term set must not silently auto-term over every column."""
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                }
            )
        assert exc_info.value.status_code == 400
        assert "add a term to at least one feature" in exc_info.value.detail.lower()

    def test_glm_tweedie_without_variance_power_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "tweedie",
                    "terms": {"age": {"type": "linear"}},
                }
            )
        assert exc_info.value.status_code == 400
        assert "variance power" in exc_info.value.detail.lower()

    def test_glm_elastic_net_without_l1_ratio_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "poisson",
                    "terms": {"age": {"type": "linear"}},
                    "regularization": "elastic_net",
                }
            )
        assert exc_info.value.status_code == 400
        assert "l1 ratio" in exc_info.value.detail.lower()

    def test_catboost_tweedie_without_variance_power_raises_400(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            TrainService._validate_config(
                {
                    "target": "y",
                    "algorithm": "catboost",
                    "loss_function": "Tweedie",
                }
            )
        assert exc_info.value.status_code == 400
        assert "variance power" in exc_info.value.detail.lower()

    def test_glm_empty_link_passes(self):
        TrainService._validate_config(
            {
                "target": "y",
                "algorithm": "glm",
                "family": "gaussian",
                "link": "",
                "terms": {"age": {"type": "linear"}},
                "evaluation": _random_evaluation_config(),
            }
        )


# ---------------------------------------------------------------------------
# Synchronous GLM schema gate on /train and /dispersion/estimate
# ---------------------------------------------------------------------------


_GLM_COLLIDING_TERMS: dict = {
    "x": {"type": "linear"},
    "x_sq": {"type": "expression", "expr": "x ** 2"},
}


def _glm_transform_gate_graph(data_path: str, code: str, config: dict):
    """Data Input → Polars → Modelling, with the user's own code in the middle."""
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(data_path),
                    },
                },
                {
                    "id": "prep",
                    "data": {"label": "prep", "nodeType": "polars", "config": {"code": code}},
                },
                {
                    "id": "train",
                    "data": {"label": "train", "nodeType": "modelling", "config": config},
                },
            ],
            "edges": [
                make_edge("source", "prep").model_dump(),
                make_edge("prep", "train").model_dump(),
            ],
        }
    )


class TestGlmInputSchemaGate:
    """GLM term columns are checked against the exact unprojected input schema
    before a job is created, so a mismatch is a 422 on the request rather than
    a job that fails later during background preparation."""

    def _service(self):
        from haute.routes._job_store import JobStore

        store = JobStore()
        return store, TrainService(store)

    def test_training_route_rejects_expression_key_colliding_with_unprojected_column(
        self, glm_collision_data
    ):
        """Upstream has an unused column ``x_sq``; the model keys an expression
        ``x_sq``. Projection would drop the column, so the gate must see the
        unprojected schema."""
        from haute.schemas import TrainRequest

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    "algorithm": "glm",
                    "target": "y",
                    "family": "gaussian",
                    "terms": _GLM_COLLIDING_TERMS,
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(body)

        assert raised.value.status_code == 422
        assert "names a column" in str(raised.value.detail)
        assert not store.list_jobs()

    def test_dispersion_route_rejects_expression_key_colliding_with_unprojected_column(
        self, glm_collision_data
    ):
        """The dispersion route materialises the same frame, so it gates the
        same way — a 422 before the estimation job exists."""
        from haute.schemas import DispersionEstimateRequest

        body = DispersionEstimateRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    "algorithm": "glm",
                    "target": "y",
                    "family": "tweedie",
                    "terms": _GLM_COLLIDING_TERMS,
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
            param="var_power",
        )
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start_dispersion_estimate(body)

        assert raised.value.status_code == 422
        assert "names a column" in str(raised.value.detail)
        assert not store.list_jobs()

    def test_training_route_reports_a_rejected_upstream_node_config_as_its_contract_payload(
        self, glm_collision_data
    ):
        """The GLM gate resolves the input schema by running the upstream graph; a
        node config the builder rejects keeps its public payload."""
        from haute.schemas import TrainRequest

        graph = _glm_schema_gate_graph(
            glm_collision_data,
            {
                "algorithm": "glm",
                "target": "y",
                "family": "gaussian",
                "terms": {"x": {"type": "linear"}},
                "evaluation": _random_evaluation_config(),
            },
        ).model_dump()
        graph["nodes"].append(_expander_without_step_count())
        graph["edges"] = [
            make_edge("source", "expander").model_dump(),
            make_edge("expander", "train").model_dump(),
        ]
        body = TrainRequest(graph=make_graph(graph), node_id="train")
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(body)

        assert raised.value.status_code == 422
        assert raised.value.detail == _MISSING_STEP_COUNT_PAYLOAD
        assert not store.list_jobs()

    def test_training_route_returns_422_when_input_schema_cannot_be_resolved(self):
        """An unfed modelling node has no schema to check against — an explicit
        422 naming the cause, never a 500 and never the projected schema."""
        from haute.schemas import TrainRequest

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                None,
                {
                    "algorithm": "glm",
                    "target": "y",
                    "family": "gaussian",
                    "terms": {"x": {"type": "linear"}},
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(body)

        assert raised.value.status_code == 422
        detail = str(raised.value.detail)
        assert "Training input schema could not be resolved" in detail
        assert "No input data available" in detail
        assert not store.list_jobs()

    @pytest.mark.parametrize(
        ("code", "error_name"),
        [
            ("df = source.with_columns(x2=undefined_name)", "NameError"),
            ("", "NotImplementedError"),
        ],
    )
    def test_user_transform_error_becomes_422_on_training_and_dispersion_routes(
        self, glm_collision_data, code, error_name
    ):
        """The schema-only build runs the user's own transform code, so any
        exception that code raises is the user's to fix — a named 422, never a
        500 that reads as a Haute crash."""
        from haute.schemas import DispersionEstimateRequest, TrainRequest

        base = {
            "algorithm": "glm",
            "target": "y",
            "terms": {"x": {"type": "linear"}},
            "evaluation": _random_evaluation_config(),
        }
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(
                TrainRequest(
                    graph=_glm_transform_gate_graph(
                        glm_collision_data, code, {**base, "family": "gaussian"}
                    ),
                    node_id="train",
                )
            )
        assert raised.value.status_code == 422
        assert "could not be resolved" in str(raised.value.detail)
        assert error_name in str(raised.value.detail)

        with pytest.raises(RequestRefusedError) as raised:
            service.start_dispersion_estimate(
                DispersionEstimateRequest(
                    graph=_glm_transform_gate_graph(
                        glm_collision_data, code, {**base, "family": "tweedie"}
                    ),
                    node_id="train",
                    param="var_power",
                )
            )
        assert raised.value.status_code == 422
        assert "could not be resolved" in str(raised.value.detail)
        assert error_name in str(raised.value.detail)
        assert not store.list_jobs()


# ---------------------------------------------------------------------------
# _validate_glm_config_values unit tests
# ---------------------------------------------------------------------------


def _glm_values(**overrides: object) -> dict[str, object]:
    return {
        "algorithm": "glm",
        "target": "y",
        "family": "poisson",
        "terms": {"x": {"type": "linear"}},
        **overrides,
    }


class TestValidateGlmConfigValues:
    def test_unknown_family_names_the_supported_families(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            _validate_glm_config_values(_glm_values(family="exponential"))
        assert exc_info.value.status_code == 400
        assert "exponential" in exc_info.value.detail
        assert "gaussian" in exc_info.value.detail

    def test_invalid_link_names_the_family_links(self):
        with pytest.raises(RequestRefusedError) as exc_info:
            _validate_glm_config_values(_glm_values(family="gamma", link="logit"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == (
            "Link 'logit' is not valid for the gamma family. Valid links: log, identity."
        )

    @pytest.mark.parametrize(
        ("family", "link"),
        [
            ("gaussian", "inverse"),
            ("binomial", "probit"),
            ("binomial", "cloglog"),
            ("poisson", "sqrt"),
            ("gamma", "inverse"),
            ("inverse_gaussian", ""),
        ],
    )
    def test_unsupported_links_and_inverse_gaussian_are_refused(self, family, link):
        with pytest.raises(RequestRefusedError) as exc_info:
            _validate_glm_config_values(_glm_values(family=family, link=link))
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize(
        ("family", "link"),
        [
            ("quasipoisson", ""),
            ("quasipoisson", "identity"),
            ("negbinomial", "log"),
            ("quasibinomial", "logit"),
            ("binomial", "log"),
            ("gaussian", "log"),
        ],
    )
    def test_supported_family_links_pass(self, family, link):
        _validate_glm_config_values(_glm_values(family=family, link=link))

    def test_absent_family_is_left_to_the_objective_gate(self):
        _validate_glm_config_values({"algorithm": "glm", "terms": {"x": {"type": "linear"}}})
