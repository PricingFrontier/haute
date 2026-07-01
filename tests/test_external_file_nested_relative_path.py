"""Regression: an ``EXTERNAL_FILE`` whose object ``path`` is pipeline-directory-
relative must resolve to the same file whether the pipeline lives at the project
root or in a subdirectory, and regardless of cwd.

THE BUG (sibling of the v2 apiInput cwd path-resolution bug). The in-process
executor's external-file builder (``_builders._build_external_file``) closed over
the RAW relative ``path`` and handed it to ``_io.load_external_object``, which
resolves the path against ``cwd``: ``content_hash(Path(path))`` opens it from the
process working directory and ``_sandbox.validate_project_path`` resolves it
there too. The cache-build route and codegen anchor a relative data path to the
PIPELINE DIRECTORY instead. So with the standard ``rating/main.py`` layout and
the server run from the project root, the loader looked for
``<root>/models/...`` while the object actually lived under
``<root>/rating/models/...`` — a spurious file-not-found that only disappeared
when cwd == the pipeline dir.

This mirrors tests/test_apiinput_nested_relative_path.py: a NESTED pipeline, a
RELATIVE pipeline-dir-relative ``path``, cwd kept at the project root (never the
pipeline dir), and no absolute path anywhere. It exercises the builder through
``_build_node_fn`` — the same factory the executor calls — and invokes the
returned callable, which is where ``load_external_object`` runs.

NOTE ON THE ABSOLUTE GUARD. Unlike a dataSource read, ``load_external_object``
independently enforces project-root containment via ``validate_project_path``, so
an out-of-cwd ABSOLUTE object path is rejected there — a pre-existing gate this
fix leaves untouched. The per-node-type out-of-cwd absolute-passthrough guard
therefore targets the anchoring step the builder now applies
(``_resolve_runtime_data_path``): it must pass an out-of-cwd absolute through
unchanged, so the fix adds no new rejection. The in-project absolute case is
covered end-to-end (loads, not re-anchored).
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from haute._sandbox import _get_project_root, set_project_root
from haute.executor import _build_node_fn
from tests.conftest import make_node as _n

#: The external object ``path`` written into the node config — RELATIVE and
#: pipeline-directory-relative (resolves under ``rating/`` at runtime).
_RELATIVE_OBJECT_PATH = "models/factor.json"
_OBJECT = {"multiplier": 10}


def _external_file_node(path: str) -> object:
    """An externalFile node whose user code multiplies through the loaded ``obj``."""
    return _n(
        {
            "id": "ext",
            "data": {
                "label": "ext",
                "nodeType": "externalFile",
                "config": {
                    "path": path,
                    "fileType": "json",
                    "code": "df = df.with_columns(y=pl.col('x') * obj['multiplier'])",
                },
            },
        }
    )


@pytest.fixture()
def nested_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A project whose pipeline lives in a SUBDIRECTORY, with cwd pinned at the
    project ROOT (never the pipeline dir) — the configuration the bug needs.

    Yields the on-disk absolute object path; the node config carries the RELATIVE
    ``path`` which must anchor to ``<root>/rating/models/...``.
    """
    monkeypatch.chdir(tmp_path)  # cwd == project root, NOT the pipeline dir
    original = _get_project_root()
    set_project_root(tmp_path)

    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')
    pipeline_directory = tmp_path / "rating"
    pipeline_directory.mkdir(parents=True, exist_ok=True)
    pipeline_directory.joinpath("main.py").write_text("import haute\npipeline = haute.Pipeline()\n")

    # Object lives under the PIPELINE dir (<root>/rating/models/...), where a
    # pipeline-dir-relative path points — NOT under <root>/models/...
    object_path = pipeline_directory / "models" / "factor.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_text(json.dumps(_OBJECT))

    yield object_path

    set_project_root(original)


def test_external_file_loads_pipeline_relative_path_from_project_root(nested_project) -> None:
    """Executor core: an externalFile whose ``path`` is relative loads the
    pipeline-dir object, with cwd at the project root.

    Pre-fix this failed with a file-not-found because the raw relative path
    resolved against cwd (``<root>/models/factor.json``, absent) inside
    ``content_hash`` instead of the pipeline dir
    (``<root>/rating/models/factor.json``) where the object lives.
    """
    node = _external_file_node(_RELATIVE_OBJECT_PATH)
    _, fn, is_source = _build_node_fn(node, source_names=["df"])

    assert is_source is False
    lf = pl.DataFrame({"x": [1, 3]}).lazy()
    df = fn(lf).collect()

    assert df["y"].to_list() == [10, 30]


def test_external_file_in_project_absolute_passthrough(nested_project) -> None:
    """An in-project ABSOLUTE object path loads and is NOT re-anchored to the
    pipeline dir — the anchoring step is a no-op for absolute paths."""
    object_path = nested_project  # <root>/rating/models/factor.json (absolute)

    node = _external_file_node(str(object_path))
    _, fn, _ = _build_node_fn(node, source_names=["df"])

    lf = pl.DataFrame({"x": [2]}).lazy()
    df = fn(lf).collect()

    assert df["y"].to_list() == [20]


def test_external_file_resolver_passes_out_of_cwd_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The anchoring step the externalFile builder now applies
    (``_resolve_runtime_data_path``) passes an out-of-cwd ABSOLUTE path through
    unchanged — the fix adds no new rejection.

    (Project-root containment for externalFile objects is still enforced
    independently by ``load_external_object`` → ``validate_project_path``; that
    pre-existing gate is untouched by this anchoring fix.)
    """
    from haute._builders import _resolve_runtime_data_path

    monkeypatch.chdir(tmp_path)
    (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating/main.py"\n')

    outside = tmp_path.parent / "elsewhere" / "models" / "factor.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps(_OBJECT))

    assert _resolve_runtime_data_path(str(outside)) == str(outside)
