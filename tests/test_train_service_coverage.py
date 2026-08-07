"""Coverage tests for TrainService error/cleanup branches.

These exercise the extracted engine arms that the route-level tests in
``test_modelling_routes.py`` don't reach directly: the RAM-estimate and
VRAM-estimate failure handlers, the missing-node ``ValueError`` arm in
``_execute_and_sink``, the column-projection path, and the GLM-config /
keep-columns merge inside ``start``.  An untested failure path can leave a
training job stuck in the wrong state, so these assert on job-store state too.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._worker_protocol import (
    WorkerArtifactManifest,
    WorkerProgressEvent,
    WorkerProtocolError,
    WorkerResultManifest,
)
from haute.errors import BoundedMemoryUnsupportedError, PreambleError
from haute.projection import AllExcept
from haute.routes._job_store import JobStore
from haute.routes._train_service import TrainService
from tests.conftest import make_edge, make_graph
from tests.test_training_worker_protocol import _inline_protocol_runner, _SuccessfulTrainingJob


def _training_execution_context() -> ExecutionContext:
    """A real TRAINING_PREP context with no admission reservation to release."""
    return ExecutionContext(
        operation="training_pipeline",
        profile=ExecutionProfile.TRAINING_PREP,
        job_id="ctx-job",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_only_request(path: str = "x.parquet"):
    """Build a TrainRequest whose graph is a single dataInput node."""
    from haute.schemas import TrainRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "n",
                    "data": {
                        "label": "n",
                        "nodeType": "dataInput",
                        "config": {"path": path},
                    },
                }
            ],
            "edges": [],
        }
    )
    return TrainRequest(graph=graph, node_id="n")


def _patch_execute_env():
    """Common patches so _execute_and_sink runs without touching real I/O."""
    return (
        patch("haute.executor._build_node_fn", return_value=None),
        patch("haute.modelling._algorithms._mem_checkpoint"),
        patch("haute.modelling._algorithms._MEM_LOG", MagicMock(write_text=MagicMock())),
        patch("haute.executor._preview_cache", MagicMock()),
        patch("haute.trace._cache", MagicMock()),
    )


# ---------------------------------------------------------------------------
# _estimate_ram failure arm (lines 409-411)
# ---------------------------------------------------------------------------


class TestEstimateRamFailure:
    def test_estimate_failure_raises_http_422(self):
        """When estimate_safe_training_rows raises, _estimate_ram fails loudly.

        nick-dev replaced the old swallow-and-fall-back-to-no-row-limit policy
        with a typed HTTP 422 so a broken memory probe surfaces to the API layer
        instead of silently training with no row cap.
        """
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        graph = make_graph({"nodes": [], "edges": []})

        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            side_effect=RuntimeError("probe blew up"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                service._estimate_ram(
                    graph,
                    "n",
                    preamble_ns=None,
                    job_id=job_id,
                )

        assert exc_info.value.status_code == 422
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error_code"] == "training_memory_estimate_failed"
        assert detail["error"] == "probe blew up"


# ---------------------------------------------------------------------------
# _check_gpu_vram_before_launch failure arm
# ---------------------------------------------------------------------------


class TestCheckGpuFallbackFailure:
    def test_vram_estimate_failure_is_swallowed(self):
        """A VRAM-probe exception must not propagate; ram_warning is unchanged."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        train_params: dict[str, object] = {"task_type": "GPU"}
        with patch(
            "haute.routes._train_service._check_gpu_vram",
            side_effect=RuntimeError("nvml exploded"),
        ):
            result = service._check_gpu_vram_before_launch(
                train_params,
                row_limit=100,
                total_source_rows=200,
                probe_columns=5,
                ram_warning="prior warning",
                job_id=job_id,
            )

        # Exception swallowed: original warning returned, task_type left on GPU,
        # and the failed check is surfaced as a job advisory rather than silence.
        assert result == "prior warning"
        assert train_params["task_type"] == "GPU"
        job = store.require_job(job_id)
        assert job["status"] == "running"
        assert "could not be checked" in job["gpu_warning"]
        assert job["warning"] == f"prior warning\n{job['gpu_warning']}"

    def test_non_gpu_task_returns_early(self):
        """Non-GPU task_type short-circuits without any VRAM probe."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        train_params: dict[str, object] = {"task_type": "CPU"}
        with patch("haute.routes._train_service._check_gpu_vram") as mock_vram:
            result = service._check_gpu_vram_before_launch(
                train_params,
                row_limit=None,
                total_source_rows=None,
                probe_columns=0,
                ram_warning=None,
                job_id=job_id,
            )

        assert result is None
        mock_vram.assert_not_called()


# ---------------------------------------------------------------------------
# _execute_and_sink — missing target node arm (line 513)
# ---------------------------------------------------------------------------


class TestExecuteAndSinkMissingTarget:
    def test_no_target_lf_raises_http_500_and_marks_error(self, tmp_path):
        """If no LazyFrame arrives at the target node, fail with HTTP 500."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        def lazy_without_target(*args, **kwargs):
            # Returns the 4-tuple shape but with the target node absent.
            return ({}, [], {}, {})

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_without_target,
            ),
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)

        assert exc_info.value.status_code == 500
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "Pipeline execution failed" in job["message"]


# ---------------------------------------------------------------------------
# _execute_and_sink — column projection path (lines 524-535)
# ---------------------------------------------------------------------------


class TestExecuteAndSinkProjection:
    def test_excluded_columns_dropped_before_sink(self, tmp_path):
        """exclude + keep_columns should drop excluded non-keep columns."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        lf = pl.LazyFrame(
            {
                "y": [1.0, 2.0, 3.0],
                "keep_me": [4, 5, 6],
                "drop_me": [7, 8, 9],
                "x1": [0.1, 0.2, 0.3],
            }
        )

        sunk_frames: list[object] = []

        def fake_bounded_sink(frame, path, **kwargs):
            sunk_frames.append(frame)

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_returns_target,
            ),
            patch("haute._polars_utils.bounded_sink", side_effect=fake_bounded_sink),
            patch("haute._polars_utils._malloc_trim"),
            p1,
            p2,
            p3,
            p4,
            p5,
        ):
            tmp_parquet = service._execute_and_sink(
                body,
                preamble_ns=None,
                row_limit=2,
                job_id=job_id,
                exclude=["drop_me", "keep_me"],
                keep_columns=["y", "keep_me"],
            )

        assert Path(tmp_parquet).name.startswith("haute_train_")
        assert len(sunk_frames) == 1
        cols = sunk_frames[0].collect_schema().names()
        # drop_me is excluded and not in keep_columns → dropped.
        assert "drop_me" not in cols
        # keep_me is excluded but protected → retained.
        assert "keep_me" in cols
        assert "y" in cols
        # Cleanup the temp file the helper created.
        Path(tmp_parquet).unlink(missing_ok=True)

    def test_no_drop_when_nothing_excluded_matches(self, tmp_path):
        """exclude listing only protected columns drops nothing."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        lf = pl.LazyFrame({"y": [1.0, 2.0], "x1": [0.1, 0.2]})
        sunk_frames: list[object] = []

        def fake_bounded_sink(frame, path, **kwargs):
            sunk_frames.append(frame)

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_returns_target,
            ),
            patch("haute._polars_utils.bounded_sink", side_effect=fake_bounded_sink),
            patch("haute._polars_utils._malloc_trim"),
            p1,
            p2,
            p3,
            p4,
            p5,
        ):
            tmp_parquet = service._execute_and_sink(
                body,
                preamble_ns=None,
                row_limit=None,
                job_id=job_id,
                exclude=["y"],
                keep_columns=["y"],
            )

        cols = sunk_frames[0].collect_schema().names()
        assert cols == ["y", "x1"]
        Path(tmp_parquet).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# start() — GLM config merge + keep-columns (lines 272-273, 287-291)
