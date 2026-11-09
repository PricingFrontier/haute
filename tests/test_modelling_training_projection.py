"""What a training job reads: projection and the training sink.

Training preparation projects the columns a model needs and closes its seed plan when execution
raises, and a model's training sink keeps exactly its selected features and role columns.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute.projection import ProjectionRequest, plan
from haute.routes._training_lifecycle import TrainService
from haute.routes._training_preparation import _training_required_columns_by_node
from tests import _modelling_route_helpers
from tests._modelling_route_helpers import (
    _glm_schema_gate_graph,
    _random_evaluation_config,
)
from tests._training_helpers import join_training_preparation
from tests.conftest import (
    make_file_input_config,
    make_graph,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
_fast_optional_training_diagnostics = _modelling_route_helpers._fast_optional_training_diagnostics
glm_collision_data = _modelling_route_helpers.glm_collision_data


# ---------------------------------------------------------------------------
# TrainService._execute_and_sink checkpoint cleanup
# ---------------------------------------------------------------------------


@contextmanager
def _training_prep_context():
    """Yield an admitted TRAINING_PREP context for direct child-core calls."""
    from haute._execution_admission import create_admitted_execution_context
    from haute._execution_context import ExecutionProfile

    context = create_admitted_execution_context(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    try:
        yield context
    finally:
        context.release_admission(preserve_primary_error=True)


class TestTrainingProjection:
    def test_glm_training_columns_include_terms_aux_and_split_column(self):
        seeds = _training_required_columns_by_node(
            "train",
            {
                "algorithm": "glm",
                "target": "claim_count",
                "weight": "exposure",
                "offset": "log_exposure",
                "terms": {
                    "driver_age": {"type": "linear"},
                    "territory": {"type": "categorical"},
                },
                "evaluation": {
                    "schema_version": 1,
                    "strategy": "group",
                    "group_column": "policy_id",
                    "seed": 42,
                    "validation": {"method": "single", "size": 0.2},
                },
            },
        )

        assert seeds == {
            "train": frozenset(
                {
                    "claim_count",
                    "driver_age",
                    "exposure",
                    "log_exposure",
                    "policy_id",
                    "territory",
                }
            )
        }

    def test_catboost_training_columns_are_exactly_selected_features_and_roles(self):
        """Features are opt-in, so the demand is exact before the schema is known:
        the selected features plus the role columns, never an all-except demand.
        A role column listed as a feature is dormant and demanded once, as a role;
        a column nobody selected (``policy_id``) is not demanded at all."""
        demand = _training_required_columns_by_node(
            "train",
            {
                "algorithm": "catboost",
                "target": "claim_count",
                "weight": "exposure",
                "feature_columns": ["driver_age", "territory", "claim_count"],
            },
        )

        assert demand == {
            "train": frozenset({"driver_age", "territory", "claim_count", "exposure"})
        }
        assert type(demand["train"]) is frozenset

    def test_catboost_feature_selection_projects_before_api_input_loading(self):
        """An unselected API column is projected away at the input, from the exact
        selected-features-plus-roles seed."""
        config = {
            "algorithm": "catboost",
            "target": "target",
            "weight": "weight",
            "offset": "offset",
            "fold_column": "fold",
            "id_columns": ["id"],
            "evaluation": {
                "schema_version": 1,
                "strategy": "group",
                "group_column": "group",
                "seed": 42,
                "validation": {"method": "single", "size": 0.2},
            },
            "feature_columns": ["feature_a", "feature_b"],
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "api",
                        "data": {
                            "label": "api",
                            "nodeType": "apiInput",
                            "config": {
                                "tables": [
                                    {
                                        "label": "rows",
                                        "emit": True,
                                        "columns": [
                                            {"name": column, "selected": True}
                                            for column in (
                                                "feature_a",
                                                "feature_b",
                                                "unselected_feature",
                                                "target",
                                                "weight",
                                                "offset",
                                                "fold",
                                                "id",
                                                "group",
                                            )
                                        ],
                                    }
                                ]
                            },
                        },
                    },
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": config,
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "api-train",
                        "source": "api",
                        "target": "train",
                        "sourceHandle": "rows",
                    }
                ],
            }
        )
        required = _training_required_columns_by_node("train", config)
        projection = plan(
            ProjectionRequest(
                graph=graph,
                target_node_id="train",
                profile=ExecutionProfile.TRAINING_PREP,
                required_columns_by_node=required,
            )
        )
        expected = frozenset(
            {
                "feature_a",
                "feature_b",
                "target",
                "weight",
                "offset",
                "fold",
                "id",
                "group",
            }
        )

        assert required == {"train": expected}
        assert projection.needed_by_node["train"] == expected
        assert projection.demand_for_edge(graph.edges[0]) == expected
        assert projection.diagnostics.node_reasons["train"].rule == "projection_seed"

    def test_prepare_training_data_forwards_training_projection(self, tmp_path):
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        config = {"target": "claim_count", "feature_columns": ["driver_age"]}
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": dict(config),
                        },
                    }
                ],
                "edges": [],
            }
        )
        captured: dict[str, object] = {}

        def fake_execute_lazy(*args, **kwargs):
            captured.update(kwargs)
            return (
                {"train": pl.DataFrame({"claim_count": [1.0], "driver_age": [40]}).lazy()},
                ["train"],
                {},
                {},
            )

        seeds = {"train": frozenset({"claim_count", "driver_age"})}
        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config=config,
            required_columns_by_node=seeds,
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.executor._preview_cache", MagicMock()),
            patch("haute.trace._cache", MagicMock()),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.failure is None
        assert captured["required_columns_by_node"] == seeds
        # The run executes under a seed plan planned for the same demand.
        plan = captured["snapshot_plan"]
        assert plan is not None
        assert captured["prepare_inputs"] is False
        assert plan.decision.target_node_id == "train"
        assert seeds["train"] <= set(plan.decision.planning_required_columns["train"])
        assert outcome.parquet_path == parquet_path
        assert Path(parquet_path).exists()
        assert outcome.feature_selection is not None
        assert outcome.execution_metrics is not None
        assert pl.read_parquet(parquet_path)["claim_count"].to_list() == [1.0]

    @pytest.mark.parametrize(
        ("project_to_keep_columns", "written"),
        [
            (True, ["claim_count", "driver_age"]),
            (False, ["claim_count", "driver_age", "unselected"]),
        ],
        ids=["projects_to_keep_columns", "no_projection_drops_nothing"],
    )
    def test_prepare_training_data_sink_keeps_only_keep_columns_when_projecting(
        self, tmp_path, project_to_keep_columns, written
    ) -> None:
        """A tree model's sink drops every column outside ``keep_columns`` (its
        selected features and role columns); without projection nothing is dropped."""
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        config = {"target": "claim_count", "feature_columns": ["driver_age"]}
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": dict(config),
                        },
                    }
                ],
                "edges": [],
            }
        )

        def fake_execute_lazy(*_args, **_kwargs):
            frame = pl.DataFrame(
                {"claim_count": [1.0, 2.0], "driver_age": [40, 41], "unselected": ["a", "b"]}
            ).lazy()
            return ({"train": frame}, ["train"], {}, {})

        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config=config,
            project_to_keep_columns=project_to_keep_columns,
            keep_columns=["claim_count", "driver_age"],
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.executor._preview_cache", MagicMock()),
            patch("haute.trace._cache", MagicMock()),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.failure is None
        assert pl.read_parquet(parquet_path).columns == written

    def test_prepare_training_data_maps_bounded_sink_failure_to_contract_failure(
        self,
        tmp_path,
    ) -> None:
        from haute.errors import BoundedMemoryUnsupportedError
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        config = {"target": "claim_count", "feature_columns": ["driver_age"]}
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": dict(config),
                        },
                    }
                ],
                "edges": [],
            }
        )

        def fake_execute_lazy(*_args, **_kwargs):
            # Filtered so the frame is not sliceable: training's writer slices a
            # frame it can slice and would never reach the native sink this test
            # is about.
            frame = (
                pl.DataFrame({"claim_count": [1.0], "driver_age": [40]})
                .lazy()
                .filter(pl.col("claim_count") > 0)
            )
            return ({"train": frame}, ["train"], {}, {})

        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config=config,
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch(
                "haute._chunked_writes.bounded_sink",
                side_effect=BoundedMemoryUnsupportedError("Bounded streaming sink failed"),
            ),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.parquet_path is None
        failure = outcome.failure
        assert failure is not None
        assert failure.terminal_reason == "contract_error"
        assert failure.http_status_code == 422
        assert "bounded streaming mode" in failure.message
        assert not Path(parquet_path).exists()

    def test_prepare_training_data_gate_failure_removes_parquet(self, tmp_path) -> None:
        """A target/task mismatch fails as a contract failure with no artifact."""
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        config = {
            "target": "claim_count",
            "task": "classification",
            "metrics": ["auc"],
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": config,
                        },
                    }
                ],
                "edges": [],
            }
        )

        def fake_execute_lazy(*_args, **_kwargs):
            return (
                {
                    "train": pl.DataFrame(
                        {"claim_count": [0.5, 1.25, 2.75], "driver_age": [40, 41, 42]}
                    ).lazy()
                },
                ["train"],
                {},
                {},
            )

        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config=config,
        )
        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        failure = outcome.failure
        assert failure is not None
        assert failure.terminal_reason == "contract_error"
        assert failure.http_status_code == 422
        assert not Path(parquet_path).exists()

    def test_prepare_training_data_maps_memory_failure_to_memory_outcome(self, tmp_path) -> None:
        from haute._execution_context import ExecutionMemoryLimitExceededError
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "train",
                        "data": {
                            "label": "train",
                            "nodeType": "modelling",
                            "config": {"target": "claim_count"},
                        },
                    }
                ],
                "edges": [],
            }
        )
        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="train",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config={"target": "claim_count"},
        )

        def raise_memory(*_args, **_kwargs):
            raise ExecutionMemoryLimitExceededError(
                "training_pipeline",
                rss_bytes=2048,
                limit_bytes=1024,
            )

        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=raise_memory,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        failure = outcome.failure
        assert failure is not None
        assert failure.terminal_reason == "memory_limited"
        assert failure.http_status_code == 507
        assert failure.fields["error_code"] == "memory_limit"
        assert not Path(parquet_path).exists()


class TestExecuteAndSinkPlanCleanup:
    """Verify the seed plan is closed even when the lazy execution raises."""

    def test_seed_plan_closed_on_error(self, tmp_path):
        """If the lazy execution raises, the plan's leases and staging are released."""
        from haute.routes._training_preparation import (
            TrainingPreparationRequest,
            prepare_training_data,
        )

        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "n",
                        "data": {
                            "label": "n",
                            "nodeType": "dataInput",
                            "config": make_file_input_config("x.parquet"),
                        },
                    }
                ],
                "edges": [],
            }
        )
        parquet_path = str(tmp_path / "prepared.parquet")
        request = TrainingPreparationRequest(
            graph=graph,
            node_id="n",
            job_id="job",
            source="live",
            parquet_path=parquet_path,
            config={"target": "claim_count"},
        )

        plans: list[Any] = []

        def failing_execute_lazy(*args, **kwargs):
            plans.append(kwargs["snapshot_plan"])
            raise RuntimeError("boom")

        with (
            patch(
                "haute.routes._training_preparation.execute_lazy_graph",
                side_effect=failing_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch("haute.executor._preview_cache", MagicMock()),
        ):
            with _training_prep_context() as context:
                outcome = prepare_training_data(request, execution_context=context)

        assert outcome.failure is not None
        assert outcome.failure.terminal_reason == "error"
        assert outcome.failure.http_status_code == 500
        # The plan was opened for the run and closed when it failed.
        assert len(plans) == 1
        assert plans[0]._closed


# ---------------------------------------------------------------------------
# Training sink projection
# ---------------------------------------------------------------------------


def _inline_sink_service(monkeypatch: pytest.MonkeyPatch):
    """Inline-protocol service plus the supervisor threads it launches."""
    from haute.routes._job_store import JobStore
    from tests.test_training_worker_protocol import _inline_protocol_runner

    store = JobStore()
    service = TrainService(store, protocol_runner=_inline_protocol_runner)
    launched: list = []
    launch_protocol = service._supervisor.launch_protocol

    def capture_launch(*args, **kwargs):
        thread = launch_protocol(*args, **kwargs)
        launched.append(thread)
        return thread

    monkeypatch.setattr(service._supervisor, "launch_protocol", capture_launch)
    return service, launched


def _spy_on_sink_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[bool | None, list[str] | None, list[str]]]:
    """Record each ``_execute_and_sink`` call's ``project_to_keep_columns`` and
    ``keep_columns`` and the columns the real sink actually wrote.

    The real sink runs; its parquet is read here because the caller deletes it
    (or hands ownership to the worker) as soon as preparation returns. Asserting
    on the arguments alone would pass even if preparation never produced a frame.
    """
    captured: list[tuple[bool | None, list[str] | None, list[str]]] = []
    original = TrainService._execute_and_sink

    def spy(self, body, preamble_ns, row_limit, job_id, **kwargs):
        prepared = original(self, body, preamble_ns, row_limit, job_id, **kwargs)
        captured.append(
            (
                kwargs.get("project_to_keep_columns"),
                kwargs.get("keep_columns"),
                pl.read_parquet(prepared).columns,
            )
        )
        return prepared

    monkeypatch.setattr(TrainService, "_execute_and_sink", spy)
    return captured


