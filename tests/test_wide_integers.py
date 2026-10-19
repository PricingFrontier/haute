"""A 128-bit integer is refused where Haute can name it, before pyarrow meets it (BUG-37).

Polars 2 makes ``Int128`` from ``Int64`` or ``Int32`` combined with ``UInt64``. pyarrow
cannot read the footer of a Parquet file holding one, and its error names no column, so
Haute refuses such a column at the node that makes it and at every Parquet write and read,
with the cast that fixes it (specs/io-layer, "Polars 2 dtypes").
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from haute._chunked_writes import part_paths, write_parts
from haute._mlflow_io import ScoringModel
from haute._model_scorer import ScoreOutputDestination, _batch_score_to_parquet
from haute._polars_io_registry import write_polars_output
from haute._polars_utils import bounded_sink, read_parquet_metadata
from haute.errors import SchemaMismatchError
from haute.executor import execute_graph
from tests.conftest import make_edge, make_graph, make_source_node, make_transform_node

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

_KEYS = pl.DataFrame(
    {"a": pl.Series([1, 2], dtype=pl.Int64), "b": pl.Series([3, 4], dtype=pl.UInt64)}
)
_SUM = "df = keys.with_columns(c=pl.col('a') + pl.col('b'))"
_REFUSED = r"Column 'c' is Int128, a 128-bit integer .* cast it upstream to Int64"


def _wide() -> pl.LazyFrame:
    return _KEYS.lazy().with_columns(c=pl.col("a") + pl.col("b"))


def test_polars_2_makes_a_128_bit_integer_whose_parquet_pyarrow_cannot_read(
    tmp_path: Path,
) -> None:
    """The reason for the refusals: pyarrow's error comes from the footer, naming nothing."""
    path = tmp_path / "wide.parquet"
    _wide().collect().write_parquet(path)

    assert _wide().collect_schema()["c"] == pl.Int128
    with pytest.raises(Exception, match="Integers with more than 64 bits not implemented"):
        pq.read_metadata(path)


def test_a_preview_refuses_the_node_that_makes_one(tmp_path: Path) -> None:
    source = tmp_path / "keys.parquet"
    _KEYS.write_parquet(source)
    node = make_source_node("keys", str(source))
    node.data.config["format"] = "parquet"
    node.data.config["mode"] = "scan"
    graph = make_graph(
        {
            "nodes": [node.model_dump(), make_transform_node("total", _SUM).model_dump()],
            "edges": [make_edge("keys", "total").model_dump()],
        }
    )

    # A schema mismatch is the run's, not the node's preview: it stops the preview.
    with pytest.raises(SchemaMismatchError, match=_REFUSED) as refused:
        execute_graph(graph, target_node_id="total")

    assert refused.value.context == {"column": "c", "node": "total"}


def test_haute_writes_no_parquet_holding_one(tmp_path: Path) -> None:
    with pytest.raises(SchemaMismatchError, match=_REFUSED) as refused:
        write_parts(tmp_path, _wide(), node_id="total")
    assert refused.value.context == {"column": "c", "node": "total"}
    assert part_paths(tmp_path) == []
    with pytest.raises(SchemaMismatchError, match=_REFUSED):
        bounded_sink(_wide(), tmp_path / "out.parquet")
    assert not (tmp_path / "out.parquet").exists()

    # Polars reads a 128-bit integer back from CSV, so a CSV write keeps one.
    bounded_sink(_wide(), tmp_path / "out.csv", fmt="csv")

    assert pl.read_csv(tmp_path / "out.csv")["c"].to_list() == [4, 6]


@pytest.mark.parametrize(("format_name", "suffix"), [("parquet", "parquet")])
def test_a_data_output_refuses_one_in_a_format_written_through_arrow(
    tmp_path: Path, format_name: str, suffix: str
) -> None:
    config = {
        "outputType": "file",
        "format": format_name,
        "mode": "sink",
        "path": f"out.{suffix}",
        "arguments": {},
    }

    with pytest.raises(SchemaMismatchError, match=_REFUSED) as refused:
        write_polars_output(
            _wide(), config, resolved_path=str(tmp_path / f"out.{suffix}"), node_id="out"
        )

    assert refused.value.context["node"] == "out"
    assert not (tmp_path / f"out.{suffix}").exists()


