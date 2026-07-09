"""Direct tests for shared runtime-path validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes._save_pipeline import SavePipelineService
from haute.routes.pipeline import _validate_runtime_input_paths


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "node_type",
    [NodeType.DATA_SOURCE, NodeType.API_INPUT, NodeType.EXTERNAL_FILE],
)
def test_validate_runtime_input_paths_rejects_project_escape_for_file_backed_nodes(
    node_type: NodeType,
) -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(
                    label="n1",
                    nodeType=node_type,
                    config={"path": "../escape.parquet"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


def test_validate_runtime_input_paths_maps_embedded_null_byte_to_400() -> None:
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(
                    label="n1",
                    nodeType=NodeType.API_INPUT,
                    config={"path": "bad\x00name.parquet"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 400
    assert "null byte" in exc_info.value.detail


@pytest.mark.parametrize(
    "path_field",
    ["artifact_path", "feature_contract_path"],
)
def test_validate_runtime_input_paths_rejects_model_score_escape(
    path_field: str,
) -> None:
    """modelScore artifact/contract paths must be confined like every input.

    The executor deliberately does not enforce the project root for these
    fields, relying on this route guard to gate route-driven flows. A path that
    escapes the project root must be rejected here, exactly like a dataSource
    ``path``.
    """
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="score",
                data=NodeData(
                    label="score",
                    nodeType=NodeType.MODEL_SCORE,
                    config={path_field: "../escape.json"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(graph)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Case-only config-sidecar collisions.
#
# Node labels differing only in case ("Foo" / "foo") sanitize to distinct
# Python identifiers, but their config sidecars (config/<type>/Foo.json vs
# config/<type>/foo.json) are the SAME file on the case-insensitive
# filesystems macOS and Windows default to — the second write would silently
# overwrite the first. The save-time guard therefore rejects the collision on
# every platform. These tests live in this file because it runs on the
# macOS/Windows platform-smoke CI leg, i.e. on real case-insensitive
# filesystems.
# ---------------------------------------------------------------------------


def _config_node(node_id: str, label: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label,
            nodeType=NodeType.DATA_SOURCE,
            config={"path": f"{node_id}.csv"},
        ),
    )


def test_case_only_label_collision_rejected_before_any_config_write(
    tmp_path: Path,
) -> None:
    """Two same-type nodes whose labels differ only in case must not save."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(
        nodes=[_config_node("a", "Foo"), _config_node("b", "foo")],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        svc._write_config_files(graph)

    assert exc_info.value.status_code == 400
    assert "Duplicate config sidecar path" in exc_info.value.detail
    assert "config/data_source/Foo.json" in exc_info.value.detail
    assert "config/data_source/foo.json" in exc_info.value.detail
    # Rejected before any write: neither casing reached the disk.
    assert not (tmp_path / "config").exists()


def test_case_only_collision_between_parent_and_submodel_rejected(
    tmp_path: Path,
) -> None:
    """A submodel child colliding case-only with a parent node must not save."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(nodes=[_config_node("parent", "Shared")], edges=[])
    child = _config_node("child", "shared")
    graph.submodels = {
        "pricing": {
            "file": "modules/pricing.py",
            "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        svc._write_config_files(graph)

    assert exc_info.value.status_code == 400
    assert "Duplicate config sidecar path" in exc_info.value.detail
    assert not (tmp_path / "config").exists()


def test_distinct_labels_still_write_one_sidecar_each(tmp_path: Path) -> None:
    """The casefold guard must not over-trigger on genuinely distinct names."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(
        nodes=[_config_node("a", "Foo"), _config_node("b", "Bar")],
        edges=[],
    )

    svc._write_config_files(graph)

    assert (tmp_path / "config" / "data_source" / "Foo.json").is_file()
    assert (tmp_path / "config" / "data_source" / "Bar.json").is_file()