# ---------------------------------------------------------------------------

# Minimal canonical evaluation object — public configs must supply exactly one.
MINIMAL_EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 42,
    "validation": {"method": "single", "size": 0.2},
}


class TestStartGlmMergeAndKeepColumns:
    def _glm_graph(self):
        config = {
            "target": "loss",
            "algorithm": "glm",
            "family": "poisson",
            "link": "log",
            "all_factors": True,
            "weight": "exposure",
            "offset": "log_exp",
            "feature_columns": ["x1"],
            "exclude": ["junk", "x1"],
            "params": {"iterations": 3},
            "evaluation": MINIMAL_EVALUATION,
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": {"path": "data.parquet"},
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
                "edges": [make_edge("source", "train").model_dump()],
            }
        )
        return graph

    def test_glm_keys_merged_and_offset_weight_kept(self):
        """GLM top-level keys merge into train_params; weight+offset join keep_cols."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        body = TrainRequest(graph=self._glm_graph(), node_id="train")

        captured: dict[str, object] = {}

        def fake_execute_and_sink(
            _body, _preamble, _row_limit, _job_id, *, exclude, keep_columns, **kwargs
        ):
            captured["exclude"] = exclude
            captured["keep_columns"] = keep_columns
            return "/tmp/fake_train.parquet"

        def fake_launch(job_id, node_id, config, train_params, *args, **kwargs):
            captured["train_params"] = train_params
            captured["config"] = config

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(
                service,
                "_estimate_ram",
                return_value=(None, None, 100, 3),
            ),
            patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
            patch.object(service, "_execute_and_sink", side_effect=fake_execute_and_sink),
            patch.object(service, "_launch_background", side_effect=fake_launch),
        ):
            resp = service.start(body)
            service._join_preparation(resp.job_id)

        assert resp.status == "started"
        created_job = next(iter(store.jobs.values()))
        assert isinstance(created_job["start_time"], float)
        assert created_job["timeout"] > 0
        # GLM top-level config keys merged into train_params (line 272-273).
        tp = captured["train_params"]
        assert tp["family"] == "poisson"
        assert tp["link"] == "log"
        # Protected columns: target + weight + offset. nick-dev builds these from a
        # set (_training_required_metadata_columns), so membership — not order — is
        # the contract; assert order-independently.
        keep = captured["keep_columns"]
        assert set(keep) == {"loss", "exposure", "log_exp", "x1"}
        # The excluded column is forwarded as the exclude list.
        assert captured["exclude"] == ["junk", "x1"]

    def test_start_stamps_explicit_timeout_before_preparation(self):
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        graph = self._glm_graph()
        for node in graph.nodes:
            if node.id == "train":
                node.data.config["timeout"] = 17
        body = TrainRequest(graph=graph, node_id="train")
        observed: dict[str, object] = {}

        def inspect_created_job(*_args, **_kwargs):
            job = next(iter(store.jobs.values()))
            observed["start_time"] = job.get("start_time")
            observed["timeout"] = job.get("timeout")
            raise RuntimeError("stop after create")

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", side_effect=inspect_created_job),
        ):
            response = service.start(body)
            service._join_preparation(response.job_id)

        assert isinstance(observed["start_time"], float)
        assert observed["timeout"] == 17
        assert store.require_job(response.job_id)["status"] == "error"

    def test_failure_during_execute_marks_background_job_error(self):
        """A preparation exception is persisted instead of escaping its thread."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        body = TrainRequest(graph=self._glm_graph(), node_id="train")

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
            patch.object(
                service,
                "_execute_and_sink",
                side_effect=RuntimeError("sink failed"),
            ),
        ):
            response = service.start(body)
            service._join_preparation(response.job_id)

        # The single created job should be in error state, not left running.
        running = [j for j in store.jobs.values() if j["status"] == "running"]
        assert running == []
        errored = [j for j in store.jobs.values() if j["status"] == "error"]
        assert errored
        assert "sink failed" in errored[0]["error"]


# ---------------------------------------------------------------------------
# _execute_and_sink — execution_context-present sink path + metrics finally
# (lines 940-954, 1014-1018, 1019->1022) and the AllExceptColumns demand
# branch (903-904).  multi-frame threads an ExecutionContext + per-node demand
# through _execute_and_sink; the VC tests call it with neither, so the
# context-staged sink and the AllExcept demand-merge arms went uncovered.
# ---------------------------------------------------------------------------