def test_a_data_output_keeps_one_in_a_format_polars_writes_itself(tmp_path: Path) -> None:
    config = {
        "outputType": "file",
        "format": "csv",
        "mode": "sink",
        "path": "out.csv",
        "arguments": {},
    }

    write_polars_output(_wide(), config, resolved_path=str(tmp_path / "out.csv"), node_id="out")

    assert pl.read_csv(tmp_path / "out.csv")["c"].to_list() == [4, 6]


def test_a_map_holding_one_in_its_values_is_refused_at_a_write(tmp_path: Path) -> None:
    held = pl.LazyFrame(
        {"m": pl.Series([{"a": 1}], dtype=pl.Map(pl.String, pl.Int128))},
    )

    with pytest.raises(SchemaMismatchError, match="Column 'm' is Map") as refused:
        write_parts(tmp_path, held, node_id="maps")

    assert refused.value.context == {"column": "m", "node": "maps"}
    assert part_paths(tmp_path) == []


class _OneScore:
    """A model scoring every row 1.0, enough for the batched scorer's writes."""

    feature_names_: list[str] = ["a"]  # noqa: RUF012

    def predict(self, x_data: object) -> list[float]:
        return [1.0] * len(x_data)  # type: ignore[arg-type]


def _scoring_model() -> ScoringModel:
    return ScoringModel(
        model=_OneScore(), feature_names=["a"], cat_feature_names=frozenset(), flavor="catboost"
    )


@pytest.mark.parametrize("rows", [2, 0], ids=["scored", "empty"])
def test_the_batched_scorer_writes_no_part_holding_one(tmp_path: Path, rows: int) -> None:
    """A passthrough column the model never reads still reaches the scored parts."""
    scored = pl.LazyFrame(
        {"a": pl.Series([1.0, 2.0][:rows]), "b": pl.Series([3, 4][:rows], dtype=pl.UInt64)}
    ).with_columns(c=pl.col("a").cast(pl.Int64) + pl.col("b"))
    out = tmp_path / "parts"

    with pytest.raises(SchemaMismatchError, match=_REFUSED):
        _batch_score_to_parquet(
            _scoring_model(),
            scored,
            ["a"],
            "pred",
            "regression",
            destination=ScoreOutputDestination(directory=out),
        )

    assert not out.exists() or part_paths(out) == []


def test_the_batched_scorer_names_one_in_its_parquet_input(tmp_path: Path) -> None:
    path = tmp_path / "input.parquet"
    _wide().collect().write_parquet(path)

    with pytest.raises(SchemaMismatchError, match=_REFUSED) as refused:
        _batch_score_to_parquet(_scoring_model(), str(path), ["a"], "pred", "regression")

    assert refused.value.context["path"] == str(path)


def test_a_staging_copy_only_polars_reads_back_keeps_one(tmp_path: Path) -> None:
    write_parts(tmp_path, _wide(), node_id="total", internal=True)

    assert pl.scan_parquet(part_paths(tmp_path)).collect()["c"].dtype == pl.Int128


def test_a_file_that_is_not_parquet_is_reported_as_before(tmp_path: Path) -> None:
    """The RAM estimate treats pyarrow's ``ValueError`` as an unknown source."""
    path = tmp_path / "quotes.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Parquet magic bytes"):
        read_parquet_metadata(path)


def test_reading_a_file_that_holds_one_names_its_column(tmp_path: Path) -> None:
    path = tmp_path / "wide.parquet"
    _wide().collect().write_parquet(path)

    with pytest.raises(SchemaMismatchError, match=_REFUSED) as refused:
        read_parquet_metadata(path)

    assert refused.value.context["path"] == str(path)