# ---------------------------------------------------------------------------
# Case-only collisions across saves (rename) and in codegen output paths.
#
# The guards above reject two nodes colliding within ONE graph. A case-only
# RENAME collides across saves instead: prev's ``Foo.json`` and the new
# ``FOO.json`` are the same on-disk file on macOS/Windows, so stale cleanup
# unlinking the prev casing would delete the sidecar the save just wrote.
# Generated ``modules/<name>.py`` files have the same overwrite hazard as
# sidecars. Like the guards above, these tests live here because this file
# runs on the macOS/Windows platform-smoke CI leg.
# ---------------------------------------------------------------------------


def test_case_only_rename_survives_stale_cleanup(tmp_path: Path) -> None:
    """A case-only node rename must not delete the freshly written sidecar.

    Renaming ``Foo`` → ``FOO`` makes the save write ``FOO.json``, which on
    the case-insensitive filesystems macOS and Windows default to is the
    SAME file as prev's ``Foo.json`` — overwritten in place. Treating the
    prev casing as stale would unlink the survivor, destroying the node's
    config at the moment it was saved.
    """
    svc = SavePipelineService(tmp_path)
    sidecar_dir = tmp_path / "config" / "data_source"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "Foo.json").write_text('{"path": "old.csv"}', encoding="utf-8")
    svc._prev_config_files = {"config/data_source/Foo.json": '{"path": "old.csv"}'}
    graph = PipelineGraph(nodes=[_config_node("a", "FOO")], edges=[])

    svc._write_config_files(graph)
    removed = svc._remove_stale_config_files(graph)

    assert removed == []
    survivor = sidecar_dir / "FOO.json"
    assert survivor.is_file()
    assert "a.csv" in survivor.read_text(encoding="utf-8")


def test_genuine_rename_still_removes_stale_sidecar(tmp_path: Path) -> None:
    """The casefold exclusion must not stop real stale cleanup."""
    svc = SavePipelineService(tmp_path)
    sidecar_dir = tmp_path / "config" / "data_source"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "Foo.json").write_text('{"path": "old.csv"}', encoding="utf-8")
    svc._prev_config_files = {"config/data_source/Foo.json": '{"path": "old.csv"}'}
    graph = PipelineGraph(nodes=[_config_node("a", "Bar")], edges=[])

    svc._write_config_files(graph)
    removed = svc._remove_stale_config_files(graph)

    assert [path.name for path in removed] == ["Foo.json"]
    assert not (sidecar_dir / "Foo.json").exists()
    assert (sidecar_dir / "Bar.json").is_file()


def test_codegen_case_only_module_collision_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    """Generated module paths differing only in case must not save."""
    svc = SavePipelineService(tmp_path)
    files = {"modules/Pricing.py": "# a", "modules/pricing.py": "# b"}

    with pytest.raises(HTTPException) as exc_info:
        svc._write_generated_code_files(files, "main.py", [])

    assert exc_info.value.status_code == 400
    assert "duplicate output path" in exc_info.value.detail
    assert "modules/Pricing.py" in exc_info.value.detail
    assert "modules/pricing.py" in exc_info.value.detail
    # Rejected before any write: neither casing reached the disk.
    assert not (tmp_path / "modules").exists()


def test_codegen_distinct_module_paths_still_write(tmp_path: Path) -> None:
    """The casefold guard must not over-trigger on genuinely distinct modules."""
    svc = SavePipelineService(tmp_path)
    files = {"modules/pricing.py": "# a", "modules/claims.py": "# b"}

    svc._write_generated_code_files(files, "main.py", [])

    assert (tmp_path / "modules" / "pricing.py").is_file()
    assert (tmp_path / "modules" / "claims.py").is_file()


