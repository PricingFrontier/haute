"""Node-level tests for the dataInput / dataOutput node types.

Covers the executor path (write_data_output dispatch, preview empty-source
behaviour), the codegen → parse round trip through the config sidecar, and
sidecar persistence key filtering — the change-class obligations for a new
node type (schema-mapping round-trips; struct capability end to end).
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import haute.executor as executor_module
from haute._builders import NodeBuildContext
from haute._config_io import (
    NODE_TYPE_TO_FOLDER,
    config_path_for_node,
    load_node_config,
)
from haute._execution_context import ExecutionProfile
from haute._polars_io_registry import PolarsIoConfigError
from haute._registry import NODE_REGISTRY, ensure_registry_ready
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import SchemaMismatchError
from haute.executor import (
    DataOutputDestinationExistsError,
    DataOutputDurabilityError,
    DataOutputPublicationError,
    resolve_data_output_path,
    write_data_output,
)
from tests.conftest import build_test_input_snapshot

ensure_registry_ready()


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _data_input_node(nid: str, config: dict) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=NodeType.DATA_INPUT, config=config))


def _ready_data_input_node(nid: str, config: dict) -> GraphNode:
    """Create a source node after explicitly publishing its test snapshot."""
    build_test_input_snapshot(config)
    return _data_input_node(nid, config)


def _data_output_node(nid: str, config: dict) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=NodeType.DATA_OUTPUT, config=config))


@pytest.fixture
def struct_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["a", "b", None],
            "tags": [["x", "y"], [], ["z"]],
            "nested": [{"k": 1}, {"k": 2}, {"k": 3}],
        }
    )


class TestExecuteSinkDataOutput:
    """write_data_output dispatches dataOutput nodes through the format registry."""

    def test_data_input_to_data_output_ndjson_round_trip(self, haute_scratch, struct_frame) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "out" / "result.jsonl"

        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "ndjson",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )
        result = write_data_output(graph, "dout")

        assert result.status == "ok"
        assert result.row_count == 3  # streaming sink → re-scanned via scan_ndjson
        assert result.format == "ndjson"
        read_back = pl.read_ndjson(out_path)
        # NDJSON carries lists and structs natively; full-frame equality holds
        # for this fixture (the research's byte-identity leg is ported in the
        # round-trip suite; this asserts the node-level path preserves values).
        assert_frame_equal(read_back, struct_frame)

    def test_eager_write_format_reports_row_count(self, haute_scratch, struct_frame) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "out.json"

        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "json",
                        "path": str(out_path),
                        "mode": "write",
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )
        result = write_data_output(graph, "dout")

        assert result.row_count == 3  # eager write → df.height, no re-scan
        parsed = json.loads(out_path.read_text(encoding="utf-8"))
        assert len(parsed) == 3

    def test_missing_output_path_raises(self, haute_scratch, struct_frame) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {"outputType": "file", "format": "parquet"},
                ),
            ],
            edges=[_edge("din", "dout")],
        )
        with pytest.raises(ValueError, match="requires a non-empty 'path'"):
            write_data_output(graph, "dout")

    def test_unknown_format_fails_loudly(self, haute_scratch, struct_frame) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {"outputType": "file", "format": "sas", "path": "x"},
                ),
            ],
            edges=[_edge("din", "dout")],
        )
        with pytest.raises(PolarsIoConfigError, match="Supported formats"):
            write_data_output(graph, "dout")

    def test_non_output_node_is_rejected_before_execution(self) -> None:
        graph = PipelineGraph(
            nodes=[_data_input_node("din", {})],
            edges=[],
        )

        with pytest.raises(ValueError, match="is not a Data Output"):
            write_data_output(graph, "din")

    @pytest.mark.parametrize(
        ("selected_columns", "message"),
        [
            ("id", "must be a list"),
            (["id", ""], "must contain non-empty string names"),
        ],
    )
    def test_selected_columns_shape_is_validated_before_execution(
        self,
        haute_scratch: Path,
        selected_columns: object,
        message: str,
    ) -> None:
        graph = PipelineGraph(
            nodes=[
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(haute_scratch / "out.parquet"),
                        "arguments": {},
                        "selected_columns": selected_columns,
                    },
                )
            ],
            edges=[],
        )

        with pytest.raises(ValueError, match=message):
            write_data_output(graph, "dout")

    def test_failed_file_write_preserves_existing_target(
        self,
        haute_scratch,
        monkeypatch: pytest.MonkeyPatch,
        struct_frame,
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "result.parquet"
        original = b"existing-complete-output"
        out_path.write_bytes(original)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        def fail_after_partial_write(_frame, _config, *, resolved_path, **_kwargs):
            resolved_path.write_bytes(b"partial")
            raise RuntimeError("writer failed")

        monkeypatch.setattr(
            "haute._polars_io_registry.write_polars_output",
            fail_after_partial_write,
        )
        with pytest.raises(RuntimeError, match="writer failed"):
            write_data_output(graph, "dout", overwrite=True)

        assert out_path.read_bytes() == original
        assert list(haute_scratch.glob(".*.haute-stage-*")) == []

    def test_failed_streaming_row_count_preserves_existing_target(
        self,
        haute_scratch,
        monkeypatch: pytest.MonkeyPatch,
        struct_frame,
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "result.jsonl"
        original = b"existing-complete-output"
        out_path.write_bytes(original)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "ndjson",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        def fail_row_count(_path):
            raise RuntimeError("row count failed")

        monkeypatch.setattr(pl, "scan_ndjson", fail_row_count)
        with pytest.raises(RuntimeError, match="row count failed"):
            write_data_output(graph, "dout", overwrite=True)

        assert out_path.read_bytes() == original
        assert list(haute_scratch.glob(".*.haute-stage-*")) == []

    def test_containment_is_rechecked_before_atomic_publish(
        self,
        haute_scratch,
        monkeypatch: pytest.MonkeyPatch,
        struct_frame,
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "result.parquet"
        original = b"existing-complete-output"
        out_path.write_bytes(original)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )
        original_validator = executor_module._validate_output_publish_paths
        validation_count = 0

        def reject_publish_after_write(final_path, staging_path, *, project_root):
            nonlocal validation_count
            validation_count += 1
            if validation_count == 2:
                raise ValueError("Data output path resolves outside the project root")
            return original_validator(final_path, staging_path, project_root=project_root)

        monkeypatch.setattr(
            executor_module,
            "_validate_output_publish_paths",
            reject_publish_after_write,
        )

        with pytest.raises(ValueError, match="outside the project root"):
            write_data_output(
                graph,
                "dout",
                project_root=haute_scratch,
                overwrite=True,
            )

        assert validation_count == 2
        assert out_path.read_bytes() == original
        assert list(haute_scratch.glob(".*.haute-stage-*")) == []

    def test_existing_destination_is_rejected_before_graph_execution(
        self,
        haute_scratch: Path,
        monkeypatch: pytest.MonkeyPatch,
        struct_frame: pl.DataFrame,
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "result.parquet"
        original = b"existing-complete-output"
        out_path.write_bytes(original)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        def unexpected_execution(*_args, **_kwargs):
            raise AssertionError("graph execution started before collision preflight")

        monkeypatch.setattr(executor_module, "_execute_lazy", unexpected_execution)

        with pytest.raises(DataOutputDestinationExistsError) as exc:
            write_data_output(graph, "dout")

        assert exc.value.display_path == str(out_path)
        assert out_path.read_bytes() == original

    def test_overwrite_true_replaces_existing_destination(
        self, haute_scratch: Path, struct_frame: pl.DataFrame
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        struct_frame.write_parquet(src_path)
        out_path = haute_scratch / "result.parquet"
        out_path.write_bytes(b"old")
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        result = write_data_output(graph, "dout", overwrite=True)

        assert result.row_count == 3
        assert_frame_equal(pl.read_parquet(out_path), struct_frame)

    def test_no_overwrite_publication_race_preserves_competing_file(
        self,
        haute_scratch: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(src_path)
        out_path = haute_scratch / "result.parquet"
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        def write_then_race(_frame, _config, *, resolved_path, **_kwargs):
            pl.DataFrame({"x": [1]}).write_parquet(resolved_path)
            out_path.write_bytes(b"competing-publisher")
            return 1

        monkeypatch.setattr(
            "haute._polars_io_registry.write_polars_output",
            write_then_race,
        )

        with pytest.raises(DataOutputDestinationExistsError):
            write_data_output(graph, "dout")

        assert out_path.read_bytes() == b"competing-publisher"
        assert list(haute_scratch.glob(".*.haute-stage-*")) == []

    def test_windows_create_only_publication_uses_rename_not_link(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stage = tmp_path / "stage.parquet"
        target = tmp_path / "result.parquet"
        stage.write_bytes(b"output")
        calls: list[tuple[Path, Path]] = []
        cleanup_calls: list[Path] = []
        original_rename = executor_module.os.rename

        def record_rename(source: Path, destination: Path) -> None:
            calls.append((source, destination))
            original_rename(source, destination)

        def unexpected_link(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("Windows publication must not use hard links")

        def record_cleanup(path: Path, *, project_root: str | Path | None) -> None:
            cleanup_calls.append(path)

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(executor_module.os, "rename", record_rename)
        monkeypatch.setattr(executor_module.os, "link", unexpected_link)
        monkeypatch.setattr(executor_module, "_cleanup_output_staging_path", record_cleanup)

        executor_module._publish_output_create_only(
            stage,
            target,
            "result.parquet",
            project_root=tmp_path,
        )

        assert calls == [(stage, target)]
        assert cleanup_calls == []
        assert target.read_bytes() == b"output"

    def test_non_windows_unsupported_link_raises_safe_publication_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stage = tmp_path / "stage.parquet"
        target = tmp_path / "result.parquet"
        stage.write_bytes(b"output")

        def unsupported_link(*_args: object, **_kwargs: object) -> None:
            raise OSError("hard links are unsupported on this filesystem")

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", False)
        monkeypatch.setattr(executor_module.os, "link", unsupported_link)

        with pytest.raises(DataOutputPublicationError) as exc:
            executor_module._publish_output_create_only(
                stage,
                target,
                "result.parquet",
                project_root=tmp_path,
            )

        assert "result.parquet" in str(exc.value)
        assert "hard links are unsupported" not in str(exc.value)
        assert isinstance(exc.value.__cause__, OSError)

    def test_non_windows_stage_unlink_failure_does_not_turn_publication_into_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stage = tmp_path / "stage.parquet"
        target = tmp_path / "result.parquet"
        stage.write_bytes(b"output")
        original_unlink = Path.unlink

        def blocked_stage_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == stage:
                raise PermissionError("temporarily locked")
            original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", False)
        monkeypatch.setattr(Path, "unlink", blocked_stage_unlink)

        executor_module._publish_output_create_only(
            stage,
            target,
            "result.parquet",
            project_root=tmp_path,
        )

        assert target.read_bytes() == b"output"
        assert stage.exists()

    def test_windows_artifact_sync_retries_transient_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifact = tmp_path / "stage.parquet"
        artifact.write_bytes(b"output")
        attempts = 0
        sleeps: list[float] = []
        original_fsync = executor_module.os.fsync

        def transient_fsync(descriptor: int) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError("temporarily locked")
            original_fsync(descriptor)

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(executor_module.os, "fsync", transient_fsync)
        monkeypatch.setattr(executor_module.time, "sleep", sleeps.append)

        executor_module._sync_output_artifact(artifact)

        assert attempts == 3
        assert sleeps == list(executor_module._WINDOWS_OUTPUT_SYNC_RETRY_DELAYS[:2])

    def test_windows_artifact_sync_raises_after_permission_retry_exhaustion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifact = tmp_path / "stage.parquet"
        artifact.write_bytes(b"output")
        attempts = 0

        def blocked_fsync(_descriptor: int) -> None:
            nonlocal attempts
            attempts += 1
            raise PermissionError("locked")

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(executor_module, "_WINDOWS_OUTPUT_SYNC_RETRY_DELAYS", (0.0, 0.0))
        monkeypatch.setattr(executor_module.os, "fsync", blocked_fsync)
        monkeypatch.setattr(executor_module.time, "sleep", lambda _delay: None)

        with pytest.raises(PermissionError, match="locked"):
            executor_module._sync_output_artifact(artifact)

        assert attempts == 3

    @pytest.mark.parametrize(
        ("is_windows", "expected_mode"),
        [(False, "rb"), (True, "rb+")],
    )
    def test_artifact_sync_uses_platform_appropriate_open_mode(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        is_windows: bool,
        expected_mode: str,
    ) -> None:
        artifact = tmp_path / "stage.parquet"
        artifact.write_bytes(b"output")
        opened_modes: list[str] = []
        original_open = Path.open

        def record_open(path: Path, mode: str):
            opened_modes.append(mode)
            return original_open(path, "rb+")

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", is_windows)
        monkeypatch.setattr(Path, "open", record_open)
        monkeypatch.setattr(executor_module.os, "fsync", lambda _descriptor: None)

        executor_module._sync_output_artifact(artifact)

        assert opened_modes == [expected_mode]

    def test_windows_directory_sync_is_a_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unexpected_open(*_args: object, **_kwargs: object) -> int:
            raise AssertionError("Windows directory sync must not open a directory descriptor")

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(executor_module.os, "open", unexpected_open)

        executor_module._sync_output_directory(tmp_path)

    def test_posix_directory_sync_uses_directory_descriptor_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        descriptor = 123
        opened: list[tuple[Path, int]] = []
        synced: list[int] = []
        closed: list[int] = []

        def record_open(path: Path, flags: int) -> int:
            opened.append((path, flags))
            return descriptor

        monkeypatch.setattr(executor_module, "_IS_WINDOWS", False)
        monkeypatch.setattr(executor_module.os, "open", record_open)
        monkeypatch.setattr(executor_module.os, "fsync", synced.append)
        monkeypatch.setattr(executor_module.os, "close", closed.append)

        executor_module._sync_output_directory(tmp_path)

        expected_flags = executor_module.os.O_RDONLY | getattr(
            executor_module.os,
            "O_DIRECTORY",
            0,
        )
        assert opened == [(tmp_path, expected_flags)]
        assert synced == [descriptor]
        assert closed == [descriptor]

    def test_directory_sync_failure_reports_published_but_unconfirmed_durability(
        self, haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        out_path = haute_scratch / "result.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(src_path)
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        def failed_directory_sync(_path: Path) -> None:
            assert out_path.exists()
            raise OSError("directory fsync failed")

        monkeypatch.setattr(executor_module, "_sync_output_directory", failed_directory_sync)

        with pytest.raises(DataOutputDurabilityError, match="was published") as exc:
            write_data_output(graph, "dout")

        assert exc.value.display_path == str(out_path)
        assert isinstance(exc.value.__cause__, OSError)
        assert out_path.exists()

    @pytest.mark.parametrize(
        ("arguments", "has_bom"),
        [
            ({}, False),
            ({"include_bom": True}, True),
        ],
    )
    def test_csv_bom_policy_is_explicit_and_opt_in(
        self,
        haute_scratch: Path,
        arguments: dict[str, object],
        has_bom: bool,
    ) -> None:
        src_path = haute_scratch / f"in-{has_bom}.parquet"
        pl.DataFrame({"city": ["Zürich"]}).write_parquet(src_path)
        out_path = haute_scratch / f"out-{has_bom}.csv"
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "csv",
                        "path": str(out_path),
                        "arguments": arguments,
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        write_data_output(graph, "dout")

        assert out_path.read_bytes().startswith(b"\xef\xbb\xbf") is has_bom
        assert pl.read_csv(out_path)["city"].to_list() == ["Zürich"]

    def test_csv_streaming_row_count_handles_embedded_newlines(self, haute_scratch: Path) -> None:
        src_path = haute_scratch / "in.parquet"
        pl.DataFrame({"text": ["alpha\nbeta", "gamma"]}).write_parquet(src_path)
        out_path = haute_scratch / "out.csv"
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "csv",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        result = write_data_output(graph, "dout")

        assert result.row_count == 2
        assert pl.read_csv(out_path)["text"].to_list() == ["alpha\nbeta", "gamma"]

    def test_csv_streaming_row_count_respects_header_and_dialect_arguments(
        self, haute_scratch: Path
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        pl.DataFrame({"id": [1, 2, 3], "text": ["alpha", "beta", "gamma"]}).write_parquet(src_path)
        out_path = haute_scratch / "out.csv"
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "csv",
                        "path": str(out_path),
                        "arguments": {
                            "include_header": False,
                            "line_terminator": "\r\n",
                            "separator": ";",
                            "quote_char": "'",
                        },
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        result = write_data_output(graph, "dout")

        assert result.row_count == 3
        assert (
            pl.read_csv(
                out_path,
                has_header=False,
                separator=";",
                quote_char="'",
                new_columns=["id", "text"],
            ).height
            == 3
        )

    def test_csv_row_count_scan_kwargs_preserve_writer_dialect(self) -> None:
        scanner_name = "".join(("scan_", "csv"))
        config = {
            "arguments": {
                "decimal_comma": True,
                "quote_char": "'",
                "separator": ";",
                "include_header": False,
                "line_terminator": "\r\n",
            }
        }

        assert executor_module._output_row_count_scan_kwargs(config, scanner_name) == {
            "raise_if_empty": False,
            "decimal_comma": True,
            "quote_char": "'",
            "separator": ";",
            "has_header": False,
            "eol_char": "\n",
        }

    def test_non_csv_row_count_scan_does_not_receive_csv_options(self) -> None:
        config = {"arguments": {"separator": ";", "include_header": False}}

        assert executor_module._output_row_count_scan_kwargs(config, "scan_avro") == {}

    @pytest.mark.parametrize("line_terminator", ["\t", "|"])
    def test_csv_row_count_scan_preserves_single_byte_line_terminators(
        self, line_terminator: str
    ) -> None:
        config = {"arguments": {"line_terminator": line_terminator}}

        assert executor_module._output_row_count_scan_kwargs(config, "scan_csv") == {
            "raise_if_empty": False,
            "eol_char": line_terminator,
        }

    @pytest.mark.parametrize("include_header", [True, False])
    def test_csv_streaming_row_count_handles_empty_output(
        self, haute_scratch: Path, include_header: bool
    ) -> None:
        src_path = haute_scratch / f"empty-{include_header}.parquet"
        pl.DataFrame({"id": pl.Series([], dtype=pl.Int64)}).write_parquet(src_path)
        out_path = haute_scratch / f"empty-{include_header}.csv"
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "csv",
                        "path": str(out_path),
                        "arguments": {"include_header": include_header},
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )

        result = write_data_output(graph, "dout")

        assert result.row_count == 0
        assert out_path.exists()

    def test_file_publication_syncs_artifact_and_parent_directory(
        self,
        haute_scratch: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src_path = haute_scratch / "in.parquet"
        pl.DataFrame({"x": [1]}).write_parquet(src_path)
        out_path = haute_scratch / "result.parquet"
        graph = PipelineGraph(
            nodes=[
                _ready_data_input_node(
                    "din",
                    {
                        "inputType": "file",
                        "format": "parquet",
                        "path": str(src_path),
                    },
                ),
                _data_output_node(
                    "dout",
                    {
                        "outputType": "file",
                        "format": "parquet",
                        "path": str(out_path),
                    },
                ),
            ],
            edges=[_edge("din", "dout")],
        )
        calls: list[tuple[str, Path]] = []
        original_artifact_sync = executor_module._sync_output_artifact
        original_directory_sync = executor_module._sync_output_directory

        def record_artifact(path: Path) -> None:
            calls.append(("artifact", path))
            original_artifact_sync(path)

        def record_directory(path: Path) -> None:
            calls.append(("directory", path))
            original_directory_sync(path)

        monkeypatch.setattr(executor_module, "_sync_output_artifact", record_artifact)
        monkeypatch.setattr(executor_module, "_sync_output_directory", record_directory)

        write_data_output(graph, "dout")

        assert calls[0][0] == "artifact"
        assert calls[0][1].name.startswith(".result.haute-stage-")
        assert calls[-1] == ("directory", out_path.parent)


class TestResolveDataOutputPath:
    def test_bare_filename_lands_under_outputs_with_format_extension(self) -> None:
        graph = PipelineGraph(nodes=[], edges=[])
        resolved, display = resolve_data_output_path(
            graph,
            {"outputType": "file", "format": "ndjson", "path": "result"},
        )
        assert display == "outputs/result.jsonl"
        assert resolved is not None and resolved.name == "result.jsonl"

    def test_explicit_extension_is_never_rewritten(self) -> None:
        graph = PipelineGraph(nodes=[], edges=[])
        _resolved, display = resolve_data_output_path(
            graph,
            {"outputType": "file", "format": "ndjson", "path": "out/result.ndjson"},
        )
        assert display == "out/result.ndjson"

    def test_database_target_has_no_filesystem_path(self) -> None:
        graph = PipelineGraph(nodes=[], edges=[])
        resolved, display = resolve_data_output_path(
            graph,
            {
                "outputType": "database",
                "format": "database",
                "table": "prices",
                "uri": "sqlite:///x.db",
            },
        )
        assert resolved is None
        assert display == "prices"

    def test_raw_sqlite_database_target_cannot_escape_project_root(
        self,
        haute_scratch: Path,
    ) -> None:
        graph = PipelineGraph(nodes=[], edges=[])

        with pytest.raises(ValueError, match="outside the project root"):
            resolve_data_output_path(
                graph,
                {
                    "outputType": "database",
                    "format": "database",
                    "table": "prices",
                    "uri": "sqlite:///../outside.sqlite",
                },
                project_root=haute_scratch,
            )

    def test_project_root_containment_is_enforced(self, haute_scratch) -> None:
        graph = PipelineGraph(nodes=[], edges=[])
        with pytest.raises(ValueError, match="outside the project root"):
            resolve_data_output_path(
                graph,
                {
                    "outputType": "file",
                    "format": "parquet",
                    "path": "/somewhere/else/out.parquet",
                },
                project_root=haute_scratch,
            )

    def test_empty_file_path_is_rejected_by_low_level_resolver(self) -> None:
        graph = PipelineGraph(nodes=[], edges=[])

        with pytest.raises(ValueError, match="no output path configured"):
            resolve_data_output_path(
                graph,
                {"outputType": "file", "format": "parquet", "path": ""},
            )

    def test_staging_path_must_be_a_contained_sibling(
        self,
        haute_scratch: Path,
        tmp_path: Path,
    ) -> None:
        final_path = haute_scratch / "result.parquet"
        outside_stage = tmp_path / "outside" / ".result.haute-stage-token.parquet"

        with pytest.raises(ValueError, match="must be a sibling"):
            executor_module._validate_output_publish_paths(
                final_path,
                outside_stage,
                project_root=haute_scratch,
            )

    def test_staging_path_must_preserve_final_extension(self, tmp_path: Path) -> None:
        final_path = tmp_path / "result.parquet"
        wrong_extension = tmp_path / ".result.haute-stage-token.csv"

        with pytest.raises(ValueError, match="preserve the final target extension"):
            executor_module._validate_output_publish_paths(
                final_path,
                wrong_extension,
                project_root=tmp_path,
            )

    def test_publish_paths_must_remain_inside_project_root(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        outside = tmp_path / "outside"
        project_root.mkdir()
        outside.mkdir()
        final_path = outside / "result.parquet"
        outside_stage = outside / ".result.haute-stage-token.parquet"

        with pytest.raises(ValueError, match="outside the project root"):
            executor_module._validate_output_publish_paths(
                final_path,
                outside_stage,
                project_root=project_root,
            )

    def test_cleanup_refuses_staging_path_outside_project_root(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        outside = tmp_path / "outside"
        project_root.mkdir()
        outside.mkdir()
        outside_stage = outside / ".result.haute-stage-token.parquet"
        outside_stage.write_bytes(b"preserve")

        executor_module._cleanup_output_staging_path(
            outside_stage,
            project_root=project_root,
        )

        assert outside_stage.read_bytes() == b"preserve"

    def test_cleanup_of_missing_staging_path_is_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        warnings: list[tuple[str, dict[str, object]]] = []

        def record_warning(event: str, **details: object) -> None:
            warnings.append((event, details))

        monkeypatch.setattr(executor_module.logger, "warning", record_warning)

        executor_module._cleanup_output_staging_path(
            tmp_path / ".missing.haute-stage-token.parquet",
            project_root=tmp_path,
        )

        assert warnings == []


class TestDataInputBuilder:
    """The exec builder's preview affordances and profile plumbing."""

    def _build(
        self,
        config: dict,
        profile: str | None = None,
        *,
        required_output_columns: frozenset[str] | None = None,
    ):
        if config:
            build_test_input_snapshot(config)
        node = _data_input_node("din", config)
        ctx = NodeBuildContext(
            node=node,
            source_names=[],
            source_ids=[],
            target_handles=None,
            row_limit=None,
            node_map=None,
            orig_source_names=None,
            preamble_ns=None,
            source=None,
            required_output_columns=required_output_columns,
            execution_profile=profile,
        )
        entry = NODE_REGISTRY[NodeType.DATA_INPUT]
        assert entry.exec is not None
        _name, fn, is_source = entry.exec(ctx)
        assert is_source
        return fn

    def test_unconfigured_node_previews_as_empty_frame(self) -> None:
        fn = self._build({})
        out = fn()
        assert isinstance(out, pl.LazyFrame)
        assert out.collect().height == 0

    def test_configured_node_reads_through_registry(self, haute_scratch, struct_frame) -> None:
        path = haute_scratch / "t.parquet"
        struct_frame.write_parquet(path)
        fn = self._build(
            {
                "inputType": "file",
                "format": "parquet",
                "path": str(path),
            }
        )
        assert_frame_equal(fn().collect(), struct_frame)

    def test_optional_polars_code_runs_after_input_resolution(
        self, haute_scratch, struct_frame
    ) -> None:
        path = haute_scratch / "t.parquet"
        struct_frame.write_parquet(path)
        fn = self._build(
            {
                "inputType": "file",
                "format": "parquet",
                "path": str(path),
                "code": "df = df.filter(pl.col('id') > 1).select('id')",
            }
        )
        assert fn().collect().to_dicts() == [{"id": 2}, {"id": 3}]

    def test_missing_projected_column_raises_schema_mismatch_before_polars_plan(
        self,
        haute_scratch,
    ) -> None:
        path = haute_scratch / "t.parquet"
        pl.DataFrame({"present": [1]}).write_parquet(path)
        fn = self._build(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": str(path),
                "arguments": {},
            },
            ExecutionProfile.OPTIMISER_SETUP.value,
            required_output_columns=frozenset({"missing"}),
        )

        with pytest.raises(
            SchemaMismatchError,
            match="Source projection references columns missing",
        ) as exc_info:
            fn()

        assert exc_info.value.context["missing"] == ["missing"]
        assert exc_info.value.context["available"] == ["present"]


