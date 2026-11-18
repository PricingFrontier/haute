"""Validating a modelling config before a job is created.

``_validate_config`` and ``_validate_glm_config_values`` refuse malformed configs, GLM term columns
are checked against the unprojected input schema on ``/train`` and ``/dispersion/estimate``,
``_training_failure_answer`` puts a fit's failure into words and ``_clamp_row_limit`` applies row
limits.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from haute.errors import HauteValidationError, RequestRefusedError
from haute.modelling._descriptors import DESCRIPTORS
from haute.routes._training_evaluation import _validate_glm_config_values
from haute.routes._training_lifecycle import TrainService
from haute.routes._training_preparation import _clamp_row_limit
from haute.routes._training_worker import _training_failure_answer
from tests import _modelling_route_helpers
from tests._failure_assertions import assert_unnamed
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


def _catboost_error(message: str) -> Exception:
    """An error of a type named as CatBoost's are."""
    return type("CatBoostError", (Exception,), {})(message)


def _said(exc: Exception, **options: str) -> str:
    return _training_failure_answer(exc, **options).message


class TestTrainingFailureAnswer:
    """A fit's failure as its job answers it: CatBoost by its type, never its text; anything
    else as every edge answers it (haute._failure_answers)."""

    def test_a_validation_refusal_is_its_own_words(self):
        answer = _training_failure_answer(HauteValidationError("Target column 'z' not found"))
        assert answer.message == "Target column 'z' not found"
        assert answer.terminal_reason == "contract_error"

    @pytest.mark.parametrize(
        "exc",
        [
            pytest.param(ValueError("could not parse '/private/tmp/leaky' as float"), id="value"),
            pytest.param(RuntimeError("catboost cache failed at /var/lib/token=abc"), id="text"),
            pytest.param(OSError(5, "failed opening /tmp/secret credential=xyz"), id="os-error"),
            pytest.param(FileNotFoundError("internal staged asset"), id="no-file"),
        ],
    )
    def test_anything_unnamed_is_an_incident_never_its_text(self, exc):
        """A dependency's text can hold paths and secrets; a failure Haute does not name is
        an incident, its text recorded under the id (an error mentioning catboost in its text
        only is not a CatBoost error)."""
        answer = _training_failure_answer(exc)
        assert answer.terminal_reason == "error"
        assert_unnamed(answer.message)
        for leaked in ("leaky", "/var/lib", "token", "/tmp/secret", "credential", "staged"):
            assert leaked not in answer.message

    def test_a_file_the_operating_system_refused_is_named(self, tmp_path):
        exc = PermissionError(13, "Permission denied", str(tmp_path / "model.cbm"))
        exc_path = tmp_path / "model.cbm"
        exc_path.write_text("x", encoding="utf-8")
        exc_path.chmod(0o444)
        try:
            said = _said(exc)
        finally:
            exc_path.chmod(0o644)
        assert "model.cbm" in said
        assert str(tmp_path) not in said

    @pytest.mark.parametrize(
        "message",
        ["NaN values in column 'x'", "Found inf in column 3"],
        ids=["nan", "inf"],
    )
    def test_catboost_non_finite_data_says_what_to_fix(self, message):
        said = _said(_catboost_error(message))
        assert "NaN or infinite values" in said
        assert ".fill_null()" in said
        assert "column" not in said

    def test_catboost_feature_mismatch_never_quotes_the_library(self):
        said = _said(_catboost_error("feature number mismatch: expected 5 but got 3"))
        assert "feature mismatch" in said.lower()
        assert "expected 5" not in said

    def test_any_other_catboost_error_names_its_type_and_fit_under_an_incident(self):
        with capture_logs() as logs:
            answer = _training_failure_answer(
                _catboost_error("internal pool error at /tmp/pool"),
                context="target 'sev' (objective 'RMSE')",
            )
        assert "CatBoostError" in answer.message
        assert "target 'sev' (objective 'RMSE')" in answer.message
        assert "internal pool error" not in answer.message
        assert answer.incident is not None and answer.incident in answer.message
        assert [log for log in logs if log.get("incident") == answer.incident]


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


# The Modelling node every gate graph here trains, by address.
_TRAIN_TARGET = {"occurrence": None, "node_id": "train"}


def _resolved(body):
    """*body* with its target resolved over its own graph, as the route resolves it over
    the flattened document (the graphs here have no submodels)."""
    from haute._node_addresses import resolved_request

    return resolved_request(body, body.graph)


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
            target=_TRAIN_TARGET,
        )
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(_resolved(body))

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
            target=_TRAIN_TARGET,
            param="var_power",
        )
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start_dispersion_estimate(_resolved(body))

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
        body = TrainRequest(graph=make_graph(graph), target=_TRAIN_TARGET)
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(_resolved(body))

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
            target=_TRAIN_TARGET,
        )
        store, service = self._service()

        with pytest.raises(RequestRefusedError) as raised:
            service.start(_resolved(body))

        assert raised.value.status_code == 422
        detail = str(raised.value.detail)
        assert "Training input schema could not be resolved" in detail
        assert "No input data available" in detail
        assert not store.list_jobs()

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param("df = source.with_columns(x2=undefined_name)", id="NameError"),
            pytest.param("", id="NotImplementedError"),
        ],
    )
    def test_user_transform_error_becomes_422_on_training_and_dispersion_routes(
        self, glm_collision_data, code
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
                _resolved(
                    TrainRequest(
                        graph=_glm_transform_gate_graph(
                            glm_collision_data, code, {**base, "family": "gaussian"}
                        ),
                        target=_TRAIN_TARGET,
                    )
                )
            )
        # The user's own transform failing is named with its node (SRV-01's node_failed).
        assert raised.value.status_code == 422
        assert raised.value.detail["error_code"] == "node_failed"
        assert raised.value.detail["message"].startswith("'prep' failed: ")

        with pytest.raises(RequestRefusedError) as raised:
            service.start_dispersion_estimate(
                _resolved(
                    DispersionEstimateRequest(
                        graph=_glm_transform_gate_graph(
                            glm_collision_data, code, {**base, "family": "tweedie"}
                        ),
                        target=_TRAIN_TARGET,
                        param="var_power",
                    )
                )
            )
        # The user's own transform failing is named with its node (SRV-01's node_failed).
        assert raised.value.status_code == 422
        assert raised.value.detail["error_code"] == "node_failed"
        assert raised.value.detail["message"].startswith("'prep' failed: ")
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
