"""Coverage tests for TrainService error/cleanup branches.

These exercise the extracted engine arms that the route-level tests in
``test_modelling_routes.py`` don't reach directly: the RAM-estimate and
VRAM-estimate failure handlers, the missing-node ``ValueError`` arm in
``_execute_and_sink``, the column-projection path, and the GLM-config /
keep-columns merge inside ``start``.  An untested failure path can leave a
training job stuck in the wrong state, so these assert on job-store state too.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from haute.routes._train_service import TrainService
from tests.conftest import make_edge, make_graph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_only_request(path: str = "x.parquet"):
    """Build a TrainRequest whose graph is a single dataSource node."""
    from haute.schemas import TrainRequest

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "n",
                    "data": {
                        "label": "n",
                        "nodeType": "dataSource",
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
    def test_estimate_failure_falls_back_to_no_row_limit(self):
        """When estimate_safe_training_rows raises, row_limit defaults to None."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        graph = make_graph({"nodes": [], "edges": []})

        with patch(
            "haute._ram_estimate.estimate_safe_training_rows",
            side_effect=RuntimeError("probe blew up"),
        ):
            ram_warning, row_limit, total_rows, probe_cols = service._estimate_ram(
                graph,
                "n",
                preamble_ns=None,
                job_id=job_id,
            )

        assert ram_warning is None
        assert row_limit is None
        assert total_rows is None
        assert probe_cols == 0
        # The job must not have been knocked out of running by the swallowed error.
        assert store.require_job(job_id)["status"] == "running"


# ---------------------------------------------------------------------------
# _check_gpu_fallback failure arm (lines 449-450)
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
            result = service._check_gpu_fallback(
                train_params,
                row_limit=100,
                total_source_rows=200,
                probe_columns=5,
                ram_warning="prior warning",
                job_id=job_id,
            )

        # Exception swallowed: original warning returned, task_type left on GPU.
        assert result == "prior warning"
        assert train_params["task_type"] == "GPU"
        assert store.require_job(job_id)["status"] == "running"

    def test_non_gpu_task_returns_early(self):
        """Non-GPU task_type short-circuits without any VRAM probe."""
        from haute.routes._job_store import JobStore

        store = JobStore()
        service = TrainService(store)
        job_id = store.create_job({"status": "running"})

        train_params: dict[str, object] = {"task_type": "CPU"}
        with patch("haute.routes._train_service._check_gpu_vram") as mock_vram:
            result = service._check_gpu_fallback(
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
            patch("haute._execute_lazy._execute_lazy", side_effect=lazy_without_target),
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

        def fake_safe_sink(frame, path):
            sunk_frames.append(frame)

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch("haute._execute_lazy._execute_lazy", side_effect=lazy_returns_target),
            patch("haute._polars_utils.safe_sink", side_effect=fake_safe_sink),
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

        def fake_safe_sink(frame, path):
            sunk_frames.append(frame)

        def lazy_returns_target(*args, **kwargs):
            return ({"n": lf}, [], {}, {})

        p1, p2, p3, p4, p5 = _patch_execute_env()
        with (
            patch("haute._execute_lazy._execute_lazy", side_effect=lazy_returns_target),
            patch("haute._polars_utils.safe_sink", side_effect=fake_safe_sink),
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


class TestStartGlmMergeAndKeepColumns:
    def _glm_graph(self):
        config = {
            "target": "loss",
            "algorithm": "glm",
            "family": "poisson",
            "link": "log",
            "weight": "exposure",
            "offset": "log_exp",
            "exclude": ["junk"],
            "params": {"iterations": 3},
        }
        graph = make_graph(
            {
                "nodes": [
                    {
                        "id": "source",
                        "data": {
                            "label": "source",
                            "nodeType": "dataSource",
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

        def fake_execute_and_sink(_body, _preamble, _row_limit, _job_id, *, exclude, keep_columns):
            captured["exclude"] = exclude
            captured["keep_columns"] = keep_columns
            return "/tmp/fake_train.parquet"

        def fake_launch(job_id, node_id, config, train_params, *args):
            captured["train_params"] = train_params
            captured["config"] = config

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(
                service,
                "_estimate_ram",
                return_value=(None, None, 100, 3),
            ),
            patch.object(service, "_check_gpu_fallback", return_value=None),
            patch.object(service, "_execute_and_sink", side_effect=fake_execute_and_sink),
            patch.object(service, "_launch_background", side_effect=fake_launch),
        ):
            resp = service.start(body)

        assert resp.status == "started"
        # GLM top-level config keys merged into train_params (line 272-273).
        tp = captured["train_params"]
        assert tp["family"] == "poisson"
        assert tp["link"] == "log"
        # Protected columns: target + weight (289) + offset (291).
        keep = captured["keep_columns"]
        assert keep == ["loss", "exposure", "log_exp"]
        # The excluded column is forwarded as the exclude list.
        assert captured["exclude"] == ["junk"]

    def test_existing_train_param_not_overwritten_by_config_key(self):
        """A key already in params is NOT clobbered by the top-level config value."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        graph = self._glm_graph()
        # Put `family` inside params too — it must win over the top-level key.
        for node in graph.nodes:
            if node.id == "train":
                node.data.config["params"] = {"family": "gamma", "iterations": 3}
        body = TrainRequest(graph=graph, node_id="train")

        captured: dict[str, object] = {}

        def fake_launch(job_id, node_id, config, train_params, *args):
            captured["train_params"] = train_params

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch.object(service, "_check_gpu_fallback", return_value=None),
            patch.object(service, "_execute_and_sink", return_value="/tmp/fake.parquet"),
            patch.object(service, "_launch_background", side_effect=fake_launch),
        ):
            service.start(body)

        # params value is preserved; the branch at 272 evaluates k-in-train_params.
        assert captured["train_params"]["family"] == "gamma"

    def test_failure_during_execute_marks_job_error_and_reraises(self):
        """An exception between job creation and launch must flip the job to error."""
        from haute.routes._job_store import JobStore
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        body = TrainRequest(graph=self._glm_graph(), node_id="train")

        with (
            patch.object(service, "_compile_preamble", return_value=None),
            patch.object(service, "_estimate_ram", return_value=(None, None, 100, 3)),
            patch.object(service, "_check_gpu_fallback", return_value=None),
            patch.object(
                service,
                "_execute_and_sink",
                side_effect=RuntimeError("sink failed"),
            ),
            pytest.raises(RuntimeError, match="sink failed"),
        ):
            service.start(body)

        # The single created job should be in error state, not left running.
        running = [j for j in store.jobs.values() if j["status"] == "running"]
        assert running == []
        errored = [j for j in store.jobs.values() if j["status"] == "error"]
        assert errored
        assert "sink failed" in errored[0]["error"]
