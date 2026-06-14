"""Project-layout parser regressions for nested pipeline files and submodels."""

from __future__ import annotations

from pathlib import Path

from haute.parser import parse_pipeline_file
from tests.conftest import write_data_source_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_nested_pipeline_uses_file_relative_configs_and_project_relative_submodels(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "rating/main.py"\n',
        encoding="utf-8",
    )

    source_config = write_data_source_config(tmp_path / "rating", "raw_rows", "data/sample.parquet")
    _write(
        tmp_path / "modules" / "scoring.py",
        """\
import polars as pl
import haute

submodel = haute.Submodel("scoring")


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


@pipeline.data_source(config="{source_config}")
def raw_rows() -> pl.LazyFrame:
    return pl.scan_parquet("data/sample.parquet")


pipeline.submodel("modules/scoring.py")
""",
    )

    graph = parse_pipeline_file(tmp_path / "rating" / "main.py")

    node_ids = {node.id for node in graph.nodes}
    assert "raw_rows" in node_ids
    assert "submodel__scoring" in node_ids
    assert graph.submodels is not None
    assert graph.submodels["scoring"]["file"] == "modules/scoring.py"


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

submodel = haute.Submodel("root_scoring")


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

submodel = haute.Submodel("rating_scoring")


@submodel.data_source(config="config/data_source/rating_source.json")
def rating_score() -> pl.LazyFrame:
    return pl.scan_parquet("rating-data.parquet")
""",
    )
    _write(
        tmp_path / "rating" / "config" / "data_source" / "rating_source.json",
        '{"path": "rating-data.parquet"}',
    )
    _write(
        tmp_path / "rating" / "main.py",
        """\
import haute

pipeline = haute.Pipeline("nested_paths")
pipeline.submodel("modules/scoring.py")
""",
    )

    graph = parse_pipeline_file(tmp_path / "rating" / "main.py")

    assert graph.submodels is not None
    assert "rating_scoring" in graph.submodels
    assert "root_scoring" not in graph.submodels
    child = graph.submodels["rating_scoring"]["graph"]["nodes"][0]
    assert child["data"]["config"]["path"] == "rating-data.parquet"


def test_nested_pipeline_keeps_legacy_project_root_submodels_when_no_local_file(
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

submodel = haute.Submodel("legacy_scoring")


@submodel.polars
def score(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    return raw_rows
""",
    )
    _write(
        tmp_path / "rating" / "main.py",
        """\
import haute

pipeline = haute.Pipeline("nested_paths")
pipeline.submodel("modules/scoring.py")
""",
    )

    graph = parse_pipeline_file(tmp_path / "rating" / "main.py")

    assert graph.submodels is not None
    assert "legacy_scoring" in graph.submodels


def test_project_root_prefixed_pipeline_local_submodel_uses_pipeline_config_base(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "haute.toml").write_text(
        '[project]\npipeline = "rating/main.py"\n',
        encoding="utf-8",
    )

    _write(
        tmp_path / "config" / "data_source" / "source.json",
        '{"path": "root-data.parquet"}',
    )
    _write(
        tmp_path / "rating" / "config" / "data_source" / "source.json",
        '{"path": "rating-data.parquet"}',
    )
    _write(
        tmp_path / "rating" / "modules" / "scoring.py",
        """\
import polars as pl
import haute

submodel = haute.Submodel("scoring")


@submodel.data_source(config="config/data_source/source.json")
def source() -> pl.LazyFrame:
    return pl.scan_parquet("rating-data.parquet")
""",
    )
    _write(
        tmp_path / "rating" / "main.py",
        """\
import haute

pipeline = haute.Pipeline("nested_paths")
pipeline.submodel("rating/modules/scoring.py")
""",
    )

    graph = parse_pipeline_file(tmp_path / "rating" / "main.py")

    assert graph.submodels is not None
    child = graph.submodels["scoring"]["graph"]["nodes"][0]
    assert child["data"]["config"]["path"] == "rating-data.parquet"
