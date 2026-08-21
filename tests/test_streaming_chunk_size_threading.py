"""Tests that ``streaming_chunk_size`` from request bodies is threaded down to
every Polars streaming sink / chunk-size context manager invoked during the
request.  When omitted from the request the
:data:`~haute._polars_utils.DEFAULT_STREAMING_CHUNK_SIZE` must apply.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from tests.conftest import make_ready_file_input_config

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sink_graph(out_path: str) -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="s",
                data=NodeData(label="s", nodeType=NodeType.DATA_INPUT),
            ),
            GraphNode(
                id="sink",
                data=NodeData(
                    label="sink",
                    nodeType=NodeType.DATA_OUTPUT,
                    config={
                        "outputType": "file",
                        "format": "parquet",
                        "mode": "sink",
                        "path": out_path,
                        "arguments": {},
                    },
                ),
            ),
        ],
        edges=[GraphEdge(id="e_s_sink", source="s", target="sink")],
    )


def _make_modelling_graph() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="train",
                data=NodeData(
                    label="train",
                    nodeType="modelling",
                    config={"target": "claim_count"},
                ),
            ),
        ],
        edges=[],
    )


def _make_optimiser_graph(data_path: str, *, mode: str = "online") -> dict:
    cfg: dict = {
        "mode": mode,
        "objective": "expected_income",
        "constraints": {"volume": {"min": 0.90}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "max_iter": 5,
        "tolerance": 1e-4,
    }
    g = PipelineGraph(
        nodes=[
            GraphNode(
                id="source",
                data=NodeData(
                    label="source",
                    nodeType=NodeType.DATA_INPUT,
                    config=make_ready_file_input_config(data_path),
                ),
            ),
            GraphNode(
                id="opt",
                data=NodeData(
                    label="opt",
                    nodeType="optimiser",
                    config=cfg,
                ),
            ),
        ],
        edges=[GraphEdge(id="e_src_opt", source="source", target="opt")],
    )
    return g.model_dump()


# ---------------------------------------------------------------------------
# 1) write_data_output → bounded_sink
# ---------------------------------------------------------------------------


class TestDataOutputExecutionThreading:
    """``write_data_output`` passes its chunk size into graph execution."""

    def test_uses_request_value(self, tmp_path):
        from haute.executor import write_data_output

        out_path = str(tmp_path / "out.parquet")
        graph = _make_sink_graph(out_path)

        captured: dict[str, object] = {}

        def fake_execute_lazy(*_args, **kwargs):
            captured.update(kwargs)
            return {"sink": lf}, ["s", "sink"], {}, {}

        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        with (
            patch(
                "haute.executor._execute_lazy",
                side_effect=fake_execute_lazy,
            ),
        ):
            write_data_output(graph, "sink", streaming_chunk_size=12345)

        assert captured["dataframe_cache_request"].streaming_chunk_size == 12345

    def test_default_when_missing(self, tmp_path):
        from haute.executor import write_data_output

        out_path = str(tmp_path / "out.parquet")
        graph = _make_sink_graph(out_path)

        captured: dict[str, object] = {}

        def fake_execute_lazy(*_args, **kwargs):
            captured.update(kwargs)
            return {"sink": lf}, ["s", "sink"], {}, {}

        lf = pl.DataFrame({"x": [1]}).lazy()
        with (
            patch(
                "haute.executor._execute_lazy",
                side_effect=fake_execute_lazy,
            ),
        ):
            write_data_output(graph, "sink")

        assert (
            captured["dataframe_cache_request"].streaming_chunk_size == DEFAULT_STREAMING_CHUNK_SIZE
        )


class TestSinkRouteThreading:
    """The ``/api/pipeline/write-output`` route must forward ``streaming_chunk_size``."""

    def test_request_value_reaches_execute_sink(self, client, tmp_path):
        from haute.routes import pipeline as pipeline_route

        response_path = str(tmp_path / "sink_route.parquet")
        graph = _make_sink_graph("sink_route.parquet").model_dump()

        captured: dict[str, object] = {}

        from haute.schemas import WriteOutputResponse

        def fake_output_transaction(
            _graph, _node_id, _source, streaming_chunk_size, *_args, **_kwargs
        ):
            captured["streaming_chunk_size"] = streaming_chunk_size
            return WriteOutputResponse(
                status="ok",
                row_count=0,
                path=response_path,
                format="parquet",
            )

        with patch.object(
            pipeline_route,
            "_output_write_transaction",
            side_effect=fake_output_transaction,
        ):
            resp = client.post(
                "/api/pipeline/write-output",
                json={"graph": graph, "node_id": "sink", "streaming_chunk_size": 9876},
            )

        assert resp.status_code == 200, resp.text
        assert captured.get("streaming_chunk_size") == 9876

    def test_omitted_passes_none_to_execute_sink(self, client, tmp_path):
        from haute.routes import pipeline as pipeline_route
        from haute.schemas import WriteOutputResponse

        response_path = str(tmp_path / "sink_route_none.parquet")
        graph = _make_sink_graph("sink_route_none.parquet").model_dump()

        captured: dict[str, object] = {}

        def fake_output_transaction(
            _graph, _node_id, _source, streaming_chunk_size, *_args, **_kwargs
        ):
            captured["streaming_chunk_size"] = streaming_chunk_size
            return WriteOutputResponse(
                status="ok",
                row_count=0,
                path=response_path,
                format="parquet",
            )

        with patch.object(
            pipeline_route,
            "_output_write_transaction",
            side_effect=fake_output_transaction,
        ):
            resp = client.post(
                "/api/pipeline/write-output",
                json={"graph": graph, "node_id": "sink"},
            )

        assert resp.status_code == 200, resp.text
        assert captured.get("streaming_chunk_size") is None

    @pytest.mark.parametrize(
        ("failure_kind", "expected_status"),
        [
            ("contract", 422),
            ("bounded", 422),
            ("destination", 409),
            ("memory", 507),
            ("unknown_envelope", 500),
            ("native_rss", 507),
            ("native_unsupported", 507),
            ("crashed_memory", 507),
            ("crashed", 500),
            ("remote_memory", 507),
            ("remote", 500),
        ],
    )
    def test_isolated_sink_failures_map_to_stable_http_contracts(
        self,
        client,
        tmp_path: Path,
        failure_kind: str,
        expected_status: int,
    ) -> None:
        from haute._worker_isolation import (
            IsolatedWorkerCrashedError,
            IsolatedWorkerMemoryLimitExceededError,
            IsolatedWorkerMemoryLimitUnsupportedError,
            IsolatedWorkerRemoteError,
        )
        from haute.routes import pipeline as pipeline_route
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        if failure_kind in {"contract", "bounded", "destination", "memory"}:
            payload = {"error_code": "test"} if failure_kind in {"contract", "memory"} else None
            failure: BaseException = pipeline_route._OutputWriteWorkerError(
                {
                    "contract": "contract",
                    "bounded": "bounded",
                    "destination": "destination_exists",
                    "memory": "memory",
                }[failure_kind],
                f"{failure_kind} failure",
                payload,
            )
        elif failure_kind == "unknown_envelope":
            failure = pipeline_route._OutputWriteWorkerError("unknown", "unknown failure")
        elif failure_kind == "native_rss":
            failure = IsolatedWorkerMemoryLimitExceededError(
                rss_bytes=200,
                rss_limit_bytes=100,
            )
        elif failure_kind == "native_unsupported":
            failure = IsolatedWorkerMemoryLimitUnsupportedError(memory_limit_bytes=100)
        elif failure_kind == "crashed_memory":
            failure = IsolatedWorkerCrashedError(exitcode=-9, memory_limit_bytes=100)
        elif failure_kind == "crashed":
            failure = IsolatedWorkerCrashedError(exitcode=1, memory_limit_bytes=100)
        elif failure_kind == "remote_memory":
            failure = IsolatedWorkerRemoteError(
                remote_type="MemoryError",
                remote_message="private memory detail",
                remote_traceback="private traceback",
            )
        else:
            failure = IsolatedWorkerRemoteError(
                remote_type="RuntimeError",
                remote_message="private child detail",
                remote_traceback="private traceback",
            )

        with patch.object(
            pipeline_route,
            "_output_write_transaction",
            side_effect=failure,
        ):
            response = client.post(
                "/api/pipeline/write-output",
                json={
                    "graph": _make_sink_graph("sink_failure.parquet").model_dump(),
                    "node_id": "sink",
                },
            )

        assert response.status_code == expected_status
        if expected_status == 500:
            expected_detail = (
                "Internal server error"
                if failure_kind == "unknown_envelope"
                else _INTERNAL_ERROR_DETAIL
            )
            assert response.json()["detail"] == expected_detail
            assert "private" not in response.text


# ---------------------------------------------------------------------------
# 2) optimiser _execute_pipeline → temporary_streaming_chunk_size
# ---------------------------------------------------------------------------


class TestOptimiserExecutePipelineChunkSize:
    """The optimiser's ``_execute_pipeline`` honours ``body.streaming_chunk_size``."""

    def _run(self, haute_scratch, *, streaming_chunk_size: int | None) -> list[int | None]:
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        data_path = haute_scratch / "data.parquet"
        pl.DataFrame(
            {
                "quote_id": ["q1"],
                "scenario_index": [0],
                "scenario_value": [1.0],
                "expected_income": [10.0],
                "volume": [1.0],
            }
        ).write_parquet(data_path)
        body_kwargs: dict = {
            "graph": _make_optimiser_graph(str(data_path)),
            "node_id": "opt",
        }
        if streaming_chunk_size is not None:
            body_kwargs["streaming_chunk_size"] = streaming_chunk_size
        body = OptimiserSolveRequest(**body_kwargs)

        svc = OptimiserSolveService(store=JobStore())
        lf = pl.DataFrame({"x": [1]}).lazy()

        captured: list[int | None] = []

        from contextlib import contextmanager

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        with (
            patch(
                "haute.routes._optimiser_service.execute_lazy_graph",
                return_value=({"opt": lf}, ["opt"], {}, {}),
            ),
            patch("haute.executor._compile_preamble", return_value={}),
            patch("haute.executor._pipeline_dir", return_value=None),
            patch("haute.executor._resolve_batch_scenario", return_value="batch"),
            patch(
                "haute.routes._optimiser_service.temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
        ):
            tmp = haute_scratch / "haute_test_chunk"
            tmp.mkdir()
            svc._execute_pipeline(body, "job-id", tmp)
        return captured

    def test_uses_request_value(self, haute_scratch):
        captured = self._run(haute_scratch, streaming_chunk_size=12345)
        assert captured == [12345]

    def test_default_when_missing(self, haute_scratch):
        captured = self._run(haute_scratch, streaming_chunk_size=None)
        assert captured == [DEFAULT_STREAMING_CHUNK_SIZE]


# ---------------------------------------------------------------------------
# 3) Train _execute_and_sink → bounded_sink
# ---------------------------------------------------------------------------


class TestTrainExecuteAndSinkChunkSize:
    """``TrainService._execute_and_sink`` forwards ``body.streaming_chunk_size``."""

    def _run(self, *, streaming_chunk_size: int | None) -> dict:
        from haute.routes._job_store import JobStore
        from haute.routes._train_service import TrainService
        from haute.schemas import TrainRequest

        store = JobStore()
        service = TrainService(store)
        graph = _make_modelling_graph().model_dump()
        body_kwargs: dict = {"graph": graph, "node_id": "train"}
        if streaming_chunk_size is not None:
            body_kwargs["streaming_chunk_size"] = streaming_chunk_size
        body = TrainRequest(**body_kwargs)
        job_id = store.create_job({"status": "running"})

        captured: dict[str, object] = {}

        def fake_bounded_sink(lf, path, **kwargs):
            captured.update(kwargs)
            pl.DataFrame({"claim_count": [1.0], "driver_age": [40]}).write_parquet(path)

        def fake_execute_lazy(*_args, **_kwargs):
            return (
                {"train": pl.DataFrame({"claim_count": [1.0], "driver_age": [40]}).lazy()},
                ["train"],
                {},
                {},
            )

        with (
            patch(
                "haute.routes._train_service.execute_lazy_graph",
                side_effect=fake_execute_lazy,
            ),
            patch("haute.executor._build_node_fn", return_value=None),
            patch("haute.modelling._algorithms._mem_checkpoint"),
            patch(
                "haute.modelling._algorithms._MEM_LOG",
                MagicMock(write_text=MagicMock()),
            ),
            patch(
                "haute._polars_utils.bounded_sink",
                side_effect=fake_bounded_sink,
            ),
        ):
            tmp_parquet = service._execute_and_sink(
                body, preamble_ns=None, row_limit=None, job_id=job_id
            )
        Path(tmp_parquet).unlink(missing_ok=True)
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert captured.get("streaming_chunk_size") == 12345

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert captured.get("streaming_chunk_size") == DEFAULT_STREAMING_CHUNK_SIZE


# ---------------------------------------------------------------------------
# 4) optimiser _persist_ratebook_factors_lazy_artifact (via _extract_factors)
# ---------------------------------------------------------------------------


class TestExtractFactorsChunkSize:
    """``_extract_factors`` forwards ``streaming_chunk_size`` to the persist helper."""

    def _build_lazy_outputs(self) -> dict:
        return {
            "banding": pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "rating_band_a": ["A"],
                }
            ).lazy()
        }

    def _config(self) -> dict:
        return {
            "mode": "ratebook",
            "banding_source": "banding",
            "quote_id": "quote_id",
            "factor_columns": [["rating_band_a"]],
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

    def _run(self, *, streaming_chunk_size: int | None) -> dict:
        from haute.routes._optimiser_service import OptimiserSolveService

        captured: dict[str, object] = {}

        def fake_persist(factors_lf, *, streaming_chunk_size):
            captured["streaming_chunk_size"] = streaming_chunk_size
            return {
                "kind": "ratebook_factors_artifact",
                "version": 1,
                "format": "parquet",
                "path": "/tmp/x.parquet",
                "directory": "/tmp",
                "row_count": 1,
                "size_bytes": 1,
                "columns": ["quote_id", "rating_band_a"],
            }

        # Patch the read_parquet_metadata used inside the persist helper —
        # not called because we replace the helper itself.
        with patch(
            "haute.routes._optimiser_service._persist_ratebook_factors_lazy_artifact",
            side_effect=fake_persist,
        ):
            OptimiserSolveService._extract_factors(
                self._build_lazy_outputs(),
                self._config(),
                "ratebook",
                streaming_chunk_size=streaming_chunk_size,
            )
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert captured.get("streaming_chunk_size") == 12345

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert captured.get("streaming_chunk_size") == DEFAULT_STREAMING_CHUNK_SIZE


class TestPersistRatebookFactorsLazyArtifact:
    """``_persist_ratebook_factors_lazy_artifact`` calls ``bounded_sink`` with the
    threaded chunk size; ``None`` falls back to ``DEFAULT_STREAMING_CHUNK_SIZE``.
    """

    def _run(self, *, streaming_chunk_size: int | None) -> dict:
        from haute.routes._optimiser_service import (
            _persist_ratebook_factors_lazy_artifact,
        )

        captured: dict[str, object] = {}

        def fake_bounded_sink(lf, path, **kwargs):
            captured.update(kwargs)
            pl.DataFrame({"x": [1]}).write_parquet(path)

        with (
            patch(
                "haute.routes._optimiser_service.bounded_sink",
                side_effect=fake_bounded_sink,
            ),
        ):
            lf = pl.DataFrame({"x": [1]}).lazy()
            _persist_ratebook_factors_lazy_artifact(lf, streaming_chunk_size=streaming_chunk_size)
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert captured.get("streaming_chunk_size") == 12345

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert captured.get("streaming_chunk_size") == DEFAULT_STREAMING_CHUNK_SIZE


# ---------------------------------------------------------------------------
# 5) optimiser _build_grid → bounded_sink
# ---------------------------------------------------------------------------


class TestBuildGridChunkSize:
    """``_build_grid`` forwards ``streaming_chunk_size`` to ``bounded_sink``."""

    def _run(self, *, streaming_chunk_size: int | None) -> dict:
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        svc = OptimiserSolveService(store=store)
        job_id = store.create_job({"status": "running"})

        captured: dict[str, object] = {}

        def fake_bounded_sink(lf, path, **kwargs):
            captured.update(kwargs)
            pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "volume": [1.0],
                }
            ).write_parquet(path)

        config = {
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
            "objective": "expected_income",
        }
        scored_lf = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "scenario_index": [0],
                "scenario_value": [1.0],
                "volume": [1.0],
            }
        ).lazy()

        with (
            patch(
                "haute.routes._optimiser_service.bounded_sink",
                side_effect=fake_bounded_sink,
            ),
            patch(
                "price_contour.build_grid_from_parquet_chunked",
                return_value=object(),
            ),
            patch(
                "haute.routes._optimiser_service._chunk_size_decision_for_parquet",
                return_value=MagicMock(chunk_size=1, provenance={}),
            ),
        ):
            svc._build_grid(
                scored_lf,
                ["volume"],
                config,
                "opt",
                job_id,
                streaming_chunk_size=streaming_chunk_size,
            )
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert captured.get("streaming_chunk_size") == 12345

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert captured.get("streaming_chunk_size") == DEFAULT_STREAMING_CHUNK_SIZE


