"""Direct tests for shared runtime-path validation."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest
from fastapi import HTTPException

from haute._path_resolution import (
    MalformedRuntimePathError,
    RuntimePathError,
    RuntimePathOutsideProjectError,
)
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph
from haute.routes import modelling as modelling_routes
from haute.routes import optimiser as optimiser_routes
from haute.routes._runtime_path_errors import runtime_path_http_exception
from haute.routes._save_pipeline import SavePipelineService
from haute.routes.pipeline import _prepare_runtime_graph, _validate_runtime_input_paths
from haute.schemas import (
    DispersionEstimateRequest,
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserSolveRequest,
    SavePipelineRequest,
    TrainEstimateRequest,
    TrainRequest,
)


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "node_type",
    [NodeType.DATA_INPUT, NodeType.API_INPUT, NodeType.EXTERNAL_FILE],
)
def test_validate_runtime_input_paths_rejects_project_escape_for_file_backed_nodes(
    node_type: NodeType,
) -> None:
    config: dict[str, object] = {"path": "../escape.parquet"}
    if node_type == NodeType.DATA_INPUT:
        config.update(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "arguments": {},
            }
        )
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="n1",
                data=NodeData(
                    label="n1",
                    nodeType=node_type,
                    config=config,
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
    ("error", "expected_status"),
    [
        (MalformedRuntimePathError("wording is deliberately irrelevant"), 400),
        (RuntimePathOutsideProjectError("different wording"), 403),
    ],
)
def test_runtime_path_http_status_uses_exception_type(
    error: RuntimePathError,
    expected_status: int,
) -> None:
    mapped = runtime_path_http_exception(error)

    assert mapped.status_code == expected_status
    assert mapped.detail == str(error)


def test_runtime_path_http_status_rejects_unknown_subtype() -> None:
    with pytest.raises(TypeError, match="Unsupported runtime path error"):
        runtime_path_http_exception(RuntimePathError("unclassified"))


@pytest.mark.parametrize(
    "route_name",
    ["train", "dispersion", "estimate"],
)
def test_modelling_execution_routes_reject_external_graph_source(
    route_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = PipelineGraph(source_file=str(tmp_path.parent / "external" / "pipeline.py"))

    if route_name == "train":
        monkeypatch.setattr(modelling_routes._train_service, "start", lambda _body: object())
        route = modelling_routes.train_model
        body = TrainRequest(graph=graph, node_id="model")
    elif route_name == "dispersion":
        monkeypatch.setattr(
            modelling_routes._train_service,
            "start_dispersion_estimate",
            lambda _body: object(),
        )
        route = modelling_routes.estimate_dispersion
        body = DispersionEstimateRequest(
            graph=graph,
            node_id="model",
            param="theta",
        )
    else:
        route = modelling_routes.estimate_training
        body = TrainEstimateRequest(graph=graph, node_id="model")

    with pytest.raises(HTTPException) as exc_info:
        route(body)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


@pytest.mark.parametrize(
    "route_name",
    ["solve", "estimate", "auto-range-start"],
)
def test_optimiser_execution_routes_reject_external_graph_source(
    route_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = PipelineGraph(source_file=str(tmp_path.parent / "external" / "pipeline.py"))

    if route_name == "solve":
        monkeypatch.setattr(optimiser_routes._solve_service, "start", lambda _body: object())
        route = optimiser_routes.solve
        body = OptimiserSolveRequest(graph=graph, node_id="optimiser")
    elif route_name == "estimate":
        route = optimiser_routes.estimate_solve
        body = OptimiserEstimateRequest(graph=graph, node_id="optimiser")
    else:
        monkeypatch.setattr(
            optimiser_routes._solve_service,
            "start_frontier_auto_range",
            lambda _body: object(),
        )
        route = optimiser_routes.start_frontier_auto_range
        body = OptimiserFrontierAutoRangeRequest(graph=graph, node_id="optimiser")

    with pytest.raises(HTTPException) as exc_info:
        route(body)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


def test_prepare_runtime_graph_validates_paths_inside_submodels() -> None:
    child = GraphNode(
        id="nested-input",
        data=NodeData(
            label="nested-input",
            nodeType=NodeType.EXTERNAL_FILE,
            config={"path": "../escape.parquet"},
        ),
    )
    graph = PipelineGraph(
        source_file="main.py",
        submodels={
            "nested": {
                "file": "modules/nested.py",
                "graph": {
                    "nodes": [child.model_dump(mode="json")],
                    "edges": [],
                },
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        _prepare_runtime_graph(graph)

    assert exc_info.value.status_code == 403
    assert "outside the project root" in exc_info.value.detail


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
    escapes the project root must be rejected here, exactly like a dataInput
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
            nodeType=NodeType.DATA_INPUT,
            config={
                "inputType": "file",
                "format": "csv",
                "mode": "scan",
                "path": f"{node_id}.csv",
                "arguments": {},
            },
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
    assert "config/data_input/Foo.json" in exc_info.value.detail
    assert "config/data_input/foo.json" in exc_info.value.detail
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

    assert (tmp_path / "config" / "data_input" / "Foo.json").is_file()
    assert (tmp_path / "config" / "data_input" / "Bar.json").is_file()


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
    sidecar_dir = tmp_path / "config" / "data_input"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "Foo.json").write_text('{"path": "old.csv"}', encoding="utf-8")
    svc._prev_config_files = {"config/data_input/Foo.json": '{"path": "old.csv"}'}
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
    sidecar_dir = tmp_path / "config" / "data_input"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "Foo.json").write_text('{"path": "old.csv"}', encoding="utf-8")
    svc._prev_config_files = {"config/data_input/Foo.json": '{"path": "old.csv"}'}
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
    assert f"config/data_input/{label}.json" in exc_info.value.detail
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

    assert (tmp_path / "config" / "data_input" / f"{label}.json").is_file()


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


# ---------------------------------------------------------------------------
# End-to-end write+cleanup ordering on a REAL filesystem.
#
# The guard tests above exercise set logic that is right on every OS; these
# two prove the physical outcome — which bytes the surviving inode holds and
# whether the explicit module-delete path can destroy a freshly written
# module — on the case-insensitive filesystems the macOS/Windows
# platform-smoke CI legs provide. On case-sensitive Linux the same tests pin
# the accepted residue trade-off instead.
# ---------------------------------------------------------------------------


def test_case_only_rename_single_surviving_inode_holds_new_bytes(
    tmp_path: Path,
) -> None:
    """After Foo→FOO, the one physical file must hold the NEW config bytes.

    `test_case_only_rename_survives_stale_cleanup` asserts the guard's set
    logic; this asserts the disk: on a case-insensitive filesystem both
    spellings must reach ONE directory entry whose content is the fresh
    save, proving write-then-cleanup ordering end to end. On a
    case-sensitive filesystem the excluded prev casing stays behind with
    its OLD bytes — the documented Linux residue trade-off.
    """
    svc = SavePipelineService(tmp_path)
    sidecar_dir = tmp_path / "config" / "data_input"
    sidecar_dir.mkdir(parents=True)
    old = sidecar_dir / "Foo.json"
    old.write_text('{"path": "old.csv"}', encoding="utf-8")
    svc._prev_config_files = {"config/data_input/Foo.json": '{"path": "old.csv"}'}
    graph = PipelineGraph(nodes=[_config_node("a", "FOO")], edges=[])

    svc._write_config_files(graph)
    removed = svc._remove_stale_config_files(graph)

    assert removed == []
    new = sidecar_dir / "FOO.json"
    assert new.is_file()
    assert "a.csv" in new.read_text(encoding="utf-8")
    if old.exists() and os.path.samefile(old, new):
        # Case-insensitive filesystem: one inode, one directory entry (the
        # filesystem is case-PRESERVING, so the entry keeps whichever
        # spelling created it — here prev's ``Foo.json``), and the prev
        # spelling reads the NEW bytes.
        entries = [p.name for p in sidecar_dir.iterdir()]
        assert len(entries) == 1
        assert entries[0].casefold() == "foo.json"
        assert "a.csv" in old.read_text(encoding="utf-8")
    else:
        # Case-sensitive filesystem: prev casing left behind, old bytes.
        assert old.read_text(encoding="utf-8") == '{"path": "old.csv"}'


def _submodel_save_request(graph: PipelineGraph) -> SavePipelineRequest:
    return SavePipelineRequest(
        name="main",
        description="",
        graph=graph,
        source_file="main.py",
    )


def _run_submodel_save(
    tmp_path: Path,
    delete_module_files: list[str],
) -> None:
    """Drive a full ``save()`` whose codegen emits ``modules/Foo.py``."""
    from unittest.mock import patch

    svc = SavePipelineService(tmp_path)
    graph = PipelineGraph(nodes=[_config_node("a", "Src")], edges=[])
    graph.submodels = {"Foo": {"file": "modules/Foo.py", "graph": {"nodes": [], "edges": []}}}
    files = {
        "main.py": 'import haute\npipeline = haute.Pipeline("main")\n',
        "modules/Foo.py": "# new module\n",
    }
    with patch("haute.codegen.graph_to_code_multi", return_value=files):
        svc.save(_submodel_save_request(graph), delete_module_files=delete_module_files)


def test_case_only_module_rename_survives_explicit_delete(tmp_path: Path) -> None:
    """A case-only submodel rename must not delete the freshly written module.

    Renaming submodel ``foo`` → ``Foo`` makes the client request deletion of
    ``modules/foo.py`` in the same save that writes ``modules/Foo.py`` — the
    SAME on-disk file on the case-insensitive filesystems macOS and Windows
    default to. Unguarded, the staged delete unlinks the module the save
    just wrote (the explicit-delete twin of the stale-diff bug fixed in
    PR #43).
    """
    (tmp_path / "main.py").write_text(
        'import haute\npipeline = haute.Pipeline("main")\n', encoding="utf-8"
    )
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    old = modules_dir / "foo.py"
    old.write_text("# old module\n", encoding="utf-8")

    _run_submodel_save(tmp_path, delete_module_files=["modules/foo.py"])

    new = modules_dir / "Foo.py"
    assert new.is_file()
    assert new.read_text(encoding="utf-8") == "# new module\n"
    if old.exists() and not os.path.samefile(old, new):
        # Case-sensitive filesystem: the skip leaves the old casing behind
        # as residue with its old bytes — same trade-off as the stale diff.
        assert old.read_text(encoding="utf-8") == "# old module\n"


def test_genuine_module_delete_still_removes_the_file(tmp_path: Path) -> None:
    """The casefold skip must not stop real module deletions."""
    (tmp_path / "main.py").write_text(
        'import haute\npipeline = haute.Pipeline("main")\n', encoding="utf-8"
    )
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    gone = modules_dir / "gone.py"
    gone.write_text("# retired module\n", encoding="utf-8")

    _run_submodel_save(tmp_path, delete_module_files=["modules/gone.py"])

    assert not gone.exists()
    assert (modules_dir / "Foo.py").is_file()


# ---------------------------------------------------------------------------
# NFC/NFD: no normalization, pinned.
#
# Non-sanitized user paths (dataInput/apiInput/externalFile ``path`` config)
# are handed to the filesystem exactly as spelled — haute applies NO Unicode
# normalization (ruled 2026-07-09; the case-ambiguity audit in
# haute._path_case_audit is the advisory companion). On the normalizing
# filesystem the macOS leg provides (APFS), an NFD spelling reaching an
# NFC-named file is the FILESYSTEM's doing; on Linux the same spelling
# misses and haute must not rescue it. This test pins that contract on both
# legs.
# ---------------------------------------------------------------------------


def test_nfc_nfd_input_path_spelling_is_never_normalized(tmp_path: Path) -> None:
    from haute._path_resolution import resolve_runtime_file_path

    nfc = "café.csv"  # é precomposed (U+00E9)
    nfd = unicodedata.normalize("NFD", nfc)  # e + combining acute (U+0301)
    assert nfc != nfd
    (tmp_path / nfc).write_text("nfc-bytes", encoding="utf-8")

    resolved = resolve_runtime_file_path(nfd, pipeline_dir=tmp_path, project_root=tmp_path)

    # The pin: haute preserves the given spelling byte-for-byte — the
    # resolved path is still NFD, never rewritten to the on-disk NFC form.
    assert resolved.name == nfd
    assert resolved.name != nfc
    if (tmp_path / nfd).exists():
        # Normalization-insensitive filesystem (APFS on the macOS leg):
        # both spellings reach the one file — the filesystem's equivalence,
        # not a haute rewrite.
        assert (tmp_path / nfd).read_text(encoding="utf-8") == "nfc-bytes"
    else:
        # Normalization-sensitive filesystem (ext4/NTFS): the NFD spelling
        # misses, and haute must NOT quietly resolve it to the NFC file.
        assert not resolved.exists()
