"""A module docstring the analyst wrote is kept on save (READ-02).

Spec: specs/codegen/high-level.md, the module docstring.
"""

from __future__ import annotations

from pathlib import Path

from haute._scaffold import starter_pipeline
from haute.codegen import keep_module_docstring
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests.conftest import current_source_revision

_HAND_WRITTEN = (
    '"""Motor rating for the 2027 renewal book.\n'
    "\n"
    "Owner: pricing team.  Two spaces, a tab\there, and a trailing space \n"
    '"""\n'
)

_BODY = (
    "\n"
    "import haute\n"
    "import polars as pl\n"
    "\n"
    'pipeline = haute.Pipeline("main")\n'
    "\n"
    "\n"
    "@pipeline.polars\n"
    "def data() -> pl.LazyFrame:\n"
    '    df = pl.LazyFrame({"x": [1]})\n'
    "    return df\n"
)


def _save(project: Path, source_file: str = "main.py") -> None:
    path = project / source_file
    graph = parse_pipeline_file(path)
    SavePipelineService(project_root=project).save(
        SavePipelineRequest(
            graph=graph,
            name=graph.pipeline_name or "main",
            description=graph.pipeline_description or "",
            source_file=source_file,
            base_revision=current_source_revision(path, project),
        )
    )


def test_a_hand_written_docstring_survives_a_save_byte_for_byte(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_bytes((_HAND_WRITTEN + _BODY).encode("utf-8"))

    _save(tmp_path)

    saved = (tmp_path / "main.py").read_bytes().decode("utf-8")
    assert saved.startswith(_HAND_WRITTEN)
    assert "Pipeline: main" not in saved


def test_codegen_writes_its_docstring_for_a_file_with_none(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_bytes(_BODY.lstrip("\n").encode("utf-8"))

    _save(tmp_path)

    assert (tmp_path / "main.py").read_text(encoding="utf-8").startswith('"""Pipeline: main"""\n')


def test_a_fresh_scaffold_saves_with_its_docstring_unchanged(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_bytes(starter_pipeline("main").encode("utf-8"))
    before = (tmp_path / "main.py").read_text(encoding="utf-8")

    _save(tmp_path)

    after = (tmp_path / "main.py").read_text(encoding="utf-8")
    assert after.split("\n", 1)[0] == before.split("\n", 1)[0] == '"""Pipeline: main"""'
    assert after == before


def test_keep_module_docstring_replaces_only_the_header() -> None:
    generated = '"""Pipeline: main"""\n\nimport haute\n'
    assert (
        keep_module_docstring(generated, '"""Mine,\nkept."""\n\nimport haute\n')
        == '"""Mine,\nkept."""\n\nimport haute\n'
    )
    assert keep_module_docstring(generated, "import haute\n") == generated
    assert keep_module_docstring(generated, "def broken(:\n") == generated