_GLM_SINK_CONFIG: dict = {
    "algorithm": "glm",
    "target": "y",
    "terms": {"x": {"type": "linear"}},
}


class TestTrainingSinkProjection:
    """A tree model's training sink keeps exactly its selected features and role
    columns. A GLM's column membership comes from its terms and interactions, so
    its training and dispersion sinks never project a column away."""

    def test_catboost_training_sink_keeps_only_selected_features_and_roles(
        self, glm_collision_data, tmp_path, monkeypatch
    ):
        captured = _spy_on_sink_projection(monkeypatch)
        from haute.schemas import TrainRequest
        from tests.test_training_worker_protocol import _SuccessfulTrainingJob

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    "algorithm": "catboost",
                    "task": "regression",
                    "target": "y",
                    "loss_function": "RMSE",
                    "feature_columns": ["x"],
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        service, launched = _inline_sink_service(monkeypatch)

        with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
            response = service.start(body)
            join_training_preparation(response.job_id)
            for thread in launched:
                thread.join(timeout=10)

        # ``x_sq`` is in the data but was never ticked, so it never reaches training.
        assert captured == [(True, ["x", "y"], ["x", "y"])]
        job = service._store.require_job(response.job_id)
        assert job["status"] == "completed", job
        assert launched

    def test_glm_training_sink_drops_nothing(self, glm_collision_data, tmp_path, monkeypatch):
        captured = _spy_on_sink_projection(monkeypatch)
        from haute.schemas import TrainRequest
        from tests.test_training_worker_protocol import _SuccessfulTrainingJob

        body = TrainRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    **_GLM_SINK_CONFIG,
                    "family": "gaussian",
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
        )
        service, launched = _inline_sink_service(monkeypatch)

        with patch("haute.modelling.TrainingJob", _SuccessfulTrainingJob):
            response = service.start(body)
            join_training_preparation(response.job_id)
            for thread in launched:
                thread.join(timeout=10)

        assert len(captured) == 1
        project_to_keep_columns, _keep_columns, columns = captured[0]
        assert project_to_keep_columns is False
        # ``x`` is the live GLM term; the sink never projects it away.
        assert "x" in columns and "y" in columns
        job = service._store.require_job(response.job_id)
        assert job["status"] == "completed", job
        assert launched

    def test_glm_dispersion_sink_drops_nothing(self, glm_collision_data, tmp_path, monkeypatch):
        captured = _spy_on_sink_projection(monkeypatch)
        from haute.schemas import DispersionEstimateRequest

        body = DispersionEstimateRequest(
            graph=_glm_schema_gate_graph(
                glm_collision_data,
                {
                    **_GLM_SINK_CONFIG,
                    "family": "tweedie",
                    "evaluation": _random_evaluation_config(),
                },
            ),
            node_id="train",
            param="var_power",
        )
        service, launched = _inline_sink_service(monkeypatch)

        response = service.start_dispersion_estimate(body)
        join_training_preparation(response.job_id)
        for thread in launched:
            thread.join(timeout=10)

        assert len(captured) == 1
        project_to_keep_columns, _keep_columns, columns = captured[0]
        assert project_to_keep_columns is False
        assert "x" in columns and "y" in columns
        job = service._store.require_job(response.job_id)
        assert job["status"] == "completed", job
