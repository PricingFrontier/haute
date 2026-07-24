"""Phase 1 Package 1G — Deploy correctness tests.

Covers two items:

* **#47** — Container base image pinning. ``DeployConfig`` must reject any
  ``container.base_image`` that is not pinned to a concrete release.  Floating
  tags (``python:3.11-slim``, ``python:3.11``, ``python:latest``, or no tag at
  all) all silently drift to whatever "latest" means at image-build time.
  Explicit patch pins (``python:3.11.9-slim``) and digest pins
  (``python@sha256:...``) are deterministic and accepted.

* **#48** — Static ``dataInput`` paths re-resolved at deploy.  The current
  ``_resolve_path`` tries CWD first, then the pipeline directory, and returns
  a path that depends on the bundling process's working directory.  That same
  path then flows through ``build_manifest`` into the deployed container,
  where CWD is ``/``.  The bundle manifest must contain fully resolved,
  absolute paths so runtime consumers do not need to re-resolve.

Tests deliberately fail loudly with ``DeployError`` or a concrete assertion;
these tests **fail until the corresponding fixes land**.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import polars as pl
import pytest

from haute.deploy._bundler import collect_artifacts
from haute.deploy._config import ContainerConfig, DeployConfig
from haute.deploy._utils import build_manifest
from haute.errors import DeployError
from tests._deploy_helpers import make_resolved_deploy
from tests.conftest import make_graph as _g

# ---------------------------------------------------------------------------
# Item #47 — Container base image pinning
# ---------------------------------------------------------------------------


def _make_config(base_image: str) -> DeployConfig:
    """Build a minimal DeployConfig with the given container.base_image."""
    return DeployConfig(
        pipeline_file=Path("main.py"),
        model_name="test-model",
        target="container",
        container=ContainerConfig(base_image=base_image),
    )


class TestBaseImageMustBePinned:
    """Item #47 — reject floating/unpinned base_image values.

    Validation runs at ``DeployConfig`` construction time (i.e. at the point
    the user's config is loaded) so misconfigurations surface before any
    container build work begins.
    """

    @pytest.mark.parametrize(
        "unpinned",
        [
            # Current default — major.minor, no patch, flavour suffix
            "python:3.11-slim",
            # Major.minor only
            "python:3.11",
            # Major only
            "python:3",
            # Explicit floating tag
            "python:latest",
            # No tag at all — equivalent to ":latest"
            "python",
            # Official registry path, no tag
            "docker.io/library/python",
            # Flavour suffix without any version
            "python:slim",
            # Minor only with flavour
            "python:3.11-bookworm",
        ],
    )
    def test_unpinned_base_image_rejected(self, unpinned: str) -> None:
        """Any base_image that does not pin a patch version or digest must raise."""
        with pytest.raises(DeployError, match="base_image"):
            _make_config(unpinned)

    @pytest.mark.parametrize(
        "pinned",
        [
            # Explicit patch version
            "python:3.11.9-slim",
            # Patch version, no flavour
            "python:3.11.9",
            # Patch version with a build suffix
            "python:3.11.9-slim-bookworm",
            # Registry-qualified patch pin
            "docker.io/library/python:3.11.9-slim",
        ],
    )
    def test_patch_pinned_base_image_accepted(self, pinned: str) -> None:
        """Explicit patch-version pins are deterministic and must be accepted."""
        config = _make_config(pinned)
        assert config.container.base_image == pinned

    @pytest.mark.parametrize(
        "digest_pinned",
        [
            # Bare digest pin
            "python@sha256:" + "a" * 64,
            # Registry-qualified digest pin
            "docker.io/library/python@sha256:" + "b" * 64,
            # Private registry digest pin
            "myregistry.example.com/haute/base@sha256:" + "c" * 64,
        ],
    )
    def test_digest_pinned_base_image_accepted(self, digest_pinned: str) -> None:
        """Digest-pinned images are the strongest form of pinning — always accept."""
        config = _make_config(digest_pinned)
        assert config.container.base_image == digest_pinned

    def test_default_base_image_rejected(self) -> None:
        """The current ``ContainerConfig`` default (``python:3.11-slim``) is
        unpinned and must be rejected — no user should be able to deploy with
        it silently. The fix must either change the default to a patch-pinned
        value or require the user to set one explicitly."""
        with pytest.raises(DeployError, match="base_image"):
            DeployConfig(
                pipeline_file=Path("main.py"),
                model_name="test-model",
                target="container",
                # Explicitly use the current default — whatever it is, it must
                # either be patch-pinned or rejected.
                container=ContainerConfig(),
            )

    def test_error_message_names_the_field(self) -> None:
        """The ``DeployError`` should explicitly mention ``base_image`` so
        users know which field is at fault."""
        with pytest.raises(DeployError) as excinfo:
            _make_config("python:3.11-slim")
        message = str(excinfo.value)
        assert "base_image" in message
        # And the offending value should appear so users can diff it.
        assert "python:3.11-slim" in message

    def test_from_toml_rejects_unpinned(self, tmp_path: Path) -> None:
        """``DeployConfig.from_toml`` must apply the same validation as
        direct construction — the check must live on the config itself,
        not on the TOML loader alone."""
        toml_content = """\
[project]
name = "svc"
pipeline = "main.py"

[deploy]
target = "container"
model_name = "svc"

[deploy.container]
base_image = "python:3.11-slim"
"""
        toml_path = tmp_path / "haute.toml"
        toml_path.write_text(toml_content)

        with pytest.raises(DeployError, match="base_image"):
            DeployConfig.from_toml(toml_path)

    def test_from_toml_accepts_patch_pinned(self, tmp_path: Path) -> None:
        """A patch-pinned ``base_image`` in haute.toml loads cleanly."""
        toml_content = """\
[project]
name = "svc"
pipeline = "main.py"

[deploy]
target = "container"
model_name = "svc"

[deploy.container]
base_image = "python:3.11.9-slim"
"""
        toml_path = tmp_path / "haute.toml"
        toml_path.write_text(toml_content)

        config = DeployConfig.from_toml(toml_path)
        assert config.container.base_image == "python:3.11.9-slim"


# ---------------------------------------------------------------------------
# Item #48 — dataInput paths resolved absolutely at bundle time
# ---------------------------------------------------------------------------


def _make_datasource_graph(node_id: str, raw_path: str):
    """Build a PipelineGraph with a single static dataInput node."""
    return _g(
        {
            "nodes": [
                {
                    "id": node_id,
                    "data": {
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "cacheMode": "direct",
                            "path": raw_path,
                            "arguments": {},
                        },
                    },
                },
            ],
        }
    )


class TestBundledPathsAreAbsolute:
    """Item #48 — bundled artifact paths must be absolute.

    Runtime consumers (container, MLflow model) cannot re-resolve against
    the project's CWD — the container starts in ``/`` and the MLflow model
    in the MLflow model directory.  The bundle manifest is the sole source
    of truth for where an artifact lives, so it must carry an already-
    resolved absolute path.
    """

    def test_collect_artifacts_returns_absolute_paths(self, tmp_path: Path) -> None:
        """Every path in the ``collect_artifacts`` output dict is absolute,
        regardless of whether the user wrote an absolute or relative path in
        their pipeline."""
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        data_file = pipeline_dir / "lookup.parquet"
        pl.DataFrame({"a": [1], "b": [2]}).write_parquet(data_file)

        # User wrote a pipeline-relative path.  This is what matters.
        graph = _make_datasource_graph("static_ds", "lookup.parquet")

        artifacts = collect_artifacts(graph, [], pipeline_dir)

        assert len(artifacts) == 1
        [(_name, resolved_path)] = artifacts.items()
        assert resolved_path.is_absolute(), f"Expected absolute path, got {resolved_path!r}"

    def test_collect_artifacts_resolves_against_pipeline_dir_not_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a file with the same name exists in CWD **and** in the
        pipeline directory, the bundler must pick the pipeline_dir file.

        The current ``_resolve_path`` picks CWD first — that is the bug
        in #48: the artifact picked at bundling time depends on which
        directory the user happened to run ``haute deploy`` from, and then
        that CWD-local absolute path ends up baked into the manifest.
        """
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        pipeline_file = pipeline_dir / "lookup.parquet"
        pl.DataFrame({"source": ["PIPELINE"]}).write_parquet(pipeline_file)

        cwd_dir = tmp_path / "elsewhere"
        cwd_dir.mkdir()
        cwd_file = cwd_dir / "lookup.parquet"
        pl.DataFrame({"source": ["CWD"]}).write_parquet(cwd_file)

        monkeypatch.chdir(cwd_dir)

        graph = _make_datasource_graph("static_ds", "lookup.parquet")
        artifacts = collect_artifacts(graph, [], pipeline_dir)

        [(_name, resolved_path)] = artifacts.items()
        assert resolved_path.is_absolute()
        # The bundler must have resolved against pipeline_dir, not CWD.
        assert resolved_path.resolve() == pipeline_file.resolve(), (
            f"Expected pipeline-relative file {pipeline_file}, got {resolved_path}"
        )
        # And the file contents should confirm that — belt and braces.
        assert pl.read_parquet(resolved_path)["source"].to_list() == ["PIPELINE"]

    def test_manifest_artifact_paths_are_absolute(self, tmp_path: Path) -> None:
        """``build_manifest`` must emit absolute artifact paths — the
        manifest is what the container / MLflow model reads at runtime,
        and runtime has no access to the user's project CWD."""
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        pl.DataFrame({"x": [1]}).write_parquet(pipeline_dir / "lookup.parquet")

        graph = _make_datasource_graph("static_ds", "lookup.parquet")
        artifacts = collect_artifacts(graph, [], pipeline_dir)

        resolved = make_resolved_deploy(
            pipeline_file=pipeline_dir / "main.py",
            model_name="svc",
            target="container",
            artifacts=artifacts,
        )
        manifest = build_manifest(resolved)

        assert "artifacts" in manifest
        assert manifest["artifacts"], "manifest must contain the collected artifact"
        for name, path_str in manifest["artifacts"].items():
            assert Path(path_str).is_absolute(), (
                f"manifest['artifacts'][{name!r}] is not absolute: {path_str!r}"
            )

    def test_manifest_paths_survive_cwd_change(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once the manifest has been built, changing CWD must not change
        what those paths refer to — i.e. the stored strings must already
        point at concrete files on disk without relying on CWD."""
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        real_file = pipeline_dir / "lookup.parquet"
        pl.DataFrame({"payload": [1]}).write_parquet(real_file)

        # Build the manifest from the pipeline directory as CWD.
        monkeypatch.chdir(pipeline_dir)
        graph = _make_datasource_graph("static_ds", "lookup.parquet")
        artifacts = collect_artifacts(graph, [], pipeline_dir)
        resolved = make_resolved_deploy(
            pipeline_file=pipeline_dir / "main.py",
            model_name="svc",
            target="container",
            artifacts=artifacts,
        )
        manifest = build_manifest(resolved)

        # Move somewhere that contains no ``lookup.parquet``.  If the bundler
        # stored a relative path, re-reading the manifest entry now would
        # resolve to a non-existent file.
        other_dir = tmp_path / "somewhere_else"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)

        # Round-trip through JSON to mimic the container reading manifest.
        round_tripped = json.loads(json.dumps(manifest))
        for _name, path_str in round_tripped["artifacts"].items():
            p = Path(path_str)
            assert p.is_absolute(), f"relative path in manifest: {path_str!r}"
            assert p.is_file(), (
                f"manifest path {path_str!r} does not resolve to a real file "
                f"from CWD {os.getcwd()!r}"
            )
            assert p.read_bytes() == real_file.read_bytes()
