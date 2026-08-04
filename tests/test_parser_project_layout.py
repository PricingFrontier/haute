"""Project-layout parser regressions for nested pipeline files and submodels."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute.errors import ParseError
from haute.parser import parse_pipeline_file
from tests.conftest import write_data_input_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_nested_pipeline_uses_file_relative_configs_and_pipeline_relative_submodels(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "rating/main.py"\n',
        encoding="utf-8",
    )

    source_config = write_data_input_config(tmp_path / "rating", "raw_rows", "data/sample.parquet")
    _write(
        tmp_path / "rating" / "modules" / "scoring.py",
        """\
import polars as pl
import haute

submodel = haute.Submodel(
    "scoring",
    definition_id="scoring",
    input_ports=[],
    output_ports=[],
)


@submodel.polars
def score(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    return raw_rows.with_columns(pl.lit(1).alias("score"))
""",
    )
    _write(
        tmp_path / "rating" / "main.py",
        f"""\
import polars as pl
import haute

pipeline = haute.Pipeline("nested_paths")


@pipeline.data_input(config="{source_config}")
def raw_rows() -> pl.LazyFrame:
    return pl.scan_parquet("data/sample.parquet")


pipeline.submodel(
    "modules/scoring.py",
    definition_id="scoring",
    instance_id="submodel__scoring",
    alias="scoring",
)
""",
    )

    graph = parse_pipeline_file(tmp_path / "rating" / "main.py")

    node_ids = {node.id for node in graph.nodes}
    assert "raw_rows" in node_ids
    assert "submodel__scoring" in node_ids
    assert graph.submodels is not None
    assert graph.submodels["scoring"].file == "modules/scoring.py"


def test_nested_pipeline_prefers_pipeline_local_submodels(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "rating/main.py"\n',
        encoding="utf-8",
    )

    _write(
        tmp_path / "modules" / "scoring.py",
        """\
import polars as pl
import haute

submodel = haute.Submodel(
    "root_scoring",
    definition_id="root_scoring",
    input_ports=[],
    output_ports=[],
)


@submodel.polars
def root_score(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    return raw_rows
""",
    )
    _write(
        tmp_path / "rating" / "modules" / "scoring.py",
        """\
import polars as pl
import haute

submodel = haute.Submodel(
    "rating_scoring",
    definition_id="rating_scoring",
    input_ports=[],
    output_ports=[],
)


@submodel.data_input(config="config/data_input/rating_source.json")
def rating_score() -> pl.LazyFrame:
    return pl.scan_parquet("rating-data.parquet")
""",
    )
    write_data_input_config(
        tmp_path / "rating",
        "rating_source",
        "rating-data.parquet",
    )
    _write(
        tmp_path / "rating" / "main.py",
        """\
import haute

pipeline = haute.Pipeline("nested_paths")
pipeline.submodel(
    "modules/scoring.py",
    definition_id="rating_scoring",
    instance_id="submodel__rating_scoring",
    alias="rating_scoring",
)
""",
    )

    graph = parse_pipeline_file(tmp_path / "rating" / "main.py")

    assert graph.submodels is not None
    assert "rating_scoring" in graph.submodels
    assert "root_scoring" not in graph.submodels
    child = graph.submodels["rating_scoring"].graph.nodes[0]
    assert child.data.config["path"] == "rating-data.parquet"


def test_pipeline_rejects_submodel_outside_project_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    pipeline_file = tmp_path / "main.py"
    _write(
        pipeline_file,
        """\
import haute

pipeline = haute.Pipeline("main")
pipeline.submodel(
    "../outside.py",
    definition_id="outside",
    instance_id="submodel__outside",
    alias="outside",
)
""",
    )

    with pytest.raises(ParseError, match="escapes the project directory"):
        parse_pipeline_file(pipeline_file)


def test_pipeline_rejects_conflicting_definition_ids_for_one_file(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "shared.py", "")
    pipeline_file = tmp_path / "main.py"
    _write(
        pipeline_file,
        """\
import haute

pipeline = haute.Pipeline("main")
pipeline.submodel(
    "shared.py",
    definition_id="definition_one",
    instance_id="submodel__one",
    alias="one",
)
pipeline.submodel(
    "shared.py",
    definition_id="definition_two",
    instance_id="submodel__two",
    alias="two",
)
""",
    )

    with pytest.raises(ParseError, match="conflicting definition ids"):
        parse_pipeline_file(pipeline_file)


def test_pipeline_rejects_one_definition_id_for_multiple_files(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    submodel_source = """\
import haute

submodel = haute.Submodel(
    "shared",
    definition_id="shared",
    input_ports=[],
    output_ports=[],
)
"""
    _write(tmp_path / "first.py", submodel_source)
    _write(tmp_path / "second.py", submodel_source)
    pipeline_file = tmp_path / "main.py"
    _write(
        pipeline_file,
        """\
import haute

pipeline = haute.Pipeline("main")
pipeline.submodel(
    "first.py",
    definition_id="shared",
    instance_id="submodel__first",
    alias="first",
)
pipeline.submodel(
    "second.py",
    definition_id="shared",
    instance_id="submodel__second",
    alias="second",
)
""",
    )

    with pytest.raises(ParseError, match="definition id resolves to multiple files"):
        parse_pipeline_file(pipeline_file)
