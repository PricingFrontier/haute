"""The pipeline directory and client fixtures shared by the server suites.

The server route, WebSocket, file-watcher, submodel and infrastructure modules run against the
pipeline directory and client defined here, and several build a file input config or read the
document fingerprint header, so each imports them from this one place.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from tests._project import project_app, use_project
from tests.conftest import (
    build_test_input_snapshot,
    write_data_input_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _file_input_config(path: str) -> dict:
    return {
        "input_type": "file",
        "format": "parquet",
        "mode": "scan",
        "path": path,
        "arguments": {},
    }


@pytest.fixture()
def pipeline_dir(tmp_path: Path) -> Path:
    """Create a temporary project with a root-level pipeline and sample data."""
    use_project(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(data_dir / "input.parquet")

    # Use as_posix() to avoid Windows backslash escape issues in the
    # generated Python source (e.g. \U interpreted as unicode escape).
    data_path = (data_dir / "input.parquet").as_posix()
    source_config = write_data_input_config(tmp_path, "source", data_path)
    build_test_input_snapshot(_file_input_config(data_path), base_dir=tmp_path)

    code = f'''\
import haute
import polars as pl

pipeline = haute.Pipeline("test_pipeline", description="A test pipeline")


@pipeline.data_input(config="{source_config}")
def source():
    """Read data."""


@pipeline.polars
def transform(source: pl.DataFrame) -> pl.DataFrame:
    """Transform."""
    return source


pipeline.connect("source", "transform")
'''
    (tmp_path / "test_pipeline.py").write_text(code)
    return tmp_path


@pytest.fixture()
def client(pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient that runs with cwd set to the temp pipeline directory."""
    use_project(pipeline_dir)
    # Re-import to pick up cwd change
    app = project_app()

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/pipeline
# ---------------------------------------------------------------------------


DOCUMENT_FINGERPRINT_HEADER = "x-haute-document-fingerprint"