class TestCodegenAndParseRoundTrip:
    """Generated code carries a config= sidecar reference and parses back."""

    def test_data_input_codegen_shape(self) -> None:
        from haute._codegen_builders import _gen_data_input
        from tests.conftest import compile_node_code

        node = _data_input_node(
            "quotes_in",
            {
                "inputType": "file",
                "format": "csv",
                "path": "data/q.csv",
                "code": "df = df.filter(pl.col('id') > 0)",
            },
        )
        code = _gen_data_input(node, [])
        assert '@pipeline.data_input(config="config/data_input/quotes_in.json")' in code
        assert "resolve_data_input_from_config" in code
        assert "df = df.filter(pl.col('id') > 0)" in code
        compile_node_code(code)

    def test_data_output_codegen_shape(self) -> None:
        from haute._codegen_builders import _gen_data_output
        from tests.conftest import compile_node_code

        node = _data_output_node(
            "prices_out",
            {"outputType": "file", "format": "ndjson", "path": "out.jsonl"},
        )
        code = _gen_data_output(node, ["scored"])
        assert '@pipeline.data_output(config="config/data_output/prices_out.json")' in code
        assert "write_polars_output_from_config" not in code
        assert "return scored" in code
        compile_node_code(code)

    def test_full_graph_to_code_to_graph_round_trip(self, haute_scratch) -> None:
        from haute.codegen import graph_to_code
        from haute.parser import parse_pipeline_source

        din_config = {
            "inputType": "file",
            "format": "csv",
            "path": "data/q.csv",
            "arguments": {"separator": ";", "schema_overrides": {"id": "int64"}},
        }
        dout_config = {
            "outputType": "file",
            "format": "parquet",
            "path": "outputs/result.parquet",
        }

        graph = PipelineGraph(
            nodes=[
                _data_input_node("quotes_in", din_config),
                _data_output_node("prices_out", dout_config),
            ],
            edges=[_edge("quotes_in", "prices_out")],
        )
        code = graph_to_code(graph)

        # Materialise the sidecars the generated decorators reference (paths
        # derived from haute_scratch; asserted equal to the canonical helper).
        for name, folder, node_type, cfg in (
            ("quotes_in", "data_input", NodeType.DATA_INPUT, din_config),
            ("prices_out", "data_output", NodeType.DATA_OUTPUT, dout_config),
        ):
            sidecar = haute_scratch / "config" / folder / f"{name}.json"
            assert sidecar == config_path_for_node(node_type, name, base_dir=haute_scratch)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(cfg), encoding="utf-8")

        parsed = parse_pipeline_source(code, _base_dir=haute_scratch)
        by_label = {n.data.label: n for n in parsed.nodes}

        din = by_label["quotes_in"]
        assert din.data.nodeType == NodeType.DATA_INPUT
        assert {k: din.data.config[k] for k in din_config} == din_config

        dout = by_label["prices_out"]
        assert dout.data.nodeType == NodeType.DATA_OUTPUT
        assert {k: dout.data.config[k] for k in dout_config} == dout_config

        # The graph edge survives the round trip.
        assert any(
            parsed.node_map[e.source].data.label == "quotes_in"
            and parsed.node_map[e.target].data.label == "prices_out"
            for e in parsed.edges
        )