class TestExecuteAndSinkWithExecutionContext:
    def test_context_stages_sink_and_publishes_metrics(self, tmp_path):
        """With a context, the sink runs inside a staged region and metrics are
        published in the finally arm; the checkpoint_dir is also cleaned up."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        lf = pl.LazyFrame({"y": [1.0, 2.0], "x1": [0.1, 0.2]})
        sunk_frames: list[object] = []

        def fake_bounded_sink(frame, path, **kwargs):
            sunk_frames.append(frame)

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        context = _training_execution_context()

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_returns_target,
            ),
            patch("haute._polars_utils.bounded_sink", side_effect=fake_bounded_sink),
            patch("haute._polars_utils._malloc_trim"),
            p1,
            p2,
            p3,
            p4,
            p5,
        ):
            tmp_parquet = service._execute_and_sink(
                body,
                preamble_ns=None,
                row_limit=None,
                job_id=job_id,
                execution_context=context,
            )

        # The staged context branch (940-954) was taken: the frame was sunk and
        # the before/after sink checkpoints recorded stage timings.
        assert len(sunk_frames) == 1
        # The finally arm (1014-1018) published execution metrics onto the job.
        job = store.require_job(job_id)
        assert "execution_metrics" in job
        metrics = job["execution_metrics"]
        assert "training_sink_write" in metrics["stage_elapsed_ms"]
        Path(tmp_parquet).unlink(missing_ok=True)

    def test_all_except_demand_columns_required_and_missing_raises_422(self, tmp_path):
        """An AllExcept node demand contributes its required_columns to the
        training-input contract; a missing one trips the 422 contract error and
        flips the job to contract_error (903-904 + 908-922 + finally 1014-1018)."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        # Frame is missing the AllExcept-required "target_col".
        lf = pl.LazyFrame({"x1": [0.1, 0.2], "x2": [0.3, 0.4]})

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        demand = {
            "n": AllExcept(
                required_columns=frozenset({"target_col"}),
                excluded_columns=frozenset({"target_col"}),
            )
        }
        context = _training_execution_context()

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_returns_target,
            ),
            patch("haute._polars_utils.bounded_sink"),
            patch("haute._polars_utils._malloc_trim"),
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(
                body,
                preamble_ns=None,
                row_limit=None,
                job_id=job_id,
                required_columns_by_node=demand,
                execution_context=context,
            )

        assert exc_info.value.status_code == 422
        assert "target_col" in str(exc_info.value.detail)
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        # Even on the contract-error raise, the finally arm published metrics.
        assert "execution_metrics" in job

    def test_iterable_demand_columns_required(self, tmp_path):
        """A plain-iterable (non-AllExcept) node demand also contributes its
        columns to the contract (branch 905-906)."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        lf = pl.LazyFrame({"x1": [0.1, 0.2]})

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        demand = {"n": frozenset({"needed_col"})}

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_returns_target,
            ),
            patch("haute._polars_utils.bounded_sink"),
            patch("haute._polars_utils._malloc_trim"),
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(
                body,
                preamble_ns=None,
                row_limit=None,
                job_id=job_id,
                required_columns_by_node=demand,
            )

        assert exc_info.value.status_code == 422
        assert "needed_col" in str(exc_info.value.detail)
        assert store.require_job(job_id)["status"] == "contract_error"


# ---------------------------------------------------------------------------
# _execute_and_sink — memory-limit + bounded-unsupported cleanup arms
# (lines 966-979, 980-994).  multi-frame added these typed cleanup handlers;
# each must unlink the temp parquet, transition the job, and re-raise the
# right HTTP shape.
# ---------------------------------------------------------------------------


class TestExecuteAndSinkCleanupArms:
    def test_public_contract_error_unlinks_temp_and_preserves_payload(self):
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()
        unlinked: list[str] = []

        def lazy_raises_contract_error(*args, **kwargs):
            raise PreambleError("invalid preamble", source_line=7)

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_raises_contract_error,
            ),
            patch(
                "haute.routes._train_service.os.unlink",
                side_effect=lambda path: unlinked.append(path),
            ),
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == {
            "error_code": "preamble_failed",
            "message": "invalid preamble",
            "source_line": 7,
        }
        assert unlinked and unlinked[0].endswith(".parquet")
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert job["error_code"] == "preamble_failed"

    def test_memory_limit_unlinks_temp_and_raises_507(self, tmp_path):
        """ExecutionMemoryLimitExceededError → temp parquet removed, job
        memory_limited, HTTP 507 (lines 966-979)."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        created_paths: list[str] = []
        real_exists = os.path.exists

        def lazy_raises_memory(*args, **kwargs):
            raise ExecutionMemoryLimitExceededError(
                "training_pipeline",
                rss_bytes=20,
                limit_bytes=10,
                job_id=job_id,
            )

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_raises_memory,
            ),
            patch(
                "haute.routes._train_service.os.unlink",
                side_effect=lambda p: created_paths.append(p),
            ),
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)

        assert exc_info.value.status_code == 507
        # The freshly mkstemp'd temp parquet was unlinked on the way out.
        assert created_paths and created_paths[0].endswith(".parquet")
        assert real_exists  # sanity: os module still intact
        assert store.require_job(job_id)["status"] == "memory_limited"

    def test_bounded_unsupported_unlinks_temp_and_raises_422(self, tmp_path):
        """BoundedMemoryUnsupportedError → temp removed, job contract_error,
        HTTP 422 (lines 980-994)."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        body = _source_only_request()

        unlinked: list[str] = []

        def lazy_raises_unsupported(*args, **kwargs):
            raise BoundedMemoryUnsupportedError("node X cannot stream")

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_raises_unsupported,
            ),
            patch(
                "haute.routes._train_service.os.unlink",
                side_effect=lambda p: unlinked.append(p),
            ),
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)

        assert exc_info.value.status_code == 422
        assert "bounded streaming mode" in str(exc_info.value.detail)
        assert unlinked and unlinked[0].endswith(".parquet")
        job = store.require_job(job_id)
        assert job["status"] == "contract_error"


# ---------------------------------------------------------------------------
# _execute_and_sink — cleanup arms when the temp parquet is already gone and
# when the checkpoint dir never materialised. Each error handler guards the
# unlink with ``if os.path.exists(tmp_parquet)``; the False arm (967->969,
# 981->983, 996->998, 1000->1002) and the finally's checkpoint-dir-absent arm
# (1019->1022) are only reachable when those paths don't exist.
# ---------------------------------------------------------------------------


class TestExecuteAndSinkCleanupAbsentPaths:
    def _service_body(self):
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})
        return store, service, job_id, _source_only_request()

    def _run_with_absent_paths(self, service, body, job_id, lazy_side_effect):
        """Run _execute_and_sink with the temp parquet absent, exercising the
        cleanup guards' skip-unlink arms.

        The training temp parquet is deleted the instant mkstemp hands it back,
        so the ``if Path(tmp_parquet).exists()`` guards are naturally False
        without mocking pathlib globally. Only the ``haute_train_`` temp is
        special-cased; the dataframe-cache machinery's own mkstemp temps are
        delegated to the real implementation untouched. os.path.exists stays
        globally False (as before) to keep that machinery off real filesystem
        I/O, and os.unlink is mocked as the belt-and-braces assertion target."""
        import tempfile as _tempfile

        ckpt_missing = "/nonexistent/haute_ckpt_absent"
        real_mkstemp = _tempfile.mkstemp
        real_unlink = os.unlink  # capture before the with-block mocks os.unlink
        real_close = os.close

        def _mkstemp_training_absent(*a, **k):
            fd, path = real_mkstemp(*a, **k)
            if k.get("prefix") == "haute_train_":
                # Drop the training temp immediately so the cleanup guards see it
                # as already gone. Windows cannot unlink an open mkstemp file,
                # so close it first and return a harmless replacement descriptor
                # for the caller's unconditional os.close.
                real_close(fd)
                real_unlink(path)
                fd = os.open(os.devnull, os.O_RDONLY)
            return fd, path

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=lazy_side_effect,
            ),
            patch("haute.routes._train_service.os.path.exists", return_value=False),
            patch.object(_tempfile, "mkstemp", side_effect=_mkstemp_training_absent),
            patch.object(_tempfile, "mkdtemp", return_value=ckpt_missing),
            patch("haute.routes._train_service.os.unlink") as mock_unlink,
            p1,
            p2,
            p3,
            p4,
            p5,
            pytest.raises(HTTPException) as exc_info,
        ):
            service._execute_and_sink(body, preamble_ns=None, row_limit=None, job_id=job_id)
        return exc_info, mock_unlink

    def test_memory_limit_skips_unlink_when_temp_absent(self):
        """ExecutionMemoryLimitExceededError with the temp already gone: the
        unlink guard is False (967->969) and no unlink is attempted."""
        store, service, job_id, body = self._service_body()

        def lazy(*a, **k):
            raise ExecutionMemoryLimitExceededError(
                "training_pipeline", rss_bytes=2, limit_bytes=1, job_id=job_id
            )

        exc_info, mock_unlink = self._run_with_absent_paths(service, body, job_id, lazy)
        assert exc_info.value.status_code == 507
        mock_unlink.assert_not_called()
        assert store.require_job(job_id)["status"] == "memory_limited"

    def test_bounded_unsupported_skips_unlink_when_temp_absent(self):
        """BoundedMemoryUnsupportedError with the temp already gone (981->983)."""
        store, service, job_id, body = self._service_body()

        def lazy(*a, **k):
            raise BoundedMemoryUnsupportedError("cannot stream")

        exc_info, mock_unlink = self._run_with_absent_paths(service, body, job_id, lazy)
        assert exc_info.value.status_code == 422
        mock_unlink.assert_not_called()
        assert store.require_job(job_id)["status"] == "contract_error"

    def test_http_reraise_skips_unlink_when_temp_absent(self):
        """A re-raised HTTPException with the temp gone (996->998). The missing
        target node raises an HTTPException inside the try, which the HTTPException
        handler re-raises after the (skipped) unlink guard."""
        store, service, job_id, body = self._service_body()

        # Returns the 4-tuple shape but without the target node, so the body
        # raises HTTPException(422) for missing required columns... actually the
        # missing-target path raises ValueError → caught by generic handler.
        # To hit the HTTPException re-raise arm, raise an HTTPException directly.
        def lazy(*a, **k):
            raise HTTPException(status_code=418, detail="teapot")

        exc_info, mock_unlink = self._run_with_absent_paths(service, body, job_id, lazy)
        assert exc_info.value.status_code == 418
        mock_unlink.assert_not_called()

    def test_generic_failure_skips_unlink_when_temp_absent(self):
        """A generic Exception with the temp gone (1000->1002) plus the
        checkpoint-dir-absent finally arm (1019->1022)."""
        store, service, job_id, body = self._service_body()

        def lazy(*a, **k):
            raise RuntimeError("kaboom")

        exc_info, mock_unlink = self._run_with_absent_paths(service, body, job_id, lazy)
        assert exc_info.value.status_code == 500
        mock_unlink.assert_not_called()
        assert store.require_job(job_id)["status"] == "error"


# ---------------------------------------------------------------------------
# start() — execution_context lifecycle + memory-limit transition (533-541,
# finally release on early failure 588-589).  multi-frame creates the admitted
# context in start() and must release admission when launch never starts.
# ---------------------------------------------------------------------------


class TestStartExecutionContextLifecycle:
    def _min_graph(self):
        from haute.schemas import TrainRequest

        config = {
            "target": "loss",
            "algorithm": "catboost",
            "loss_function": "RMSE",
            "params": {"iterations": 2},
            "evaluation": MINIMAL_EVALUATION,
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": {"path": "data.parquet"},
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
                "edges": [make_edge("source", "train").model_dump()],
            }
        )
        return TrainRequest(graph=graph, node_id="train")

    def test_memory_limit_during_execute_marks_memory_limited(self):
        """A preparation memory limit becomes a structured terminal 507 job."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        body = self._min_graph()

        released: list[bool] = []

        def fake_execute(*args, **kwargs):
            raise ExecutionMemoryLimitExceededError(
                "training_pipeline",
                rss_bytes=2,
                limit_bytes=1,
                job_id="x",
            )

        # A real admission_release callback so start()'s finally arm
        # (execution_context.release_admission()) is observably invoked.
        ctx = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            job_id="ctx-job",
            admission_release=lambda: released.append(True),
        )

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
            patch(
                "haute.routes._train_service.create_admitted_execution_context",
                return_value=ctx,
            ),
            patch("haute.routes._train_service.bind_running_execution_metrics_publisher"),
            patch.object(service, "_execute_and_sink", side_effect=fake_execute),
        ):
            response = service.start(body)
            service._join_preparation(response.job_id)

        jobs = list(store.jobs.values())
        assert jobs and jobs[0]["status"] == "memory_limited"
        assert jobs[0]["http_status_code"] == 507
        assert jobs[0]["error_code"] == "memory_limit"
        assert jobs[0]["error_detail"]["message"]
        # launch never started → finally released the admitted context.
        assert released == [True]

    def test_start_returns_handle_before_preparation_and_cancel_reaches_token(self):
        """The returned handle can cancel preparation while its sink is blocked."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        body = self._min_graph()
        released: list[bool] = []
        ctx = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            job_id="ctx-job",
            admission_release=lambda: released.append(True),
        )

        preparation_entered = threading.Event()
        release_preparation = threading.Event()

        def blocked_execute(*_args, execution_context, **_kwargs):
            preparation_entered.set()
            assert release_preparation.wait(timeout=5)
            execution_context.checkpoint(label="training_materialisation")
            raise AssertionError("cancelled materialisation continued")

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
            patch(
                "haute.routes._train_service.create_admitted_execution_context",
                return_value=ctx,
            ),
            patch("haute.routes._train_service.bind_running_execution_metrics_publisher"),
            patch.object(
                service,
                "_execute_and_sink",
                side_effect=blocked_execute,
            ),
        ):
            response = service.start(body)
            assert response.status == "started"
            assert preparation_entered.wait(timeout=5)
            assert store.require_job(response.job_id)["status"] == "running"
            assert service.cancel(response.job_id)["status"] == "cancelled"
            release_preparation.set()
            service._join_preparation(response.job_id)

        jobs = list(store.jobs.values())
        assert jobs and jobs[0]["status"] == "cancelled"
        assert released == [True]

    def test_preparation_thread_start_failure_is_terminal_and_releases_registry(self):
        """A thread-launch failure cannot leave an uncancellable running job."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)

        with (
            patch(
                "haute.routes._train_service.threading.Thread.start",
                side_effect=RuntimeError("thread unavailable"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.start(self._min_graph())

        assert exc_info.value.status_code == 500
        (job_id,) = store.jobs
        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert "failed to start" in job["message"]
        assert service._training_jobs.cancel(job_id) is False


# ---------------------------------------------------------------------------
# Pure helper functions: _string_list_config, _training_required_metadata_columns,
# _training_required_columns_by_node, _job_elapsed_seconds, _check_gpu_vram.
# These are module-level and validate config-shape invariants; their error and
# early-return arms (197, 202, 204, 219->229, 247, 348-349, 368, 384->390) are
# only reachable with the right config shapes, not via the orchestration tests.
# ---------------------------------------------------------------------------


class TestStringListConfig:
    def test_non_list_value_raises(self):
        """A scalar where a list is expected is a config error (line 197)."""
        from haute.routes._train_service import _string_list_config

        with pytest.raises(ValueError, match="must be a list of column names"):
            _string_list_config({"id_columns": "not_a_list"}, "id_columns")

    def test_non_string_member_raises(self):
        """A non-string / empty member is rejected (line 202)."""
        from haute.routes._train_service import _string_list_config

        with pytest.raises(ValueError, match="non-empty string column names"):
            _string_list_config({"id_columns": ["ok", 123]}, "id_columns")

    def test_duplicate_members_deduplicated(self):
        """Duplicates are skipped while order is preserved (line 204 continue)."""
        from haute.routes._train_service import _string_list_config

        out = _string_list_config({"id_columns": ["a", "b", "a", "c", "b"]}, "id_columns")
        assert out == ["a", "b", "c"]


class TestRequiredMetadataColumns:
    def test_non_dict_evaluation_is_ignored(self):
        """A non-dict evaluation value skips the evaluation-column block."""
        from haute.routes._train_service import _training_required_metadata_columns

        cols = _training_required_metadata_columns(
            {"target": "y", "evaluation": "random", "id_columns": ["pid"]}
        )
        # target + id_columns survive; the malformed evaluation adds no column.
        assert cols == {"y", "pid"}

    def test_temporal_evaluation_adds_date_column(self):
        """A temporal evaluation contributes its date_column to the keep set."""
        from haute.routes._train_service import _training_required_metadata_columns

        cols = _training_required_metadata_columns(
            {
                "target": "y",
                "evaluation": {"strategy": "temporal", "date_column": "asof"},
            }
        )
        assert cols == {"y", "asof"}


class TestRequiredColumnsByNode:
    def test_missing_target_returns_none(self):
        """No target → no derivable column demand (line 247)."""
        from haute.routes._train_service import _training_required_columns_by_node

        assert _training_required_columns_by_node("n", {"algorithm": "catboost"}) is None


class TestJobElapsedSeconds:
    def test_falls_back_to_elapsed_seconds_field(self):
        """Without start_time, the stored elapsed_seconds is used (348-349)."""
        from haute.routes._train_service import _job_elapsed_seconds

        assert _job_elapsed_seconds({"elapsed_seconds": 12.5}) == 12.5

    def test_non_numeric_elapsed_uses_fallback(self):
        """A non-numeric elapsed_seconds yields the fallback (line 349 else)."""
        from haute.routes._train_service import _job_elapsed_seconds

        assert _job_elapsed_seconds({"elapsed_seconds": "nope"}, fallback=3.0) == 3.0


class TestCheckGpuVramHelper:
    def test_zero_rows_returns_empty_check(self):
        """Non-positive rows/columns short-circuit to an empty check (line 368)."""
        from haute.routes._train_service import _check_gpu_vram

        check = _check_gpu_vram(0, 5, {})
        assert check.estimated_mb is None
        assert check.available_mb is None
        assert check.warning is None

    def test_sufficient_vram_yields_no_warning(self):
        """When estimated VRAM fits available VRAM, no warning (384->390)."""
        from haute.routes import _train_service

        with (
            patch.object(
                _train_service,
                "_DEFAULT_BORDER_COUNT",
                128,
            ),
            patch(
                "haute._ram_estimate.estimate_gpu_vram_bytes",
                return_value=1 * 1024**3,
            ),
            patch(
                "haute._host_memory.available_vram_bytes",
                return_value=8 * 1024**3,
            ),
        ):
            check = _train_service._check_gpu_vram(1000, 10, {})

        assert check.warning is None
        assert check.estimated_mb is not None
        assert check.available_mb is not None

    def test_unknown_vram_warns_without_refusing(self):
        """Unknown VRAM is advisory: warning set, ``insufficient`` stays False."""
        from haute.routes import _train_service

        with (
            patch(
                "haute._ram_estimate.estimate_gpu_vram_bytes",
                return_value=1 * 1024**3,
            ),
            patch(
                "haute._host_memory.available_vram_bytes",
                return_value=None,
            ),
        ):
            check = _train_service._check_gpu_vram(1000, 10, {})

        assert check.warning is not None
        assert "could not be detected" in check.warning
        assert check.insufficient is False
        assert check.available_mb is None
        assert check.estimated_mb is not None

    def test_insufficient_vram_sets_blocking_flag(self):
        """Observed-too-small VRAM is the one state that refuses a launch."""
        from haute.routes import _train_service

        with (
            patch(
                "haute._ram_estimate.estimate_gpu_vram_bytes",
                return_value=8 * 1024**3,
            ),
            patch(
                "haute._host_memory.available_vram_bytes",
                return_value=1 * 1024**3,
            ),
        ):
            check = _train_service._check_gpu_vram(1000, 10, {})

        assert check.warning is not None
        assert check.insufficient is True


# ---------------------------------------------------------------------------
# Public cancel guards and the start() categorical-levels merge.
# ---------------------------------------------------------------------------


class TestCancelGuards:
    def test_cancel_non_training_job_raises_404(self):
        """cancel on a job that isn't a training job → 404 (line 597)."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running", "job_type": "scoring"})

        with pytest.raises(HTTPException) as exc_info:
            service.cancel(job_id)
        assert exc_info.value.status_code == 404

    def test_cancel_non_running_job_returns_job_unchanged(self):
        """cancel on an already-finished training job is a no-op (line 599)."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "completed", "job_type": "training"})

        result = service.cancel(job_id)
        assert result["status"] == "completed"


class TestStartCategoricalLevelsMerge:
    def test_declared_levels_merged_into_config(self):
        """Upstream categorical-level declarations are merged into the config
        threaded to launch (line 430)."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        config = {
            "target": "loss",
            "algorithm": "catboost",
            "loss_function": "RMSE",
            "categorical_levels": {"region": ["north", "south"]},
            "params": {"iterations": 2},
            "evaluation": MINIMAL_EVALUATION,
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataInput",
                            "config": {"path": "data.parquet"},
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
                "edges": [make_edge("source", "train").model_dump()],
            }
        )
        store = JobStore()
        service = TrainService(store)
        body = TrainRequest(graph=graph, node_id="train")

        captured: dict[str, object] = {}

        def fake_launch(job_id, node_id, cfg, train_params, *args, **kwargs):
            captured["config"] = cfg

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch.object(service, "_check_gpu_vram_before_launch", return_value=None),
            patch.object(service, "_execute_and_sink", return_value="/tmp/fake.parquet"),
            patch.object(service, "_launch_background", side_effect=fake_launch),
        ):
            response = service.start(body)
            service._join_preparation(response.job_id)

        # The merged config still carries the declared levels (round-tripped
        # through the merge helper that line 430 installs).
        assert "categorical_levels" in captured["config"]
        assert "region" in captured["config"]["categorical_levels"]


# ---------------------------------------------------------------------------
# _check_gpu_vram_before_launch — feasible GPU VRAM returns the prior warning
# (the 768->794 short-circuit: vram_check.warning is falsy).
# ---------------------------------------------------------------------------


class TestCheckGpuFallbackNoWarning:
    def test_gpu_task_with_feasible_vram_returns_ram_warning(self):
        """task_type=GPU but VRAM fits → no exception, ram_warning passed through
        (branch 768->794)."""
        from haute.routes._job_store import JobStore
        from haute.routes._train_service import _VramCheck

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        train_params: dict[str, object] = {"task_type": "GPU"}
        with patch(
            "haute.routes._train_service._check_gpu_vram",
            return_value=_VramCheck(estimated_mb=10.0, available_mb=100.0, warning=None),
        ):
            result = service._check_gpu_vram_before_launch(
                train_params,
                row_limit=50,
                total_source_rows=100,
                probe_columns=4,
                ram_warning="ram!",
                job_id=job_id,
            )

        assert result == "ram!"
        assert store.require_job(job_id)["status"] == "running"

    def test_gpu_task_with_unknown_vram_warns_and_proceeds(self):
        """Unknown VRAM attaches a job warning but does not refuse the launch."""
        from haute.routes._job_store import JobStore
        from haute.routes._train_service import _VramCheck

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        advisory = "GPU VRAM could not be detected (no NVIDIA GPU found)."
        train_params: dict[str, object] = {"task_type": "GPU"}
        with patch(
            "haute.routes._train_service._check_gpu_vram",
            return_value=_VramCheck(
                estimated_mb=10.0,
                available_mb=None,
                warning=advisory,
                insufficient=False,
            ),
        ):
            result = service._check_gpu_vram_before_launch(
                train_params,
                row_limit=50,
                total_source_rows=100,
                probe_columns=4,
                ram_warning="ram!",
                job_id=job_id,
            )

        assert result == "ram!"
        job = store.require_job(job_id)
        assert job["gpu_warning"] == advisory
        assert job["warning"] == f"ram!\n{advisory}"


# ---------------------------------------------------------------------------
# _launch_background — use the deterministic inline protocol runner to cover
# progress/history, typed failures, parent cleanup, and supervisor start errors.
# ---------------------------------------------------------------------------


def _launch_config():
    return {
        "target": "y",
        "algorithm": "catboost",
        "loss_function": "RMSE",
        "params": {"iterations": 1},
        "evaluation": MINIMAL_EVALUATION,
    }


def _evaluation_preview_request(**config_overrides: object):
    from haute.schemas import TrainEstimateRequest

    config = {
        "target": "y",
        "task": "regression",
        "algorithm": "catboost",
        "loss_function": "RMSE",
        "metrics": ["rmse"],
        "evaluation": MINIMAL_EVALUATION,
        **config_overrides,
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
    return TrainEstimateRequest(graph=graph, node_id="train")


class TestEvaluationPreviewFailures:
    def test_incomplete_or_invalid_config_has_no_preview(self) -> None:
        service = TrainService(JobStore())
        assert (
            service.evaluation_preview(
                _evaluation_preview_request(target=""),
                row_limit=None,
            )
            is None
        )
        assert (
            service.evaluation_preview(
                _evaluation_preview_request(
                    evaluation={
                        "schema_version": 2,
                        "strategy": "random",
                        "seed": 42,
                        "validation": {"method": "none"},
                    }
                ),
                row_limit=None,
            )
            is None
        )

    @pytest.mark.parametrize(
        ("lazy_outputs", "message"),
        [
            ({}, "No training data arrived"),
            ({"train": pl.LazyFrame({"x": [1.0]})}, "missing required column"),
            ({"train": pl.LazyFrame({"y": [None]})}, "contains only null values"),
        ],
    )
    def test_data_dependent_preview_errors_are_actionable_422s(
        self,
        lazy_outputs: dict[str, pl.LazyFrame],
        message: str,
    ) -> None:
        from haute.routes import _train_service

        context = _training_execution_context()
        service = TrainService(JobStore())
        with (
            patch.object(
                _train_service,
                "create_admitted_execution_context",
                return_value=context,
            ),
            patch.object(TrainService, "_compile_preamble", return_value=None),
            patch.object(
                _train_service,
                "dataframe_graph_input_fingerprint",
                return_value="fingerprint",
            ),
            patch.object(
                _train_service,
                "build_dataframe_execution_cache_request",
                return_value=None,
            ),
            patch.object(
                _train_service,
                "execute_lazy_graph",
                return_value=(lazy_outputs,),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.evaluation_preview(
                _evaluation_preview_request(),
                row_limit=None,
            )

        assert exc_info.value.status_code == 422
        assert message in str(exc_info.value.detail)

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (PreambleError("bad preamble", source_line=2), "bad preamble"),
            (
                BoundedMemoryUnsupportedError("unsafe lazy plan"),
                "cannot run in bounded mode",
            ),
        ],
    )
    def test_preview_maps_public_and_bounded_execution_errors(
        self,
        error: BaseException,
        message: str,
    ) -> None:
        from haute.routes import _train_service

        context = _training_execution_context()
        service = TrainService(JobStore())
        with (
            patch.object(
                _train_service,
                "create_admitted_execution_context",
                return_value=context,
            ),
            patch.object(TrainService, "_compile_preamble", side_effect=error),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.evaluation_preview(
                _evaluation_preview_request(),
                row_limit=None,
            )

        assert exc_info.value.status_code == 422
        assert message in str(exc_info.value.detail)


class TestPreparationTerminalPaths:
    def test_cancelled_preparation_transitions_and_releases_owner(self) -> None:
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        estimate_body = _evaluation_preview_request()
        body = TrainRequest(
            graph=estimate_body.graph,
            node_id=estimate_body.node_id,
            source=estimate_body.source,
        )
        token = ExecutionCancellationToken()
        token.cancel()

        service._prepare_and_launch_training(
            job_id,
            body,
            "train",
            body.graph.node_map["train"].data.config,
            token,
        )

        job = store.require_job(job_id)
        assert job["status"] == "cancelled"
        assert job["message"] == "Cancelled"

    def test_server_preparation_http_failure_is_terminal_error(self) -> None:
        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )

        service._persist_preparation_http_failure(
            job_id,
            HTTPException(status_code=500, detail="preparation exploded"),
        )

        job = store.require_job(job_id)
        assert job["status"] == "error"
        assert job["message"] == "preparation exploded"

    def test_join_preparation_times_out_for_live_owner(self) -> None:
        store = JobStore()
        service = TrainService(store)
        owner = MagicMock()
        owner.is_alive.return_value = True
        service._preparation_threads["job-1"] = owner

        with pytest.raises(TimeoutError, match="still running"):
            service._join_preparation("job-1", timeout=0.01)

        owner.join.assert_called_once_with(timeout=0.01)


class TestLaunchBackgroundWorker:
    def _service_and_job(self):
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store, protocol_runner=_inline_protocol_runner)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "node_label": "train",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        return store, service, job_id

    def test_success_path_runs_progress_iteration_and_completes(self, tmp_path):
        """A TrainingJob whose run() invokes progress + on_iteration (past the
        loss-history cap) drives the success closures and the truncation arm
        (1075-1076); the job ends completed and the temp parquet is unlinked."""
        from haute.routes import _train_service

        store, service, job_id = self._service_and_job()
        context = _training_execution_context()
        tmp_parquet = str(tmp_path / "train.parquet")
        Path(tmp_parquet).write_text("x", encoding="utf-8")

        cap = _train_service._max_train_loss_history()

        class FakeJob(_SuccessfulTrainingJob):
            def run(self, progress, on_iteration, **kwargs):
                progress("working", 0.5)
                # Push more iterations than the cap so 1075-1076 truncates.
                for i in range(cap + 3):
                    on_iteration(i, cap + 3, {"loss": float(i)})
                return super().run(progress, on_iteration, **kwargs)

        with (
            patch("haute.modelling.TrainingJob", FakeJob),
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={"name": "train", "output_dir": str(tmp_path / "outputs")},
            ),
        ):
            thread = service._launch_background(
                job_id,
                "train",
                _launch_config(),
                {"iterations": 1},
                tmp_parquet,
                ram_warning=None,
                total_source_rows=100,
                execution_context=context,
            )
            assert thread is not None
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "completed"
        # The loss history was capped and flagged truncated.
        assert job["train_loss_history_truncated"] is True
        assert len(job["train_loss_history"]) == cap
        # Temp parquet removed in the worker finally (1229->exit true side).
        assert not Path(tmp_parquet).exists()

    def test_execution_cancelled_marks_cancelled(self, tmp_path):
        """An ExecutionCancelledError from run() → job cancelled (1164-1169)."""
        from haute._execution_context import ExecutionCancelledError
        from haute.routes import _train_service

        store, service, job_id = self._service_and_job()
        context = _training_execution_context()
        tmp_parquet = str(tmp_path / "train.parquet")
        Path(tmp_parquet).write_text("x", encoding="utf-8")

        class FakeJob:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, *a, **k):
                raise ExecutionCancelledError("training_pipeline")

        with (
            patch("haute.modelling.TrainingJob", FakeJob),
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={"name": "t", "output_dir": str(tmp_path / "outputs")},
            ),
        ):
            thread = service._launch_background(
                job_id,
                "train",
                _launch_config(),
                {"iterations": 1},
                tmp_parquet,
                ram_warning=None,
                total_source_rows=None,
                execution_context=context,
            )
            assert thread is not None
            thread.join_and_raise(timeout=10)

        assert store.require_job(job_id)["status"] == "cancelled"
        assert not Path(tmp_parquet).exists()

    def test_bounded_unsupported_marks_contract_error(self, tmp_path):
        """BoundedMemoryUnsupportedError from run() → contract_error (1185-1186)."""
        from haute.routes import _train_service

        store, service, job_id = self._service_and_job()
        context = _training_execution_context()
        tmp_parquet = str(tmp_path / "train.parquet")
        Path(tmp_parquet).write_text("x", encoding="utf-8")

        class FakeJob:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, *a, **k):
                raise BoundedMemoryUnsupportedError("cannot stream node Z")

        with (
            patch("haute.modelling.TrainingJob", FakeJob),
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={"name": "t", "output_dir": str(tmp_path / "outputs")},
            ),
        ):
            thread = service._launch_background(
                job_id,
                "train",
                _launch_config(),
                {"iterations": 1},
                tmp_parquet,
                ram_warning=None,
                total_source_rows=None,
                execution_context=context,
            )
            assert thread is not None
            thread.join_and_raise(timeout=10)

        job = store.require_job(job_id)
        assert job["status"] == "contract_error"
        assert "bounded streaming mode" in job["message"]
        assert not Path(tmp_parquet).exists()

    def test_thread_start_failure_marks_error_and_raises_500(self, tmp_path):
        """If Thread.start() blows up, the job flips to error, admission is
        released, the temp file is removed, and a 500 is raised (1236-1250)."""
        from haute.routes import _train_service

        store, service, job_id = self._service_and_job()
        released: list[bool] = []
        from haute._execution_context import ExecutionContext

        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            job_id="ctx-job",
            admission_release=lambda: released.append(True),
        )
        # Non-existent temp path so the start-failure handler takes the
        # "temp parquet absent" arm (branch 1236->1238).
        tmp_parquet = str(tmp_path / "never_written.parquet")

        with (
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={"name": "t", "output_dir": str(tmp_path / "outputs")},
            ),
            patch(
                "haute.routes._background_jobs.IsolatedSupervisorThread.start",
                side_effect=RuntimeError("no threads available"),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service._launch_background(
                job_id,
                "train",
                _launch_config(),
                {"iterations": 1},
                tmp_parquet,
                ram_warning=None,
                total_source_rows=None,
                execution_context=context,
            )

        assert exc_info.value.status_code == 500
        assert store.require_job(job_id)["status"] == "error"
        assert released == [True]
        assert not Path(tmp_parquet).exists()


class TestProtocolCallbackValidation:
    @staticmethod
    def _capture_training_launch(tmp_path: Path):
        from haute.routes import _train_service
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "training",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        prepared = tmp_path / "training.parquet"
        prepared.write_bytes(b"prepared")
        captured: dict[str, object] = {}
        launched = MagicMock(name="supervisor_thread")

        def capture_launch(*_args, **kwargs):
            captured.update(kwargs)
            return launched

        with (
            patch.object(service._supervisor, "launch_protocol", side_effect=capture_launch),
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={
                    "name": "quoted",
                    "output_dir": str(tmp_path / "outputs"),
                },
            ),
        ):
            result = service._launch_training_protocol(
                job_id,
                "quoted",
                _launch_config(),
                {"iterations": 1},
                str(prepared),
                None,
                None,
                execution_context=_training_execution_context(),
            )

        assert result is launched
        return store, job_id, captured

    @staticmethod
    def _capture_dispersion_launch(tmp_path: Path):
        from haute.routes import _train_service
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": "dispersion_estimate",
                "param": "theta",
                "start_time": time.monotonic(),
                "timeout": 60,
            }
        )
        prepared = tmp_path / "dispersion.parquet"
        prepared.write_bytes(b"prepared")
        captured: dict[str, object] = {}
        launched = MagicMock(name="supervisor_thread")

        def capture_launch(*_args, **kwargs):
            captured.update(kwargs)
            return launched

        with (
            patch.object(service._supervisor, "launch_protocol", side_effect=capture_launch),
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={"target": "y", "params": {}},
            ),
        ):
            result = service._launch_dispersion_protocol(
                job_id,
                "quoted",
                {
                    "target": "y",
                    "algorithm": "glm",
                    "family": "negbinomial",
                    "terms": {"x": {"type": "linear"}},
                },
                "theta",
                str(prepared),
                execution_context=_training_execution_context(),
            )

        assert result is launched
        return store, job_id, captured

    def test_training_progress_callback_rejects_malformed_events(self, tmp_path: Path) -> None:
        store, job_id, captured = self._capture_training_launch(tmp_path)
        on_progress = captured["on_progress"]
        on_finished = captured["on_finished"]
        assert callable(on_progress)
        assert callable(on_finished)

        try:
            with pytest.raises(WorkerProtocolError, match="Unknown training progress"):
                on_progress(WorkerProgressEvent(0, 0.5, "bad", "unknown", {}))
            with pytest.raises(WorkerProtocolError, match="fields are malformed"):
                on_progress(
                    WorkerProgressEvent(
                        1,
                        0.5,
                        "bad",
                        "iteration",
                        {"iteration": True, "total": 1, "metrics": {}},
                    )
                )
            store.jobs.pop(job_id)
            with pytest.raises(KeyError, match="disappeared"):
                on_progress(
                    WorkerProgressEvent(
                        2,
                        0.5,
                        "fit",
                        "iteration",
                        {"iteration": 1, "total": 2, "metrics": {"loss": 0.5}},
                    )
                )
        finally:
            on_finished()

    def test_training_tuning_progress_is_validated_and_monotonic(self, tmp_path: Path) -> None:
        store, job_id, captured = self._capture_training_launch(tmp_path)
        on_progress = captured["on_progress"]
        on_finished = captured["on_finished"]
        assert callable(on_progress)
        assert callable(on_finished)
        valid_fields = {
            "phase": "trial_fit",
            "trial_index": 1,
            "trial_count": 5,
            "fold_index": 1,
            "fold_count": 2,
            "completed_fits": 1,
            "total_fits": 11,
            "best_objective": 0.25,
        }

        try:
            on_progress(
                WorkerProgressEvent(
                    1,
                    1 / 11,
                    "Tuning: trial_fit",
                    "tuning",
                    valid_fields,
                )
            )
            job = store.require_job(job_id)
            assert {
                key: job[key]
                for key in (
                    "phase",
                    "trial_index",
                    "trial_count",
                    "fold_index",
                    "fold_count",
                    "completed_fits",
                    "total_fits",
                    "best_objective",
                )
            } == valid_fields

            with pytest.raises(WorkerProtocolError, match="fields are malformed"):
                on_progress(
                    WorkerProgressEvent(
                        2,
                        0.2,
                        "bad keys",
                        "tuning",
                        {**valid_fields, "unexpected": True},
                    )
                )
            with pytest.raises(WorkerProtocolError, match="fields are malformed"):
                on_progress(
                    WorkerProgressEvent(
                        3,
                        0.2,
                        "invalid counts",
                        "tuning",
                        {**valid_fields, "trial_count": 4},
                    )
                )
            with pytest.raises(WorkerProtocolError, match="completed_fits regressed"):
                on_progress(
                    WorkerProgressEvent(
                        4,
                        0.0,
                        "regressed",
                        "tuning",
                        {**valid_fields, "completed_fits": 0},
                    )
                )
            with pytest.raises(WorkerProtocolError, match="total_fits changed"):
                on_progress(
                    WorkerProgressEvent(
                        5,
                        2 / 13,
                        "changed total",
                        "tuning",
                        {
                            **valid_fields,
                            "trial_count": 6,
                            "completed_fits": 2,
                            "total_fits": 13,
                        },
                    )
                )

            store.jobs.pop(job_id)
            with pytest.raises(KeyError, match="disappeared during tuning"):
                on_progress(
                    WorkerProgressEvent(
                        6,
                        2 / 11,
                        "missing job",
                        "tuning",
                        {**valid_fields, "completed_fits": 2},
                    )
                )
        finally:
            on_finished()

    def test_training_completion_callback_rejects_malformed_results(self, tmp_path: Path) -> None:
        _store, job_id, captured = self._capture_training_launch(tmp_path)
        completed_fields = captured["completed_fields"]
        on_finished = captured["on_finished"]
        assert callable(completed_fields)
        assert callable(on_finished)
        model_artifact = WorkerArtifactManifest(
            kind="model",
            relative_path="output/quoted.cbm",
            size_bytes=0,
            sha256="0" * 64,
            lifetime="staged",
        )

        try:
            cases = (
                (
                    WorkerResultManifest(metadata=[]),
                    "metadata must be an object",
                ),
                (
                    WorkerResultManifest(metadata={"response": []}),
                    "response must be an object",
                ),
                (
                    WorkerResultManifest(
                        metadata={
                            "response": {
                                "status": "completed",
                                "job_id": "another-job",
                            }
                        }
                    ),
                    "status or job identifier",
                ),
                (
                    WorkerResultManifest(
                        metadata={
                            "response": {
                                "status": "completed",
                                "job_id": job_id,
                                "model_path": "output/quoted.cbm",
                            }
                        }
                    ),
                    "model path does not match",
                ),
                (
                    WorkerResultManifest(
                        metadata={
                            "response": {
                                "status": "completed",
                                "job_id": job_id,
                                "model_path": "output/quoted.cbm",
                            }
                        },
                        artifacts=(model_artifact,),
                    ),
                    "execution metrics must be an object",
                ),
            )
            for result, message in cases:
                with pytest.raises(WorkerProtocolError, match=message):
                    completed_fields(result)
        finally:
            on_finished()

    def test_dispersion_callbacks_reject_malformed_events_and_results(self, tmp_path: Path) -> None:
        _store, _job_id, captured = self._capture_dispersion_launch(tmp_path)
        on_progress = captured["on_progress"]
        completed_fields = captured["completed_fields"]
        on_finished = captured["on_finished"]
        assert callable(on_progress)
        assert callable(completed_fields)
        assert callable(on_finished)

        try:
            with pytest.raises(WorkerProtocolError, match="Unknown dispersion progress"):
                on_progress(WorkerProgressEvent(0, 0.5, "bad", "iteration", {}))

            cases = (
                (WorkerResultManifest(metadata=[]), "metadata must be an object"),
                (
                    WorkerResultManifest(metadata={"param": "var_power"}),
                    "parameter does not match",
                ),
                (
                    WorkerResultManifest(
                        metadata={
                            "param": "theta",
                            "value": True,
                            "llf": -1.0,
                            "n_fits": 1,
                            "execution_metrics": {},
                        }
                    ),
                    "metadata is malformed",
                ),
            )
            for result, message in cases:
                with pytest.raises(WorkerProtocolError, match=message):
                    completed_fields(result)
        finally:
            on_finished()


class TestProtocolLaunchCleanup:
    @staticmethod
    def _service_job_and_context(
        tmp_path: Path,
        *,
        job_type: str,
    ):
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job(
            {
                "status": "running",
                "job_type": job_type,
                "param": "theta" if job_type == "dispersion_estimate" else None,
                "start_time": time.monotonic(),
                "timeout": 1.0,
            }
        )
        prepared = tmp_path / f"{job_type}.parquet"
        prepared.write_bytes(b"prepared")
        service._supervisor.launch_protocol = MagicMock(name="launch_protocol")
        released: list[bool] = []
        context = ExecutionContext(
            operation="training_pipeline",
            profile=ExecutionProfile.TRAINING_PREP,
            admission_release=lambda: released.append(True),
        )
        return store, service, job_id, prepared, context, released

    @staticmethod
    def _launch(
        kind: str,
        service: TrainService,
        job_id: str,
        prepared: Path,
        context: ExecutionContext,
    ):
        if kind == "training":
            return service._launch_training_protocol(
                job_id,
                "quoted",
                _launch_config(),
                {"iterations": 1},
                str(prepared),
                None,
                None,
                execution_context=context,
            )
        return service._launch_dispersion_protocol(
            job_id,
            "quoted",
            {
                "target": "y",
                "algorithm": "glm",
                "family": "negbinomial",
                "terms": {"x": {"type": "linear"}},
            },
            "theta",
            str(prepared),
            execution_context=context,
        )

    @pytest.mark.parametrize(
        ("kind", "job_type"),
        [
            ("training", "training"),
            ("dispersion", "dispersion_estimate"),
        ],
    )
    def test_status_recheck_failure_cleans_transferred_resources(
        self,
        tmp_path: Path,
        kind: str,
        job_type: str,
    ) -> None:
        store, service, job_id, prepared, context, released = self._service_job_and_context(
            tmp_path, job_type=job_type
        )
        stored = store.require_job(job_id)

        with (
            patch.object(
                store,
                "require_job",
                side_effect=(stored, RuntimeError("job store unavailable")),
            ),
            pytest.raises(RuntimeError, match="job store unavailable"),
        ):
            self._launch(kind, service, job_id, prepared, context)

        assert released == [True]
        assert not prepared.exists()
        service._supervisor.launch_protocol.assert_not_called()

    @pytest.mark.parametrize(
        ("kind", "job_type"),
        [
            ("training", "training"),
            ("dispersion", "dispersion_estimate"),
        ],
    )
    def test_expired_launch_times_out_and_cleans_staging(
        self,
        tmp_path: Path,
        kind: str,
        job_type: str,
    ) -> None:
        from haute.routes import _train_service

        store, service, job_id, prepared, context, released = self._service_job_and_context(
            tmp_path, job_type=job_type
        )
        start_time = float(store.require_job(job_id)["start_time"])
        output_root = tmp_path / "outputs"
        with (
            patch.object(_train_service.time, "monotonic", return_value=start_time + 2.0),
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                return_value={
                    "name": "quoted",
                    "output_dir": str(output_root),
                },
            ),
        ):
            result = self._launch(kind, service, job_id, prepared, context)

        assert result is None
        assert store.require_job(job_id)["status"] == "timed_out"
        assert released == [True]
        assert not prepared.exists()
        assert not list(tmp_path.rglob(".haute-*-*"))
        service._supervisor.launch_protocol.assert_not_called()

    @pytest.mark.parametrize(
        ("kind", "job_type"),
        [
            ("training", "training"),
            ("dispersion", "dispersion_estimate"),
        ],
    )
    def test_request_build_failure_cleans_transferred_resources(
        self,
        tmp_path: Path,
        kind: str,
        job_type: str,
    ) -> None:
        from haute.routes import _train_service

        _store, service, job_id, prepared, context, released = self._service_job_and_context(
            tmp_path, job_type=job_type
        )
        with (
            patch.object(
                _train_service,
                "build_training_job_kwargs",
                side_effect=ValueError("invalid worker request"),
            ),
            pytest.raises(ValueError, match="invalid worker request"),
        ):
            self._launch(kind, service, job_id, prepared, context)

        assert released == [True]
        assert not prepared.exists()
        assert not list(tmp_path.rglob(".haute-*-*"))
        service._supervisor.launch_protocol.assert_not_called()

    def test_dispersion_staging_creation_failure_cleans_transferred_resources(
        self,
        tmp_path: Path,
    ) -> None:
        from haute.routes import _train_service

        _store, service, job_id, prepared, context, released = self._service_job_and_context(
            tmp_path, job_type="dispersion_estimate"
        )
        with (
            patch.object(
                _train_service.tempfile,
                "mkdtemp",
                side_effect=OSError("scratch unavailable"),
            ),
            pytest.raises(OSError, match="scratch unavailable"),
        ):
            self._launch("dispersion", service, job_id, prepared, context)

        assert released == [True]
        assert not prepared.exists()
        service._supervisor.launch_protocol.assert_not_called()