# ---------------------------------------------------------------------------
# Windows-reserved device names in save-time filenames.
#
# A node label like ``CON`` sanitizes to a valid Python identifier, but its
# sidecar ``config/<type>/CON.json`` (and a submodel's ``modules/NUL.py``)
# names a DOS device, not a file, on Windows — case-insensitively and with
# ANY extension. The save-time guards therefore reject reserved stems (CON,
# PRN, AUX, NUL, COM1-COM9, LPT1-LPT9) on every platform, mirroring the
# casefold collision guards above. Like those guards, these tests live in
# this file because it runs on the macOS/Windows platform-smoke CI legs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["CON", "con", "Com1", "LPT9"])
def test_reserved_device_label_rejected_before_any_config_write(
    tmp_path: Path,
    label: str,
) -> None:
    """A config-bearing node whose sidecar stem is a reserved device must not save."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(nodes=[_config_node("a", label)], edges=[])

    with pytest.raises(HTTPException) as exc_info:
        svc._write_config_files(graph)

    assert exc_info.value.status_code == 400
    assert "reserved device name on Windows" in exc_info.value.detail
    assert f"config/data_source/{label}.json" in exc_info.value.detail
    # Rejected before any write: nothing reached the disk.
    assert not (tmp_path / "config").exists()


def test_reserved_device_label_in_submodel_graph_rejected(tmp_path: Path) -> None:
    """A reserved-stem sidecar inside an embedded submodel graph must not save."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(nodes=[_config_node("parent", "Safe")], edges=[])
    child = _config_node("child", "NUL")
    graph.submodels = {
        "pricing": {
            "file": "modules/pricing.py",
            "graph": {"nodes": [child.model_dump(mode="json")], "edges": []},
        }
    }

    with pytest.raises(HTTPException) as exc_info:
        svc._write_config_files(graph)

    assert exc_info.value.status_code == 400
    assert "reserved device name on Windows" in exc_info.value.detail
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize("label", ["CONTRACT", "CONS", "COM", "COM10"])
def test_near_reserved_labels_still_save(tmp_path: Path, label: str) -> None:
    """The reserved-name guard must not over-trigger on near-miss names."""
    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(nodes=[_config_node("a", label)], edges=[])

    svc._write_config_files(graph)

    assert (tmp_path / "config" / "data_source" / f"{label}.json").is_file()


def test_codegen_reserved_module_filename_rejected_before_any_write(
    tmp_path: Path,
) -> None:
    """A submodel named ``NUL`` mints ``modules/NUL.py`` — rejected at codegen."""
    svc = SavePipelineService(tmp_path)
    files = {"main.py": "# main", "modules/NUL.py": "# device, not a file"}

    with pytest.raises(HTTPException) as exc_info:
        svc._write_generated_code_files(files, "main.py", [])

    assert exc_info.value.status_code == 400
    assert "reserved device name on Windows" in exc_info.value.detail
    assert "'NUL.py'" in exc_info.value.detail
    # Rejected before any write: neither file reached the disk.
    assert not (tmp_path / "modules").exists()
    assert not (tmp_path / "main.py").exists()


def test_codegen_reserved_main_filename_rejected(tmp_path: Path) -> None:
    """The main pipeline file gets the same reserved-stem treatment."""
    svc = SavePipelineService(tmp_path)
    files = {"CON.py": "# console, not a file"}

    with pytest.raises(HTTPException) as exc_info:
        svc._write_generated_code_files(files, "CON.py", [])

    assert exc_info.value.status_code == 400
    assert "reserved device name on Windows" in exc_info.value.detail
    assert not (tmp_path / "CON.py").exists()


def test_validate_runtime_input_paths_checks_optimiser_apply_file_mode_only() -> None:
    file_graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="apply",
                data=NodeData(
                    label="apply",
                    nodeType=NodeType.OPTIMISER_APPLY,
                    config={"sourceType": "file", "artifact_path": "../escape.json"},
                ),
            )
        ],
        edges=[],
    )
    job_graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="apply",
                data=NodeData(
                    label="apply",
                    nodeType=NodeType.OPTIMISER_APPLY,
                    config={"sourceType": "job", "artifact_path": "../escape.json"},
                ),
            )
        ],
        edges=[],
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_runtime_input_paths(file_graph)
    assert exc_info.value.status_code == 403

    _validate_runtime_input_paths(job_graph)