class TestSidecarPersistence:
    def test_config_folders_registered(self) -> None:
        assert NODE_TYPE_TO_FOLDER[NodeType.DATA_INPUT] == "data_input"
        assert NODE_TYPE_TO_FOLDER[NodeType.DATA_OUTPUT] == "data_output"

    def test_prepare_config_keeps_spec_keys_and_drops_internal_state(self) -> None:
        from haute._config_io import _prepare_config_for_sidecar

        config = {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": "data/q.csv",
            "arguments": {"separator": ";"},
            "_editorOnly": {"open": True},
        }
        prepared = _prepare_config_for_sidecar(NodeType.DATA_INPUT, config)
        assert prepared == {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": "data/q.csv",
            "arguments": {"separator": ";"},
        }

    def test_sidecar_save_load_exact_shape(self, haute_scratch) -> None:
        config = {
            "inputType": "inline",
            "format": "records",
            "records": [{"a": 1, "s": {"k": "v"}}],
            "arguments": {
                "schema": {"a": "int64", "s": {"type": "Struct", "fields": {"k": "str"}}}
            },
        }
        sidecar = haute_scratch / "config" / "data_input" / "inline_in.json"
        assert sidecar == config_path_for_node(
            NodeType.DATA_INPUT, "inline_in", base_dir=haute_scratch
        )
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(config), encoding="utf-8")
        loaded = load_node_config(sidecar, base_dir=haute_scratch)
        assert loaded == config