# ---------------------------------------------------------------------------
# 6) pipeline preview/trace handlers — temporary_streaming_chunk_size
# ---------------------------------------------------------------------------


class TestPreviewHandlerThreading:
    """``/api/pipeline/preview`` must thread ``streaming_chunk_size`` into
    ``temporary_streaming_chunk_size`` in the worker that runs the executor call."""

    def _post(self, client, *, body_kwargs: dict) -> dict[str, list[int | None] | list[int]]:
        from contextlib import contextmanager

        from haute.routes import pipeline as pipeline_route
        from haute.schemas import NodeResult

        graph = _make_sink_graph("ignored.parquet").model_dump()
        node_id = "sink"

        captured: dict[str, list[int | None] | list[int]] = {
            "chunk_sizes": [],
            "ctx_enter_threads": [],
            "ctx_exit_threads": [],
            "execute_threads": [],
        }

        def fake_ctx(chunk_size):
            captured["chunk_sizes"].append(chunk_size)

            @contextmanager
            def _cm():
                captured["ctx_enter_threads"].append(threading.get_ident())
                try:
                    yield
                finally:
                    captured["ctx_exit_threads"].append(threading.get_ident())

            return _cm()

        def fake_execute_graph(*_args, **_kwargs):
            captured["execute_threads"].append(threading.get_ident())
            return {node_id: NodeResult(status="ok")}

        with (
            patch.object(
                pipeline_route,
                "temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
            patch.object(pipeline_route, "execute_graph", side_effect=fake_execute_graph),
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={"graph": graph, "node_id": node_id, **body_kwargs},
            )
        assert resp.status_code == 200, resp.text
        return captured

    def test_preview_handler_threads_chunk_size(self, client):
        captured = self._post(client, body_kwargs={"streaming_chunk_size": 12345})
        assert captured["chunk_sizes"] == [12345]
        assert captured["ctx_enter_threads"] == captured["execute_threads"]
        assert captured["ctx_exit_threads"] == captured["execute_threads"]

    def test_preview_handler_default_when_missing(self, client):
        captured = self._post(client, body_kwargs={})
        assert captured["chunk_sizes"] == [DEFAULT_STREAMING_CHUNK_SIZE]
        assert captured["ctx_enter_threads"] == captured["execute_threads"]
        assert captured["ctx_exit_threads"] == captured["execute_threads"]

    def test_preview_timeout_keeps_chunk_scope_until_worker_finishes(self, client):
        from contextlib import contextmanager

        from haute.routes import pipeline as pipeline_route
        from haute.schemas import NodeResult

        graph = _make_sink_graph("ignored.parquet").model_dump()
        node_id = "sink"
        worker_started = threading.Event()
        release_worker = threading.Event()
        ctx_exited = threading.Event()
        captured: dict[str, list[int | None] | list[int]] = {
            "chunk_sizes": [],
            "ctx_enter_threads": [],
            "ctx_exit_threads": [],
            "execute_threads": [],
        }

        def fake_ctx(chunk_size):
            captured["chunk_sizes"].append(chunk_size)

            @contextmanager
            def _cm():
                captured["ctx_enter_threads"].append(threading.get_ident())
                try:
                    yield
                finally:
                    captured["ctx_exit_threads"].append(threading.get_ident())
                    ctx_exited.set()

            return _cm()

        def fake_execute_graph(*_args, **_kwargs):
            captured["execute_threads"].append(threading.get_ident())
            worker_started.set()
            if not release_worker.wait(timeout=5.0):
                raise TimeoutError("test worker was not released")
            return {node_id: NodeResult(status="ok")}

        worker_errors: list[BaseException] = []

        async def fake_run_blocking(func, *args, **kwargs):
            def _run_worker():
                try:
                    func(*args)
                except BaseException as exc:
                    worker_errors.append(exc)

            thread = threading.Thread(target=_run_worker, daemon=True)
            thread.start()
            assert worker_started.wait(timeout=1.0)
            raise TimeoutError("preview timed out")

        with (
            patch.object(
                pipeline_route,
                "temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
            patch.object(pipeline_route, "execute_graph", side_effect=fake_execute_graph),
            patch.object(
                pipeline_route,
                "run_blocking_with_response_timeout",
                side_effect=fake_run_blocking,
            ),
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={
                    "graph": graph,
                    "node_id": node_id,
                    "streaming_chunk_size": 12345,
                },
            )
            assert resp.status_code == 504, resp.text
            assert worker_started.wait(timeout=1.0)
            assert captured["chunk_sizes"] == [12345]
            assert captured["ctx_enter_threads"] == captured["execute_threads"]
            assert not ctx_exited.is_set()
            release_worker.set()
            assert ctx_exited.wait(timeout=2.0)
            assert captured["ctx_exit_threads"] == captured["execute_threads"]
            assert not worker_errors


class TestTraceHandlerThreading:
    """``/api/pipeline/trace`` must thread ``streaming_chunk_size`` into
    ``temporary_streaming_chunk_size`` in the worker that runs the executor call."""

    def _post(self, client, *, body_kwargs: dict) -> dict[str, list[int | None] | list[int]]:
        from contextlib import contextmanager

        from haute.routes import pipeline as pipeline_route
        from haute.trace import TraceResult

        graph = _make_sink_graph("ignored.parquet").model_dump()

        captured: dict[str, list[int | None] | list[int]] = {
            "chunk_sizes": [],
            "ctx_enter_threads": [],
            "ctx_exit_threads": [],
            "execute_threads": [],
        }

        def fake_ctx(chunk_size):
            captured["chunk_sizes"].append(chunk_size)

            @contextmanager
            def _cm():
                captured["ctx_enter_threads"].append(threading.get_ident())
                try:
                    yield
                finally:
                    captured["ctx_exit_threads"].append(threading.get_ident())

            return _cm()

        def fake_execute_trace(*_args, **_kwargs):
            captured["execute_threads"].append(threading.get_ident())
            return TraceResult(
                target_node_id="sink",
                row_index=0,
                column=None,
                output_value=None,
                steps=[],
                row_id_column=None,
                row_id_value=None,
                total_nodes_in_pipeline=0,
                nodes_in_trace=0,
                execution_ms=0.0,
                waterfall=None,
            )

        with (
            patch.object(
                pipeline_route,
                "temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
            patch.object(pipeline_route, "execute_trace", side_effect=fake_execute_trace),
        ):
            resp = client.post(
                "/api/pipeline/trace",
                json={"graph": graph, **body_kwargs},
            )
        assert resp.status_code == 200, resp.text
        return captured

    def test_trace_handler_threads_chunk_size(self, client):
        captured = self._post(client, body_kwargs={"streaming_chunk_size": 12345})
        assert captured["chunk_sizes"] == [12345]
        assert captured["ctx_enter_threads"] == captured["execute_threads"]
        assert captured["ctx_exit_threads"] == captured["execute_threads"]

    def test_trace_handler_default_when_missing(self, client):
        captured = self._post(client, body_kwargs={})
        assert captured["chunk_sizes"] == [DEFAULT_STREAMING_CHUNK_SIZE]
        assert captured["ctx_enter_threads"] == captured["execute_threads"]
        assert captured["ctx_exit_threads"] == captured["execute_threads"]

    def test_trace_timeout_keeps_chunk_scope_until_worker_finishes(self, client):
        from contextlib import contextmanager

        from haute.routes import pipeline as pipeline_route
        from haute.trace import TraceResult

        graph = _make_sink_graph("ignored.parquet").model_dump()
        worker_started = threading.Event()
        release_worker = threading.Event()
        ctx_exited = threading.Event()
        captured: dict[str, list[int | None] | list[int]] = {
            "chunk_sizes": [],
            "ctx_enter_threads": [],
            "ctx_exit_threads": [],
            "execute_threads": [],
        }

        def fake_ctx(chunk_size):
            captured["chunk_sizes"].append(chunk_size)

            @contextmanager
            def _cm():
                captured["ctx_enter_threads"].append(threading.get_ident())
                try:
                    yield
                finally:
                    captured["ctx_exit_threads"].append(threading.get_ident())
                    ctx_exited.set()

            return _cm()

        def fake_execute_trace(*_args, **_kwargs):
            captured["execute_threads"].append(threading.get_ident())
            worker_started.set()
            if not release_worker.wait(timeout=5.0):
                raise TimeoutError("test worker was not released")
            return TraceResult(
                target_node_id="sink",
                row_index=0,
                column=None,
                output_value=None,
                steps=[],
                row_id_column=None,
                row_id_value=None,
                total_nodes_in_pipeline=0,
                nodes_in_trace=0,
                execution_ms=0.0,
                waterfall=None,
            )

        worker_errors: list[BaseException] = []

        async def fake_run_blocking(func, *args, **kwargs):
            def _run_worker():
                try:
                    func(*args)
                except BaseException as exc:
                    worker_errors.append(exc)

            thread = threading.Thread(target=_run_worker, daemon=True)
            thread.start()
            assert worker_started.wait(timeout=1.0)
            raise TimeoutError("trace timed out")

        with (
            patch.object(
                pipeline_route,
                "temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
            patch.object(pipeline_route, "execute_trace", side_effect=fake_execute_trace),
            patch.object(
                pipeline_route,
                "run_blocking_with_response_timeout",
                side_effect=fake_run_blocking,
            ),
        ):
            resp = client.post(
                "/api/pipeline/trace",
                json={"graph": graph, "streaming_chunk_size": 12345},
            )
            assert resp.status_code == 504, resp.text
            assert worker_started.wait(timeout=1.0)
            assert captured["chunk_sizes"] == [12345]
            assert captured["ctx_enter_threads"] == captured["execute_threads"]
            assert not ctx_exited.is_set()
            release_worker.set()
            assert ctx_exited.wait(timeout=2.0)
            assert captured["ctx_exit_threads"] == captured["execute_threads"]
            assert not worker_errors


# ---------------------------------------------------------------------------
# 7) Gap A — _validate_and_project qid null-count streaming_collect
# ---------------------------------------------------------------------------


def _validate_and_project_data_path(tmp_path) -> str:
    from haute._sandbox import set_project_root

    set_project_root(tmp_path)
    data_path = tmp_path / "data.parquet"
    pl.DataFrame(
        {
            "quote_id": ["q1"],
            "scenario_index": [0],
            "scenario_value": [1.0],
            "expected_income": [10.0],
            "volume": [1.0],
        }
    ).write_parquet(data_path)
    return str(data_path)


class TestValidateAndProjectChunkSize:
    """``_validate_and_project`` honours the request's ``streaming_chunk_size``
    while running the qid-null-count ``streaming_collect``."""

    def _run(self, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        svc = OptimiserSolveService(store=JobStore())
        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }
        source_lf = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "scenario_index": [0],
                "scenario_value": [1.0],
                "expected_income": [10.0],
                "volume": [1.0],
            }
        ).lazy()

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        with patch(
            "haute.routes._optimiser_service.temporary_streaming_chunk_size",
            side_effect=fake_ctx,
        ):
            svc._validate_and_project(
                source_lf,
                config,
                "job-id",
                streaming_chunk_size=streaming_chunk_size,
            )
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 8) Gap B — _optimiser_input_metrics streaming_collect (estimate endpoint)
# ---------------------------------------------------------------------------


class TestOptimiserInputMetricsChunkSize:
    """``_optimiser_input_metrics`` honours ``body.streaming_chunk_size`` while
    running the final ``streaming_collect`` aggregation."""

    def _run(self, tmp_path, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager

        from haute.routes import optimiser as optimiser_route
        from haute.schemas import OptimiserEstimateRequest

        data_path = _validate_and_project_data_path(tmp_path)
        body_kwargs: dict = {
            "graph": _make_optimiser_graph(data_path),
            "node_id": "opt",
        }
        if streaming_chunk_size is not None:
            body_kwargs["streaming_chunk_size"] = streaming_chunk_size
        body = OptimiserEstimateRequest(**body_kwargs)

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        with patch.object(optimiser_route, "temporary_streaming_chunk_size", side_effect=fake_ctx):
            optimiser_route._optimiser_input_metrics(body)
        return captured

    def test_uses_request_value(self, tmp_path):
        captured = self._run(tmp_path, streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self, tmp_path):
        captured = self._run(tmp_path, streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 9) Gap C — _ScenarioFrontierRangeAccumulator.finish bucket totals
# ---------------------------------------------------------------------------


class TestScenarioFrontierRangeAccumulatorFinishChunkSize:
    """``_ScenarioFrontierRangeAccumulator.finish`` wraps the bucket
    ``streaming_collect`` calls in ``temporary_streaming_chunk_size``."""

    def _run(self, haute_scratch, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager

        from haute.routes._optimiser_service import (
            _ScenarioFrontierRangeAccumulator,
        )

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        parts_dir = haute_scratch / "acc_parts"
        parts_dir.mkdir()
        acc = _ScenarioFrontierRangeAccumulator(
            quote_id_col="quote_id",
            constraint_cols=["volume"],
            partition_count=2,
            parts_root=parts_dir,
        )
        batch = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "volume": [1.0, 2.0],
            }
        )
        acc.add_batch(batch, batch_index=0)
        with patch(
            "haute.routes._optimiser_service.temporary_streaming_chunk_size",
            side_effect=fake_ctx,
        ):
            acc.finish(streaming_chunk_size=streaming_chunk_size)
        return captured

    def test_uses_request_value(self, haute_scratch):
        captured = self._run(haute_scratch, streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self, haute_scratch):
        captured = self._run(haute_scratch, streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 10) Gap D — streaming auto-range per-chunk streaming_collect
# ---------------------------------------------------------------------------


class TestStreamingAutoRangePerChunkChunkSize:
    """``_run_streaming_frontier_auto_range_job`` wraps its per-chunk
    ``streaming_collect`` in ``temporary_streaming_chunk_size``."""

    def _run(self, tmp_path, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserFrontierAutoRangeRequest

        data_path = _validate_and_project_data_path(tmp_path)
        body_kwargs: dict = {
            "graph": _make_optimiser_graph(data_path),
            "node_id": "opt",
        }
        if streaming_chunk_size is not None:
            body_kwargs["streaming_chunk_size"] = streaming_chunk_size
        body = OptimiserFrontierAutoRangeRequest(**body_kwargs)

        store = JobStore()
        svc = OptimiserSolveService(store=store)
        job_id = store.create_job({"status": "running", "job_type": "frontier_auto_range"})

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        # Build a fake chunk that the iter_chunked_frames mock yields
        from haute.chunking import ChunkBatch

        chunk_batch = ChunkBatch(
            index=0,
            frame=pl.DataFrame(
                {
                    "quote_id": ["q1"],
                    "scenario_index": [0],
                    "scenario_value": [1.0],
                    "expected_income": [10.0],
                    "volume": [1.0],
                }
            ),
            source_rows=1,
            output_rows=1,
            checkpoint_path=None,
        )

        # Construct a minimal streaming_plan stub
        fake_plan = MagicMock()
        fake_plan.base_node_id = "source"
        fake_plan.scenario_node_id = "opt"
        fake_plan.base_required_columns = None
        fake_plan.chunk_plan = MagicMock()

        config = {
            "objective": "expected_income",
            "mode": "online",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with (
            patch.object(
                svc,
                "_execute_pipeline",
                return_value={
                    "source": pl.DataFrame(
                        {
                            "quote_id": ["q1"],
                            "scenario_index": [0],
                            "scenario_value": [1.0],
                            "expected_income": [10.0],
                            "volume": [1.0],
                        }
                    ).lazy()
                },
            ),
            patch(
                "haute.chunking.iter_chunked_frames",
                return_value=iter([chunk_batch]),
            ),
            patch("haute.chunking.ChunkRunnerRequest", MagicMock()),
            patch("haute.executor._compile_preamble", return_value={}),
            patch("haute.executor._pipeline_dir", return_value=None),
            patch(
                "haute.routes._optimiser_service.temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
        ):
            svc._run_streaming_frontier_auto_range_job(
                body,
                job_id,
                config=config,
                chunk_size=1000,
                partition_count=2,
                streaming_plan=fake_plan,
            )
        return captured

    def test_uses_request_value(self, tmp_path):
        captured = self._run(tmp_path, streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self, tmp_path):
        captured = self._run(tmp_path, streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 11) Gap E — _estimate_scenario_frontier_ranges bounded_collect_batches
# ---------------------------------------------------------------------------


class TestEstimateScenarioFrontierRangesChunkSize:
    """``_estimate_scenario_frontier_ranges`` wraps the ``bounded_collect_batches``
    pipeline in ``temporary_streaming_chunk_size``."""

    def _run(self, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager

        from haute.routes._optimiser_service import (
            FrontierAutoRangeContext,
            _estimate_scenario_frontier_ranges,
        )

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        lf = pl.DataFrame(
            {
                "quote_id": ["q1", "q2"],
                "volume": [1.0, 2.0],
            }
        ).lazy()
        with patch(
            "haute.routes._optimiser_service.temporary_streaming_chunk_size",
            side_effect=fake_ctx,
        ):
            _estimate_scenario_frontier_ranges(
                FrontierAutoRangeContext(
                    chunk_size=10,
                    partition_count=2,
                    streaming_chunk_size=streaming_chunk_size,
                ),
                scored_lf=lf,
                quote_id_col="quote_id",
                constraint_cols=["volume"],
            )
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 12) Gap F — _ratebook_factor_level_counts uses streaming_collect
#     (called from _materialise_ratebook_frontier_point)
# ---------------------------------------------------------------------------


class TestRatebookFactorLevelCountsChunkSize:
    """``_ratebook_factor_level_counts`` honours the threaded chunk size when
    triggered via ``_materialise_ratebook_frontier_point`` artifact lookup."""

    def _run(self, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager

        from haute.routes._optimiser_service import (
            _ratebook_factor_level_counts_from_artifact,
        )

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        factors_lf = pl.DataFrame(
            {
                "quote_id": ["q1"],
                "rating_band_a": ["A"],
            }
        ).lazy()
        with (
            patch(
                "haute.routes._optimiser_service._scan_ratebook_factors_artifact",
                return_value=factors_lf,
            ),
            patch(
                "haute.routes._optimiser_service.temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
        ):
            _ratebook_factor_level_counts_from_artifact(
                {"path": "/tmp/x.parquet", "columns": ["quote_id", "rating_band_a"]},
                [["rating_band_a"]],
                streaming_chunk_size=streaming_chunk_size,
            )
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 13) Gap G — _launch_background / _solve_background wraps the solve worker
# ---------------------------------------------------------------------------


class TestLaunchBackgroundSolveChunkSize:
    """``_launch_background`` wraps the entire solve worker (``_solve_online``/
    ``_solve_ratebook``) in ``temporary_streaming_chunk_size``."""

    def _run(self, *, streaming_chunk_size: int | None) -> list[int | None]:
        from contextlib import contextmanager

        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService

        store = JobStore()
        svc = OptimiserSolveService(store=store)
        job_id = store.create_job({"status": "running", "job_type": "solve", "start_time": 0.0})

        captured: list[int | None] = []

        def fake_ctx(chunk_size):
            captured.append(chunk_size)

            @contextmanager
            def _cm():
                yield

            return _cm()

        events = threading.Event()

        def fake_solve_online(*_args, **_kwargs):
            events.set()

        config = {
            "objective": "expected_income",
            "constraints": {"volume": {"min": 0.9}},
            "quote_id": "quote_id",
            "scenario_index": "scenario_index",
            "scenario_value": "scenario_value",
        }

        with (
            patch(
                "haute.routes._optimiser_service.temporary_streaming_chunk_size",
                side_effect=fake_ctx,
            ),
            patch(
                "haute.routes._optimiser_service._solve_online",
                side_effect=fake_solve_online,
            ),
        ):
            from haute.routes._optimiser_service import SolveContext

            svc._launch_background(
                SolveContext(
                    job_id=job_id,
                    node_id="opt",
                    mode="online",
                    streaming_chunk_size=streaming_chunk_size,
                ),
                config=config,
                quote_grid=MagicMock(),
                ratebook_factors_handle=None,
            )
            events.wait(timeout=5.0)
        return captured

    def test_uses_request_value(self):
        captured = self._run(streaming_chunk_size=12345)
        assert 12345 in captured

    def test_default_when_missing(self):
        captured = self._run(streaming_chunk_size=None)
        assert DEFAULT_STREAMING_CHUNK_SIZE in captured


# ---------------------------------------------------------------------------
# 14) Integration test — every chunk-size value passed to
#     ``pl.Config.set_streaming_chunk_size`` during a full solve start equals
#     the request body's ``streaming_chunk_size``.
# ---------------------------------------------------------------------------


class TestSolveStartIntegrationChunkSize:
    """End-to-end: every ``pl.Config.set_streaming_chunk_size`` call during the
    solve setup + solve worker uses the request body's chunk size."""

    def test_every_recorded_value_matches_request(self, tmp_path):
        from haute.routes._job_store import JobStore
        from haute.routes._optimiser_service import OptimiserSolveService
        from haute.schemas import OptimiserSolveRequest

        data_path = _validate_and_project_data_path(tmp_path)
        body = OptimiserSolveRequest(
            graph=_make_optimiser_graph(data_path),
            node_id="opt",
            streaming_chunk_size=12345,
        )

        svc = OptimiserSolveService(store=JobStore())

        recorded: list[int] = []
        original_set = pl.Config.set_streaming_chunk_size

        def record_set(value):
            recorded.append(int(value))
            return original_set(value)

        solver_started = threading.Event()

        def fake_solve_online(*_args, **_kwargs):
            solver_started.set()

        def fake_solve_ratebook(*_args, **_kwargs):
            solver_started.set()

        from unittest.mock import MagicMock as _MagicMock

        with (
            patch.object(pl.Config, "set_streaming_chunk_size", side_effect=record_set),
            patch(
                "haute.routes._optimiser_service._solve_online",
                side_effect=fake_solve_online,
            ),
            patch(
                "haute.routes._optimiser_service._solve_ratebook",
                side_effect=fake_solve_ratebook,
            ),
            patch.object(svc, "_build_grid", return_value=_MagicMock()),
        ):
            svc.start(body)
            solver_started.wait(timeout=10.0)

        assert recorded, "expected at least one pl.Config.set_streaming_chunk_size call"
        assert all(v == 12345 for v in recorded), (
            f"All recorded chunk sizes must be 12345; got {recorded}"
        )


# ---------------------------------------------------------------------------
# 15) Behavioural — bounded_sink mutates the Polars-global chunk size during
#     the sink (not just our wrapper bookkeeping).
# ---------------------------------------------------------------------------


class TestBoundedSinkAppliesPolarsChunkSize:
    """``bounded_sink`` must actually call ``pl.Config.set_streaming_chunk_size``
    with the requested value, not just store it in our wrapper context."""

    def test_polars_chunk_size_is_set_during_sink(self, tmp_path: Path) -> None:
        from haute._polars_utils import bounded_sink

        lf = pl.DataFrame({"x": list(range(30_000))}).lazy()
        target = tmp_path / "out.parquet"

        recorded: list[int] = []
        original_set = pl.Config.set_streaming_chunk_size

        def record_set(value):
            recorded.append(int(value))
            return original_set(value)

        with patch.object(pl.Config, "set_streaming_chunk_size", side_effect=record_set):
            bounded_sink(lf, target, streaming_chunk_size=10_000)

        assert target.exists(), "bounded_sink must produce the output parquet"
        assert 10_000 in recorded, (
            f"expected pl.Config.set_streaming_chunk_size(10_000) to be called; got {recorded}"
        )
