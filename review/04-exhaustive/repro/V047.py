"""Isolated reproduction for V047.

Claim: build_and_push_image() creates/reuses ``.haute_build`` in the cwd with
``mkdir(exist_ok=True)`` and only removes it in the ``except BaseException``
cleanup.  On a SUCCESSFUL build the directory persists.  A second
build_and_push_image() in the same working directory REUSES the directory and
its ``artifacts/`` subdir without clearing it: current artifacts are copied in
(shutil.copy2) but pre-existing files from a prior run are never pruned.  If the
artifact set changes between runs (a model file renamed / a .pkl replaced by a
.cbm) the orphan from run 1 survives and is bundled wholesale by the Dockerfile's
``COPY artifacts/ artifacts/`` into the new image — shipping a stale model
artifact that the current manifest does not reference.

This repro is fully isolated:
  * cwd is a tempfile.TemporaryDirectory (patched via Path.cwd, mirroring the
    project's own container tests).
  * source artifact files live in another tempdir.
  * Docker, app-source and Dockerfile generators are patched out (no Docker,
    and the real app source contains unicode that breaks cp1252 on Windows).
  * No read/write of rating/, src/, tests/, or any real project file.

It ASSERTS on the specific wrong VALUE: after run 2 the on-disk
``.haute_build/artifacts/`` directory still contains the run-1 artifact, while
the freshly written manifest references only the run-2 artifact.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from haute.deploy import _container
from haute.deploy._config import ContainerConfig, DeployConfig, ResolvedDeploy
from haute.graph_utils import GraphNode, NodeData, PipelineGraph


def _make_resolved(artifacts: dict[str, Path]) -> ResolvedDeploy:
    """Minimal ResolvedDeploy for a container build with the given artifacts."""
    config = DeployConfig(
        pipeline_file=Path("main.py"),
        model_name="test-model",
        target="container",
        container=ContainerConfig(base_image="python:3.11.9-slim", registry=""),
    )
    graph = PipelineGraph(nodes=[GraphNode(id="n1", data=NodeData(label="n1"))])
    return ResolvedDeploy(
        config=config,
        full_graph=graph,
        pruned_graph=graph,
        input_node_ids=["n1"],
        output_node_id="n1",
        artifacts=artifacts,
        input_schema={"age": "int"},
        output_schema={"premium": "float"},
        removed_node_ids=[],
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as cwd_str, tempfile.TemporaryDirectory() as src_str:
        cwd = Path(cwd_str)
        src = Path(src_str)

        # Run 1 artifact set: a single CatBoost model.
        old_artifact = src / "old_model.cbm"
        old_artifact.write_text("OLD MODEL BYTES")
        resolved_run1 = _make_resolved({"old_model.cbm": old_artifact})

        # Run 2 artifact set: the model was renamed (old removed, new added).
        new_artifact = src / "new_model.cbm"
        new_artifact.write_text("NEW MODEL BYTES")
        resolved_run2 = _make_resolved({"new_model.cbm": new_artifact})

        common_patches = {
            "_check_docker_available": lambda: None,
            "_docker_build": lambda build_dir, image_tag: None,
            "_docker_push": lambda image_tag: None,
            "_generate_app_source": lambda model_name, port: "# app\n",
            "_generate_dockerfile": lambda base_image, port, resolved: (
                "FROM x\nCOPY artifacts/ artifacts/\n"
            ),
        }

        with patch.object(_container.Path, "cwd", return_value=cwd):
            with patch.multiple(_container, **common_patches):
                _container.build_and_push_image(resolved_run1)
                # A successful build must leave the build dir on disk for the
                # claim to bite; assert that precondition explicitly.
                build_dir = cwd / ".haute_build"
                artifacts_dir = build_dir / "artifacts"
                assert build_dir.is_dir(), (
                    "PRECONDITION FAILED: .haute_build was removed after a "
                    "successful build — the staleness claim would not apply."
                )
                assert (artifacts_dir / "old_model.cbm").exists()

                # Second build in the SAME cwd with a DIFFERENT artifact set.
                _container.build_and_push_image(resolved_run2)

        artifacts_dir = cwd / ".haute_build" / "artifacts"
        files_on_disk = sorted(p.name for p in artifacts_dir.iterdir())

        manifest = json.loads(
            (cwd / ".haute_build" / "deploy_manifest.json").read_text()
        )
        manifest_artifacts = sorted(manifest["artifacts"].keys())

        print(f"artifacts/ on disk after run 2 : {files_on_disk}")
        print(f"manifest['artifacts'] after run2: {manifest_artifacts}")

        # The freshly written manifest correctly references only the run-2 model.
        assert manifest_artifacts == ["new_model.cbm"], manifest_artifacts

        # BUG: the run-1 orphan survives in the directory that the Dockerfile's
        # ``COPY artifacts/ artifacts/`` bundles wholesale into the image.
        assert "old_model.cbm" in files_on_disk, (
            "Expected stale run-1 artifact to survive (the bug); it did not — "
            "directory appears to have been cleared."
        )
        # And it is NOT referenced by the current manifest -> silent stale ship.
        assert "old_model.cbm" not in manifest_artifacts
        # Sanity: the new model is of course present and referenced.
        assert files_on_disk == ["new_model.cbm", "old_model.cbm"], files_on_disk
        # Prove the stale bytes are the OLD ones (not overwritten).
        stale_bytes = (artifacts_dir / "old_model.cbm").read_text()
        assert stale_bytes == "OLD MODEL BYTES", stale_bytes

        print(
            "REPRODUCED: .haute_build/artifacts/ ships a stale, unreferenced "
            "artifact 'old_model.cbm' (bytes=%r) from the previous run, while "
            "the current manifest lists only %s."
            % (stale_bytes, manifest_artifacts)
        )


if __name__ == "__main__":
    main()