def test_generated_data_input_resolves_project_relative_dataset_from_nested_pipeline(
    tmp_path: Path,
) -> None:
    import json

    from haute.graph_utils import resolve_data_input_from_config

    project_root = tmp_path / "project"
    pipeline_root = project_root / "rating"
    config_dir = pipeline_root / "config" / "data_input"
    data_dir = project_root / "data"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    pl.DataFrame({"quote_id": [1, 2]}).write_parquet(data_dir / "quotes.parquet")
    (config_dir / "quotes.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "data/quotes.parquet",
            }
        ),
        encoding="utf-8",
    )

    frame = resolve_data_input_from_config(
        "config/data_input/quotes.json",
        base_dir=pipeline_root,
        project_root=project_root,
    )

    assert frame.collect().to_dicts() == [{"quote_id": 1}, {"quote_id": 2}]


def test_data_input_codegen_passes_discovered_project_root() -> None:
    from haute._codegen_builders import _gen_data_input

    code = _gen_data_input(
        _data_input_node(
            "quotes", {"inputType": "file", "format": "parquet", "path": "data/quotes.parquet"}
        ),
        [],
    )

    assert "get_project_root(_HAUTE_CONFIG_BASE)" in code
    assert "base_dir=_HAUTE_CONFIG_BASE" in code
    assert "project_root=project_root" in code
