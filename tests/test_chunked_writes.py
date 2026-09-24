"""Tests for chunked writes and join recipes."""

from __future__ import annotations

import datetime
import random
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq
import pytest
from polars.io.plugins import register_io_source

import haute._chunked_writes
from haute._chunked_writes import (
    SINGLE_FILE_ROW_GROUP_SIZE,
    JoinRecipe,
    RecipeEquivalenceError,
    WriteRecipe,
    is_part_name,
    part_name,
    part_paths,
    scan_parts,
    sliceable,
    write_file,
    write_parts,
)
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._hashing import content_hash
from haute._polars_utils import (
    bounded_sink,
    current_streaming_chunk_size,
    set_streaming_chunk_size,
)
from haute.chunking import classify_chunk_local_polars_code


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "a": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "b": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            "k": ["x", "x", "y", "y", "z", "z", "w", "w", "v", "v"],
            "n": [1, None, 3, None, 5, None, 7, None, 9, None],
            "s": [{"sub": i} for i in range(1, 11)],
            "lst": [[1, 2], [3], [4, 5], [1], [6], [7, 8], [9], [2], [10], [11]],
            "x": list(range(10)),
        }
    )


def _make_join_frames() -> tuple[pl.DataFrame, pl.DataFrame]:
    rnd = random.Random(42)
    keys = ["a", "b", "c", None, "h"]
    base = pl.DataFrame(
        {
            "k": [rnd.choice(keys) for _ in range(20)],
            "k2": [rnd.choice([1, 2, None]) for _ in range(20)],
            "x": list(range(20)),
            "v": [float(i) for i in range(20)],
        }
    )
    join = pl.DataFrame(
        {
            "k": [rnd.choice(keys + ["z"]) for _ in range(12)] + ["h"] * 8,
            "k2": [rnd.choice([1, 2, None]) for _ in range(20)],
            "y": list(range(20)),
            "v": [float(i * 2) for i in range(20)],
        }
    )
    return base, join


TRANSFORM_CASES: list[tuple[str, Callable[[pl.LazyFrame], pl.LazyFrame] | None, bool | None]] = [
    ("with_columns_elementwise", lambda lf: lf.with_columns(c=pl.col("a") + pl.col("b")), True),
    ("select_expr", lambda lf: lf.select(pl.col("a") * 2, pl.col("b") - 1), True),
    ("cast", lambda lf: lf.with_columns(pl.col("a").cast(pl.Float64)), True),
    ("rename", lambda lf: lf.rename({"a": "a2"}), True),
    ("drop", lambda lf: lf.drop("b"), True),
    ("unnest", lambda lf: lf.unnest("s"), True),
    ("limit", lambda lf: lf.limit(5), True),
    ("broadcast_sum", lambda lf: lf.with_columns(total=pl.col("a").sum()), False),
    ("over", lambda lf: lf.with_columns(s=pl.col("a").sum().over("k")), False),
    ("shift", lambda lf: lf.with_columns(s=pl.col("a").shift(1)), False),
    ("cum_sum", lambda lf: lf.with_columns(s=pl.col("a").cum_sum()), False),
    ("rank", lambda lf: lf.with_columns(s=pl.col("a").rank()), False),
    (
        "fill_null_forward",
        lambda lf: lf.with_columns(pl.col("n").fill_null(strategy="forward")),
        False,
    ),
    ("filter", lambda lf: lf.filter(pl.col("a") > 2), False),
    ("explode", lambda lf: lf.explode("lst"), False),
    ("join", lambda lf: lf.join(lf, on="a"), False),
    ("group_by_agg", lambda lf: lf.group_by("k").agg(pl.col("a").sum()), False),
    ("unique", lambda lf: lf.unique(), False),
    ("sort", lambda lf: lf.sort("a"), False),
    ("concat", lambda lf: pl.concat([lf, lf]), False),
    ("csv_scan", None, False),
    ("python_io_source", None, False),
    ("row_index_parquet", None, True),
    ("row_index_memory_after_select", None, None),
]


@pytest.mark.parametrize("case_name,op,expected", TRANSFORM_CASES)
def test_sliceable_is_decided_by_polars_slice_pushdown(
    tmp_path: Path,
    case_name: str,
    op: Callable[[pl.LazyFrame], pl.LazyFrame] | None,
    expected: bool | None,
) -> None:
    df = _sample_df()
    parquet_path = tmp_path / "base.parquet"
    if not parquet_path.exists():
        df.write_parquet(parquet_path)

    if case_name == "csv_scan":
        csv_path = tmp_path / "sample.csv"
        df.drop("s", "lst").write_csv(csv_path)
        csv_lf = pl.scan_csv(csv_path)
        assert sliceable(csv_lf) is False
        return

    if case_name == "python_io_source":
        simple_df = df.drop("s", "lst")
        source_lf = register_io_source(
            lambda with_columns, predicate, n_rows, batch_size: iter([simple_df]),
            schema=simple_df.schema,
        )
        assert sliceable(source_lf) is False
        return

    if case_name == "row_index_parquet":
        lf = pl.scan_parquet(parquet_path).with_row_index()
        assert sliceable(lf) is True
        whole = lf.collect()
        sliced = lf.slice(3, 4).collect()
        assert sliced["index"].to_list() == whole["index"].slice(3, 4).to_list()
        assert sliced.equals(whole.slice(3, 4))
        return

    if case_name == "row_index_memory_after_select":
        lf = df.lazy().select("a", "b").with_row_index()
        is_sl = sliceable(lf)
        whole = lf.collect()
        sliced = lf.slice(2, 3).collect()
        if is_sl:
            assert sliced["index"].to_list() == whole["index"].slice(2, 3).to_list()
            assert sliced.equals(whole.slice(2, 3))
        return

    assert op is not None
    # Parquet scan
    scan_lf = op(pl.scan_parquet(parquet_path))
    assert sliceable(scan_lf) is expected, f"Parquet scan failed for {case_name}"

    # In-memory lazyframe
    mem_lf = op(df.lazy())
    assert sliceable(mem_lf) is expected, f"In-memory lazyframe failed for {case_name}"


def test_sliced_iteration_of_an_absorbing_operator_is_never_used(tmp_path: Path) -> None:
    df = _sample_df()
    parquet_path = tmp_path / "base.parquet"
    df.write_parquet(parquet_path)

    operators: list[tuple[str, Callable[[pl.LazyFrame], pl.LazyFrame]]] = [
        ("unique", lambda lf: lf.unique()),
        ("group_by", lambda lf: lf.group_by("k").len()),
        ("sort", lambda lf: lf.sort("x")),
    ]

    count = 0
    for name, op in operators:
        for source_type, base_lf in [
            ("scan", pl.scan_parquet(parquet_path)),
            ("mem", df.lazy()),
        ]:
            count += 1
            frame = op(base_lf)
            assert sliceable(frame) is False, f"Expected not sliceable for {name}_{source_type}"

            target = tmp_path / f"write_{count}"
            target.mkdir()
            res = write_parts(target, frame, chunk_rows=3)
            assert res.strategy == "native"

            parts = part_paths(target)
            got = scan_parts(parts).collect()
            expected = frame.collect()
            assert got.sort(got.columns, nulls_last=True).equals(
                expected.sort(expected.columns, nulls_last=True)
            )
            assert got.height == expected.height


def test_sliced_write_equals_native_write_and_splits_into_parts(tmp_path: Path) -> None:
    df = _sample_df()
    parquet_path = tmp_path / "base.parquet"
    df.write_parquet(parquet_path)

    frame = pl.scan_parquet(parquet_path).with_columns(c=pl.col("a") * 2)
    target = tmp_path / "parts_out"
    target.mkdir()
    res = write_parts(target, frame, chunk_rows=3)

    assert res.strategy == "sliced"
    assert res.parts == (
        "part-00000.parquet",
        "part-00001.parquet",
        "part-00002.parquet",
        "part-00003.parquet",
    )

    parts = part_paths(target)
    assert [p.name for p in parts] == list(res.parts)

    got = scan_parts(parts).collect()
    expected = frame.collect()
    assert got.equals(expected)

    for p in parts:
        assert pl.read_parquet(p).schema == frame.collect_schema()


def test_empty_result_writes_one_schema_part(tmp_path: Path) -> None:
    # 1. Empty sliceable frame
    empty_df = pl.DataFrame({"a": [1], "b": ["x"]}).clear()
    empty_file = tmp_path / "empty.parquet"
    empty_df.write_parquet(empty_file)

    empty_frame = pl.scan_parquet(empty_file).with_columns(c=pl.col("a") + 1)
    target_a = tmp_path / "empty_frame"
    target_a.mkdir()
    res_a = write_parts(target_a, empty_frame, chunk_rows=5)

    assert res_a.chunks == 1
    parts_a = part_paths(target_a)
    assert len(parts_a) == 1
    assert parts_a[0].name == "part-00000.parquet"
    got_a = scan_parts(parts_a).collect()
    assert got_a.height == 0
    assert got_a.schema == empty_frame.collect_schema()

    # 2. Empty join result
    base = pl.DataFrame({"k": ["x"], "v": [1]}).clear().lazy()
    join = pl.DataFrame({"k": ["x"], "w": [2]}).clear().lazy()
    recipe = JoinRecipe(base, join, {"how": "inner", "on": "k"})

    target_b = tmp_path / "empty_join"
    target_b.mkdir()
    res_b = write_parts(target_b, recipe.native(), join=recipe, chunk_rows=5)

    assert res_b.chunks == 1
    parts_b = part_paths(target_b)
    assert len(parts_b) == 1
    assert parts_b[0].name == "part-00000.parquet"
    got_b = scan_parts(parts_b).collect()
    assert got_b.height == 0
    assert got_b.schema == recipe.native().collect_schema()


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, "part-00000.parquet"),
        (99_999, "part-99999.parquet"),
        (100_000, "part-100000.parquet"),
    ],
)
def test_part_names_are_canonical_and_unbounded(index: int, expected: str) -> None:
    assert part_name(index) == expected
    assert is_part_name(expected)


@pytest.mark.parametrize(
    "value",
    [-1, True, False, 1.0, "1"],
)
def test_part_name_rejects_non_canonical_indices(value: object) -> None:
    with pytest.raises(ValueError, match="part index"):
        part_name(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    [
        "part-0000.parquet",
        "part-000000.parquet",
        "part-+0001.parquet",
        "part--0001.parquet",
        "part-１２３４５.parquet",
        "part-0000a.parquet",
    ],
)
def test_part_name_rejects_non_canonical_spellings(name: str) -> None:
    assert not is_part_name(name)


def test_part_paths_filters_invalid_names_and_orders_by_numeric_index(tmp_path: Path) -> None:
    for name in [
        "part-100001.parquet",
        "part-99999.parquet",
        "part-100000.parquet",
        "part-99998.parquet",
        "part-000000.parquet",
        "part-１２３４５.parquet",
    ]:
        (tmp_path / name).touch()

    assert [path.name for path in part_paths(tmp_path)] == [
        "part-99998.parquet",
        "part-99999.parquet",
        "part-100000.parquet",
        "part-100001.parquet",
    ]


@pytest.mark.parametrize("name", ["part-99999.parquet", "part-100000.parquet"])
def test_source_cache_part_accepts_unbounded_canonical_part_names(name: str) -> None:
    from haute._source_cache import SourceCachePart

    part = SourceCachePart.from_dict(
        {"name": name, "size_bytes": 0, "digest": "0" * 16, "row_count": 0}
    )
    assert part.name == name


def test_chunked_join_equals_native_join(tmp_path: Path) -> None:
    base, join = _make_join_frames()
    key_cases = [
        ("on", "k"),
        ("on", ["k", "k2"]),
        ("lr", ("k", "k")),
        ("lr", (["k", "k2"], ["k", "k2"])),
    ]
    hows = ["inner", "left", "right", "full", "semi", "anti", "cross"]
    coalesce_options = [None, True, False]
    chunk_rows_list = [1, 7, 1000]

    target = tmp_path / "join_run"
    target.mkdir()

    for how in hows:
        effective_key_cases = [("none", None)] if how == "cross" else key_cases
        for k_type, k_val in effective_key_cases:
            for coalesce in coalesce_options:
                cfg: dict[str, Any] = {"how": how, "suffix": "_r"}
                if how != "cross":
                    if k_type == "on":
                        cfg["on"] = k_val
                    else:
                        cfg["leftOn"], cfg["rightOn"] = k_val
                if coalesce is not None:
                    cfg["coalesce"] = coalesce

                recipe = JoinRecipe(base.lazy(), join.lazy(), cfg)
                native = recipe.native().collect()
                expected_sorted = native.sort(native.columns, nulls_last=True)

                for chunk_rows in chunk_rows_list:
                    for f in target.iterdir():
                        f.unlink()

                    res = write_parts(target, recipe.native(), join=recipe, chunk_rows=chunk_rows)
                    assert res.strategy == "chunked_join"

                    got = scan_parts(part_paths(target)).collect()
                    assert got.schema == native.schema, (
                        f"Schema mismatch for {cfg} chunk={chunk_rows}"
                    )

                    got_sorted = got.sort(got.columns, nulls_last=True)
                    assert got_sorted.equals(expected_sorted), (
                        f"Row mismatch for {cfg} chunk={chunk_rows}"
                    )


@pytest.mark.parametrize(
    ("base_extra", "lookup_extra"),
    [
        (["__haute_chunk_matches"], []),
        (["__haute_chunk_matches", "__haute_chunk_matches_0"], ["__haute_chunk_matches"]),
    ],
)
def test_chunked_join_match_count_name_avoids_both_input_schemas(
    tmp_path: Path, base_extra: list[str], lookup_extra: list[str]
) -> None:
    base_data: dict[str, list[object]] = {"base_key": ["a", "b"], "left": [1, 2]}
    lookup_data: dict[str, list[object]] = {
        "lookup_key": ["a", "a", "b"],
        "right": [10, 11, 12],
    }
    for name in base_extra:
        base_data[name] = [f"base-{name}", f"base-{name}"]
    for name in lookup_extra:
        lookup_data[name] = [f"lookup-{name}"] * 3

    recipe = JoinRecipe(
        pl.DataFrame(base_data).lazy(),
        pl.DataFrame(lookup_data).lazy(),
        {"how": "inner", "leftOn": "base_key", "rightOn": "lookup_key"},
    )
    target = tmp_path / "count_name"
    target.mkdir()

    write_parts(target, recipe.native(), join=recipe, chunk_rows=2)
    actual = scan_parts(part_paths(target)).collect()
    expected = recipe.native().collect()
    assert actual.sort(actual.columns).equals(expected.sort(expected.columns))


def test_chunked_join_heavy_split_uses_collision_free_match_count_name(tmp_path: Path) -> None:
    recipe = JoinRecipe(
        pl.DataFrame(
            {
                "base_key": ["a"],
                "__haute_chunk_matches": ["base"],
                "__haute_chunk_matches_0": ["base-0"],
            }
        ).lazy(),
        pl.DataFrame(
            {
                "lookup_key": ["a"] * 5,
                "__haute_chunk_matches": ["lookup"] * 5,
                "value": list(range(5)),
            }
        ).lazy(),
        {"how": "inner", "leftOn": "base_key", "rightOn": "lookup_key"},
    )
    target = tmp_path / "heavy_count_name"
    target.mkdir()

    write_parts(target, recipe.native(), join=recipe, chunk_rows=2)
    actual = scan_parts(part_paths(target)).collect()
    expected = recipe.native().collect()
    assert actual.sort(actual.columns).equals(expected.sort(expected.columns))


def test_chunked_validation_accepts_match_count_named_key_when_unique(tmp_path: Path) -> None:
    recipe = JoinRecipe(
        pl.DataFrame({"__haute_chunk_matches": ["a", "b"], "left": [1, 2]}).lazy(),
        pl.DataFrame({"__haute_chunk_matches": ["a", "b"], "right": [3, 4]}).lazy(),
        {"how": "inner", "on": "__haute_chunk_matches", "validate": "1:1"},
    )
    target = tmp_path / "validation_success"
    target.mkdir()

    write_parts(target, recipe.native(), join=recipe, chunk_rows=1)
    actual = scan_parts(part_paths(target)).collect()
    expected = recipe.native().collect()
    assert actual.equals(expected)


def test_chunked_validation_failure_matches_native_for_match_count_named_key(
    tmp_path: Path,
) -> None:
    recipe = JoinRecipe(
        pl.DataFrame({"__haute_chunk_matches": ["a", "a"], "left": [1, 2]}).lazy(),
        pl.DataFrame({"__haute_chunk_matches": ["a"], "right": [3]}).lazy(),
        {"how": "inner", "on": "__haute_chunk_matches", "validate": "1:m"},
    )
    target = tmp_path / "validation_failure"
    target.mkdir()

    with pytest.raises(pl.exceptions.ComputeError) as native_error:
        recipe.native().collect()
    with pytest.raises(pl.exceptions.ComputeError) as chunked_error:
        write_parts(target, recipe.native(), join=recipe, chunk_rows=1)
    assert str(chunked_error.value) == str(native_error.value)


def test_budgeted_validated_join_uses_one_native_sink_when_it_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = JoinRecipe(
        pl.DataFrame({"k": ["a", "a"], "left": [1, 2]}).lazy(),
        pl.DataFrame({"k": ["a"], "right": [3]}).lazy(),
        {"how": "inner", "on": "k", "validate": "m:1"},
    )
    context = ExecutionContext(
        operation="budgeted-join",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=512 * 1024 * 1024,
        memory_sampler=lambda: 0,
    )
    sinks: list[pl.LazyFrame] = []
    original_sink = haute._chunked_writes._Parts.sink

    def track_sink(self: Any, frame: pl.LazyFrame, **kwargs: Any) -> None:
        sinks.append(frame)
        original_sink(self, frame, **kwargs)

    monkeypatch.setattr(haute._chunked_writes._Parts, "sink", track_sink)
    target = tmp_path / "native_join"
    target.mkdir()

    result = write_parts(
        target, recipe.native(), join=recipe, chunk_rows=4, execution_context=context
    )

    assert result.strategy == "native"
    assert result.native_reason == "join_within_memory_budget"
    assert result.chunk_rows is None
    assert len(sinks) == 1
    assert scan_parts(part_paths(target)).collect().equals(recipe.native().collect())


def test_budgeted_join_stays_chunked_when_native_allowance_is_insufficient(tmp_path: Path) -> None:
    base_path = tmp_path / "base.parquet"
    join_path = tmp_path / "join.parquet"
    pl.DataFrame({"k": ["a", "a"], "left": [1, 2]}).write_parquet(base_path)
    pl.DataFrame({"k": ["a"], "right": [3]}).write_parquet(join_path)
    recipe = JoinRecipe(
        pl.scan_parquet(base_path),
        pl.scan_parquet(join_path),
        {"how": "inner", "on": "k", "validate": "m:1"},
    )
    context = ExecutionContext(
        operation="small-budget-join",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=100,
        memory_sampler=lambda: 0,
    )
    target = tmp_path / "chunked_join"
    target.mkdir()

    result = write_parts(
        target, recipe.native(), join=recipe, chunk_rows=4, execution_context=context
    )

    assert result.strategy == "chunked_join"
    assert result.chunk_rows == 1


def test_budgeted_row_local_recipe_counts_its_wide_output_for_parts_and_file(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "input.parquet"
    pl.DataFrame({"i0": list(range(20_000))}).write_parquet(source_path)
    source = pl.scan_parquet(source_path)
    recipe = WriteRecipe(
        input=source,
        fn=lambda frame: frame.with_columns(wide=pl.lit("x" * 4096)),
    )
    frame = recipe.native()
    context = ExecutionContext(
        operation="wide-recipe",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=8 * 1024 * 1024,
        memory_sampler=lambda: 0,
    )
    parts_target = tmp_path / "parts"
    parts_target.mkdir()
    file_target = tmp_path / "single.parquet"

    parts = write_parts(
        parts_target,
        frame,
        recipe=recipe,
        chunk_rows=20_000,
        execution_context=context,
    )
    single = write_file(
        file_target,
        frame,
        recipe=recipe,
        chunk_rows=20_000,
        execution_context=context,
    )

    assert 1 <= parts.chunk_rows < 20_000
    assert single.chunk_rows == parts.chunk_rows
    assert scan_parts(part_paths(parts_target)).collect().equals(frame.collect())
    assert pl.read_parquet(file_target).equals(frame.collect())


def test_unvalidated_custom_and_ordered_joins_stay_on_the_chunked_path(tmp_path: Path) -> None:
    context = ExecutionContext(
        operation="excluded-joins",
        profile=ExecutionProfile.LAZY_SINK,
        memory_limit_bytes=512 * 1024 * 1024,
        memory_sampler=lambda: 0,
    )
    recipes = [
        JoinRecipe(
            pl.DataFrame({"k": ["a"], "left": [1]}).lazy(),
            pl.DataFrame({"k": ["a"], "right": [2]}).lazy(),
            {"how": "inner", "on": "k"},
        ),
        JoinRecipe(
            pl.DataFrame({"k": ["a"], "left": [1]}).lazy(),
            pl.DataFrame({"k": ["a"], "right": [2]}).lazy(),
            {"how": "inner", "on": "k", "validate": "1:1", "maintainOrder": "left"},
        ),
        JoinRecipe(
            pl.DataFrame({"k": ["a"], "left": [1]}).lazy(),
            pl.DataFrame({"k": ["a"], "right": [2]}).lazy(),
            {"how": "inner", "on": "k", "validate": "1:1"},
            finish=lambda frame: frame.select(pl.all()),
        ),
    ]
    for index, recipe in enumerate(recipes):
        target = tmp_path / f"excluded_{index}"
        target.mkdir()
        result = write_parts(
            target, recipe.native(), join=recipe, chunk_rows=4, execution_context=context
        )
        assert result.strategy == "chunked_join"


def test_validate_rejections_match_polars(tmp_path: Path) -> None:
    base, join = _make_join_frames()

    # 1. right/semi/anti with validate 1:m, m:1, 1:1
    target = tmp_path / "val_dir"
    target.mkdir()

    for how in ["right", "semi", "anti"]:
        for v in ["1:m", "m:1", "1:1"]:
            for f in target.iterdir():
                f.unlink()
            cfg = {"how": how, "on": "k", "validate": v}
            recipe = JoinRecipe(base.lazy(), join.lazy(), cfg)
            with pytest.raises(pl.exceptions.ComputeError) as exc_native:
                recipe.native().collect_schema()
            expected_msg = str(exc_native.value)

            with pytest.raises(pl.exceptions.ComputeError) as exc_chunk:
                write_parts(target, recipe.native(), join=recipe, chunk_rows=5)
            assert str(exc_chunk.value) == expected_msg
            assert not part_paths(target)

    # 2. cross with validate "1:1" ignores it and equals native
    for f in target.iterdir():
        f.unlink()
    cross_cfg = {"how": "cross", "validate": "1:1"}
    cross_recipe = JoinRecipe(base.lazy(), join.lazy(), cross_cfg)
    write_parts(target, cross_recipe.native(), join=cross_recipe, chunk_rows=5)
    cross_got = scan_parts(part_paths(target)).collect()
    assert cross_got.sort(cross_got.columns, nulls_last=True).equals(
        cross_recipe.native().collect().sort(cross_got.columns, nulls_last=True)
    )

    # 3. inner/left/full with duplicated key on checked side
    base_dup = pl.DataFrame({"k": ["a", "a", "b"], "x": [1, 2, 3]})
    join_dup = pl.DataFrame({"k": ["a", "b", "b"], "y": [1, 2, 3]})
    base_uniq = pl.DataFrame({"k": ["a", "b", "c"], "x": [1, 2, 3]})
    join_uniq = pl.DataFrame({"k": ["a", "b", "c"], "y": [1, 2, 3]})

    for how in ["inner", "left", "full"]:
        # 1:m checks base
        rec_1m = JoinRecipe(
            base_dup.lazy(), join_uniq.lazy(), {"how": how, "on": "k", "validate": "1:m"}
        )
        with pytest.raises(
            pl.exceptions.ComputeError, match=r"join keys did not fulfill 1:m validation"
        ):
            for f in target.iterdir():
                f.unlink()
            write_parts(target, rec_1m.native(), join=rec_1m, chunk_rows=2)

        # m:1 checks join
        rec_m1 = JoinRecipe(
            base_uniq.lazy(), join_dup.lazy(), {"how": how, "on": "k", "validate": "m:1"}
        )
        with pytest.raises(
            pl.exceptions.ComputeError, match=r"join keys did not fulfill m:1 validation"
        ):
            for f in target.iterdir():
                f.unlink()
            write_parts(target, rec_m1.native(), join=rec_m1, chunk_rows=2)

        # 1:1 checks both
        rec_11_base = JoinRecipe(
            base_dup.lazy(), join_uniq.lazy(), {"how": how, "on": "k", "validate": "1:1"}
        )
        with pytest.raises(
            pl.exceptions.ComputeError, match=r"join keys did not fulfill 1:1 validation"
        ):
            for f in target.iterdir():
                f.unlink()
            write_parts(target, rec_11_base.native(), join=rec_11_base, chunk_rows=2)

        rec_11_join = JoinRecipe(
            base_uniq.lazy(), join_dup.lazy(), {"how": how, "on": "k", "validate": "1:1"}
        )
        with pytest.raises(
            pl.exceptions.ComputeError, match=r"join keys did not fulfill 1:1 validation"
        ):
            for f in target.iterdir():
                f.unlink()
            write_parts(target, rec_11_join.native(), join=rec_11_join, chunk_rows=2)

    # 4. inner/left/full with unique sides succeed and equal native
    for how in ["inner", "left", "full"]:
        for v in ["1:m", "m:1", "1:1"]:
            for f in target.iterdir():
                f.unlink()
            rec_ok = JoinRecipe(
                base_uniq.lazy(), join_uniq.lazy(), {"how": how, "on": "k", "validate": v}
            )
            write_parts(target, rec_ok.native(), join=rec_ok, chunk_rows=2)
            got_ok = scan_parts(part_paths(target)).collect()
            assert got_ok.sort(got_ok.columns).equals(
                rec_ok.native().collect().sort(got_ok.columns)
            )

    # 5. duplicate key that NO row of the other side references still raises for m:1
    base_no_ref = pl.DataFrame({"k": ["a", "b"], "x": [1, 2]})
    join_unref_dup = pl.DataFrame({"k": ["z", "z"], "y": [3, 4]})
    for how in ["inner", "left", "full"]:
        rec_unref = JoinRecipe(
            base_no_ref.lazy(), join_unref_dup.lazy(), {"how": how, "on": "k", "validate": "m:1"}
        )
        with pytest.raises(
            pl.exceptions.ComputeError, match=r"join keys did not fulfill m:1 validation"
        ):
            for f in target.iterdir():
                f.unlink()
            write_parts(target, rec_unref.native(), join=rec_unref, chunk_rows=2)

    # 6. duplicate null keys never violate: nulls never match (nulls_equal=False).
    base_nulls = pl.DataFrame({"k": [None, None, "a"], "x": [1, 2, 3]})
    join_nulls = pl.DataFrame({"k": [None, None, "a"], "y": [4, 5, 6]})
    for how in ["inner", "left", "full"]:
        for v in ["1:m", "m:1", "1:1"]:
            for f in target.iterdir():
                f.unlink()
            rec_nulls = JoinRecipe(
                base_nulls.lazy(), join_nulls.lazy(), {"how": how, "on": "k", "validate": v}
            )
            res_nulls = write_parts(target, rec_nulls.native(), join=rec_nulls, chunk_rows=2)
            assert res_nulls.strategy == "chunked_join"
            got_nulls = scan_parts(part_paths(target)).collect()
            if how == "full" and v in {"1:m", "1:1"}:
                # Documented divergence: Polars 1.44.2 rejects exactly this case —
                # a full join validating its base side when BOTH sides hold
                # duplicate null keys — though it accepts duplicate base nulls
                # against a single join-side null, and ignores nulls for every
                # other join and side. The writer applies the consistent rule.
                with pytest.raises(pl.exceptions.ComputeError, match="validation"):
                    rec_nulls.native().collect()
                expected_nulls = (
                    base_nulls.lazy().join(join_nulls.lazy(), how=how, on="k").collect()
                )
            else:
                expected_nulls = rec_nulls.native().collect()
            assert got_nulls.sort(got_nulls.columns, nulls_last=True).equals(
                expected_nulls.sort(got_nulls.columns, nulls_last=True)
            )

    base_pnull = pl.DataFrame({"k1": ["a", "a"], "k2": [None, None], "x": [1, 2]})
    join_pnull = pl.DataFrame({"k1": ["a", "a"], "k2": [None, None], "y": [3, 4]})
    for how in ["inner", "left", "full"]:
        for v in ["1:m", "m:1", "1:1"]:
            for f in target.iterdir():
                f.unlink()
            rec_pnull = JoinRecipe(
                base_pnull.lazy(),
                join_pnull.lazy(),
                {"how": how, "on": ["k1", "k2"], "validate": v},
            )
            # A key with any null part never matches, so it never violates.
            res_pnull = write_parts(target, rec_pnull.native(), join=rec_pnull, chunk_rows=2)
            assert res_pnull.strategy == "chunked_join"
            got_pnull = scan_parts(part_paths(target)).collect()
            if how == "full" and v in {"1:m", "1:1"}:
                # The same documented Polars divergence as above.
                with pytest.raises(pl.exceptions.ComputeError, match="validation"):
                    rec_pnull.native().collect()
                expected_pnull = (
                    base_pnull.lazy().join(join_pnull.lazy(), how=how, on=["k1", "k2"]).collect()
                )
            else:
                expected_pnull = rec_pnull.native().collect()
            assert got_pnull.sort(got_pnull.columns, nulls_last=True).equals(
                expected_pnull.sort(got_pnull.columns, nulls_last=True)
            )


def test_chunked_join_keeps_driving_order_when_maintain_order_names_it(tmp_path: Path) -> None:
    base, join = _make_join_frames()
    order_cases = [
        ("left", "left"),
        ("inner", "left"),
        ("left", "left_right"),
        ("inner", "left_right"),
        ("right", "right"),
        ("right", "right_left"),
        ("semi", "left"),
        ("anti", "left"),
    ]
    chunk_rows_list = [1, 2, 3, 1000]

    target = tmp_path / "order_dir"
    target.mkdir()

    for how, order in order_cases:
        cfg = {"how": how, "on": "k", "maintainOrder": order, "suffix": "_r"}
        recipe = JoinRecipe(base.lazy(), join.lazy(), cfg)
        native = recipe.native().collect()

        for chunk_rows in chunk_rows_list:
            for f in target.iterdir():
                f.unlink()
            write_parts(target, recipe.native(), join=recipe, chunk_rows=chunk_rows)
            got = scan_parts(part_paths(target)).collect()

            if order in ("left", "right"):
                col = "x" if order == "left" else "y"
                assert got[col].to_list() == native[col].to_list(), (
                    f"Order mismatch in {col} for {how}, {order}, chunk {chunk_rows}"
                )
                assert got.sort(got.columns, nulls_last=True).equals(
                    native.sort(native.columns, nulls_last=True)
                ), f"Multiset mismatch for {how}, {order}, chunk {chunk_rows}"
            else:
                assert got.equals(native), (
                    f"Exact frame mismatch for {how}, {order}, chunk {chunk_rows}"
                )


def test_unchunkable_join_is_written_natively_and_says_so(tmp_path: Path) -> None:
    base, join = _make_join_frames()

    # (full, maintainOrder "left") -> strategy "native", native_reason "full_join_ordered"
    target1 = tmp_path / "t1"
    target1.mkdir()
    cfg1 = {"how": "full", "on": "k", "maintainOrder": "left", "suffix": "_r"}
    recipe1 = JoinRecipe(base.lazy(), join.lazy(), cfg1)
    res1 = write_parts(target1, recipe1.native(), join=recipe1, chunk_rows=5)
    assert res1.strategy == "native"
    assert res1.native_reason == "full_join_ordered"
    got1 = scan_parts(part_paths(target1)).collect()
    assert got1.sort(got1.columns, nulls_last=True).equals(
        recipe1.native().collect().sort(got1.columns, nulls_last=True)
    )

    # (left, maintainOrder "right") -> "native", "ordered_by_lookup_side"
    target2 = tmp_path / "t2"
    target2.mkdir()
    cfg2 = {"how": "left", "on": "k", "maintainOrder": "right", "suffix": "_r"}
    recipe2 = JoinRecipe(base.lazy(), join.lazy(), cfg2)
    res2 = write_parts(target2, recipe2.native(), join=recipe2, chunk_rows=5)
    assert res2.strategy == "native"
    assert res2.native_reason == "ordered_by_lookup_side"
    got2 = scan_parts(part_paths(target2)).collect()
    assert got2.sort(got2.columns, nulls_last=True).equals(
        recipe2.native().collect().sort(got2.columns, nulls_last=True)
    )


def test_high_fan_out_key_is_split_by_expected_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collected_frames: list[pl.DataFrame] = []
    orig_collect = haute._chunked_writes.execution_collect

    def tracking_collect(lf: pl.LazyFrame, *args: Any, **kwargs: Any) -> pl.DataFrame:
        res = orig_collect(lf, *args, **kwargs)
        collected_frames.append(res)
        return res

    part_heights: list[int] = []
    orig_sink = haute._chunked_writes._Parts.sink
    orig_cw = haute._chunked_writes._Parts.collect_and_write

    def tracking_sink(self: Any, lf: pl.LazyFrame, *args: Any, **kwargs: Any) -> None:
        orig_sink(self, lf, *args, **kwargs)
        path = self.directory / self.names[-1]
        part_heights.append(pl.read_parquet(path).height)

    def tracking_cw(self: Any, lf: pl.LazyFrame) -> None:
        orig_cw(self, lf)
        path = self.directory / self.names[-1]
        part_heights.append(pl.read_parquet(path).height)

    monkeypatch.setattr(haute._chunked_writes, "execution_collect", tracking_collect)
    monkeypatch.setattr(haute._chunked_writes._Parts, "sink", tracking_sink)
    monkeypatch.setattr(haute._chunked_writes._Parts, "collect_and_write", tracking_cw)

    cases = [
        # (a) one driving row matching 23 lookup rows
        (
            pl.DataFrame({"k": ["A"], "x": [1]}),
            pl.DataFrame({"k": ["A"] * 23, "y": list(range(23))}),
        ),
        # (b) driving chunk whose 5 rows share keys matching 3 lookup rows each
        # (15 expected output rows)
        (
            pl.DataFrame({"k": ["A", "B", "C", "D", "E"], "x": [1, 2, 3, 4, 5]}),
            pl.DataFrame(
                {
                    "k": ["A"] * 3 + ["B"] * 3 + ["C"] * 3 + ["D"] * 3 + ["E"] * 3,
                    "y": list(range(15)),
                }
            ),
        ),
        # (c) many driving rows sharing ONE key with 2 lookup rows
        (
            pl.DataFrame({"k": ["A"] * 12, "x": list(range(12))}),
            pl.DataFrame({"k": ["A", "A"], "y": [1, 2]}),
        ),
    ]

    target = tmp_path / "fan_out_dir"
    target.mkdir()

    for idx, (b_df, j_df) in enumerate(cases):
        for order in [None, "left"]:
            for f in target.iterdir():
                f.unlink()
            collected_frames.clear()
            part_heights.clear()

            cfg: dict[str, Any] = {"how": "inner", "on": "k"}
            if order is not None:
                cfg["maintainOrder"] = order

            recipe = JoinRecipe(b_df.lazy(), j_df.lazy(), cfg)
            native = recipe.native().collect()

            write_parts(target, recipe.native(), join=recipe, chunk_rows=5)

            # Assert:
            # - no collected driving chunk has more than 5 rows
            # - no collected lookup-match frame exceeds 6 rows (chunk_rows + 1)
            # - no part holds more than 5 rows
            for frame in collected_frames:
                assert frame.height <= 6, f"Collected frame height {frame.height} > 6 in case {idx}"

            for h in part_heights:
                assert h <= 5, f"Part height {h} > 5 in case {idx} order {order}"

            got = scan_parts(part_paths(target)).collect()
            assert got.sort(got.columns).equals(native.sort(got.columns))

            if order == "left":
                assert got["x"].to_list() == native["x"].to_list()


def test_non_sliceable_sides_are_staged_and_never_published(tmp_path: Path) -> None:
    base_file = tmp_path / "base.parquet"
    pl.DataFrame({"k": ["a", "b", "c", "d"], "x": [1, 2, 3, 4]}).write_parquet(base_file)

    # Filtered scan is not sliceable
    base = pl.scan_parquet(base_file).filter(pl.col("x") >= 2)
    assert sliceable(base) is False

    # Group by is not sliceable
    join = (
        pl.DataFrame({"k": ["b", "b", "c", "d"], "val": [10, 20, 30, 40]})
        .lazy()
        .group_by("k")
        .agg(pl.col("val").sum())
    )
    assert sliceable(join) is False

    recipe = JoinRecipe(base, join, {"how": "inner", "on": "k"})
    target = tmp_path / "staged_out"
    target.mkdir()

    res = write_parts(target, recipe.native(), join=recipe, chunk_rows=2)
    assert res.strategy == "chunked_join"
    assert res.staged_inputs == 2

    got = scan_parts(part_paths(target)).collect()
    native = recipe.native().collect()
    assert got.sort("k").equals(native.sort("k"))

    # After write, target contains only part-*.parquet files (no staging dir)
    entries = [p.name for p in target.iterdir()]
    assert all(is_part_name(name) for name in entries)
    assert not (target / ".chunk-inputs").exists()


def test_cross_join_chunks_by_lookup_size(tmp_path: Path) -> None:
    base = pl.DataFrame({"x": list(range(10))}).lazy()
    lookup = pl.DataFrame({"y": list(range(4))}).lazy()

    recipe = JoinRecipe(base, lookup, {"how": "cross"})
    target = tmp_path / "cross_target"
    target.mkdir()

    write_parts(target, recipe.native(), join=recipe, chunk_rows=8)
    parts = part_paths(target)
    assert len(parts) == 5

    for p in parts:
        assert pl.read_parquet(p).height <= 8

    got = scan_parts(parts).collect()
    native = recipe.native().collect()
    assert got.sort(got.columns).equals(native.sort(got.columns))


@pytest.mark.parametrize("order", ["left", "left_right", "right", "right_left", "none"])
@pytest.mark.parametrize("sizes", [(2, 23), (23, 2), (0, 23), (23, 0)])
def test_cross_join_bounds_both_inputs_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, order: str, sizes: tuple[int, int]
) -> None:
    from polars.testing import assert_frame_equal

    recipe = JoinRecipe(
        pl.DataFrame({"x": range(sizes[0])}).lazy(),
        pl.DataFrame({"x": range(sizes[1])}).lazy(),
        {"how": "cross", "maintainOrder": order, "suffix": "_lookup"},
    )
    expected = recipe.native().collect(engine="in-memory")
    heights: list[int] = []
    collect = haute._chunked_writes.execution_collect

    def bounded_collect(*args, **kwargs):
        result = collect(*args, **kwargs)
        heights.append(result.height)
        return result

    monkeypatch.setattr(haute._chunked_writes, "execution_collect", bounded_collect)
    target = tmp_path / "bounded_cross"
    target.mkdir()
    written = write_parts(target, recipe.native(), join=recipe, chunk_rows=7)
    assert written.strategy == "chunked_join"
    assert written.chunk_rows == 7
    assert max(heights, default=0) <= 7
    paths = part_paths(target)
    assert all(pq.ParquetFile(path).metadata.num_rows <= 7 for path in paths)
    actual = scan_parts(paths).collect()
    if order == "none":
        actual, expected = actual.sort(actual.columns), expected.sort(expected.columns)
    assert_frame_equal(actual, expected)


class Cancelled(Exception):  # noqa: N818
    pass


class _FakeExecutionContext:
    def __init__(self, cancel_on_chunk: int = 3) -> None:
        self.cancel_on_chunk = cancel_on_chunk
        self.chunk_write_calls = 0
        self.chunks_recorded = 0
        self.bytes_recorded = 0

    def checkpoint(self, *, label: str, node_id: str | None = None) -> None:
        if label == "chunked_write":
            self.chunk_write_calls += 1
            if self.chunk_write_calls == self.cancel_on_chunk:
                raise Cancelled("Cancelled on chunk write")

    def record_chunk(self) -> None:
        self.chunks_recorded += 1

    def record_bytes_written(self, n: int) -> None:
        self.bytes_recorded += n

    def record_collect(self, *args: Any, **kwargs: Any) -> None:
        pass


def test_writer_checkpoints_between_chunks_and_stops_on_cancel(tmp_path: Path) -> None:
    df = pl.DataFrame({"a": list(range(10))})
    target = tmp_path / "cancel_target"
    target.mkdir()

    ctx = _FakeExecutionContext(cancel_on_chunk=3)
    frame = df.lazy()

    with pytest.raises(Cancelled):
        write_parts(
            target,
            frame,
            chunk_rows=2,
            execution_context=ctx,  # type: ignore[arg-type]
        )

    parts = part_paths(target)
    assert len(parts) < 5
    assert len(parts) == 2


# -- Recipes come from the builder, and validation survives awkward key names --


def _source(node_id: str, path: Path) -> Any:
    from haute._types import GraphNode, NodeData, NodeType

    config = {"inputType": "file", "format": "parquet", "mode": "scan", "path": str(path)}
    return GraphNode(
        id=node_id, data=NodeData(label=node_id, nodeType=NodeType.DATA_INPUT, config=config)
    )


def test_an_instance_join_is_written_with_the_builders_recipe(
    haute_scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``instanceOf`` join has only a reference in its own config; its recipe
    must be the builder's (resolved roles and joined config), or its capture
    would fail or join differently."""
    from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
    from haute.execution import execute_lazy_graph
    from haute.executor import _build_node_fn

    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    pl.DataFrame({"k": [1, 2, 3, 1], "v": [10, 20, 30, 40]}).write_parquet(
        haute_scratch / "b.parquet"
    )
    pl.DataFrame({"k": [1, 3], "w": [7, 9]}).write_parquet(haute_scratch / "j.parquet")
    pl.DataFrame({"k": [3, 3, 5], "v": [1, 2, 3]}).write_parquet(haute_scratch / "b2.parquet")
    pl.DataFrame({"k": [3, 5], "w": [8, 6]}).write_parquet(haute_scratch / "j2.parquet")
    join_config = {
        "how": "left",
        "on": ["k"],
        "selected_columns": ["k", "w"],
        "column_renames": {"w": "weight"},
    }
    graph = PipelineGraph(
        nodes=[
            _source("base", haute_scratch / "b.parquet"),
            _source("side", haute_scratch / "j.parquet"),
            _source("base2", haute_scratch / "b2.parquet"),
            _source("side2", haute_scratch / "j2.parquet"),
            GraphNode(
                id="J", data=NodeData(label="J", nodeType=NodeType.EDGE_JOIN, config=join_config)
            ),
            GraphNode(
                id="J_copy",
                data=NodeData(
                    label="J_copy", nodeType=NodeType.EDGE_JOIN, config={"instanceOf": "J"}
                ),
            ),
        ],
        edges=[
            GraphEdge(id="e0", source="base", target="J", targetHandle="base"),
            GraphEdge(id="e1", source="side", target="J", targetHandle="join"),
            # Join handle listed first: roles come from handles, not edge order.
            GraphEdge(id="e2", source="side2", target="J_copy", targetHandle="join"),
            GraphEdge(id="e3", source="base2", target="J_copy", targetHandle="base"),
        ],
        preamble="import polars as pl",
        source_file=str(haute_scratch / "main.py"),
    )
    recipes: dict[str, JoinRecipe] = {}

    outputs, *_ = execute_lazy_graph(
        graph, _build_node_fn, target_node_id="J_copy", join_recipes=recipes
    )

    recipe = recipes["J_copy"]
    assert recipe.config["on"] == ["k"]
    engine_output = outputs["J_copy"].collect()
    # The instance joins with the original's config and keeps its own (absent) shaping.
    assert engine_output.columns == ["k", "v", "w"]
    target = haute_scratch / "parts"
    target.mkdir()
    written = write_parts(target, outputs["J_copy"], join=recipe, chunk_rows=1)
    assert written.strategy == "chunked_join"
    got = scan_parts(part_paths(target)).collect()
    assert got.sort(got.columns).equals(engine_output.sort(engine_output.columns))


def test_validation_is_exact_when_a_join_key_is_named_len(tmp_path: Path) -> None:
    base = pl.DataFrame({"len": [1, 2], "x": [1, 2]}).lazy()
    join = pl.DataFrame({"len": [1, 2], "y": [3, 4]}).lazy()
    recipe = JoinRecipe(base, join, {"how": "left", "on": ["len"], "validate": "1:1"})

    written = write_parts(tmp_path, recipe.native(), join=recipe, chunk_rows=1)

    assert written.strategy == "chunked_join"
    got = scan_parts(part_paths(tmp_path)).collect()
    assert got.sort("len").equals(recipe.native().collect().sort("len"))

    duplicated = JoinRecipe(
        base, pl.concat([join, join]), {"how": "left", "on": ["len"], "validate": "1:1"}
    )
    other = tmp_path / "dup"
    other.mkdir()
    with pytest.raises(
        pl.exceptions.ComputeError, match="join keys did not fulfill 1:1 validation"
    ):
        write_parts(other, duplicated.native(), join=duplicated, chunk_rows=1)


def test_parts_carry_write_time_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (a) sliced write producing > 1 part
    target_sliced = tmp_path / "sliced"
    target_sliced.mkdir()
    lf_sliced = pl.LazyFrame({"a": [1, 2, 3, 4], "b": ["w", "x", "y", "z"]})
    written_sliced = write_parts(target_sliced, lf_sliced, chunk_rows=1)
    assert len(written_sliced.parts) > 1
    assert list(written_sliced.digests) == list(written_sliced.parts)
    for name in written_sliced.parts:
        assert written_sliced.digests[name] == content_hash(target_sliced / name)

    # (b) ordered chunked join whose parts go through _Parts.collect_and_write
    target_join = tmp_path / "join"
    target_join.mkdir()
    base = pl.DataFrame({"k": [1, 2], "v": ["a", "b"]}).lazy()
    join = pl.DataFrame({"k": [1, 2], "w": [10, 20]}).lazy()
    recipe = JoinRecipe(base, join, {"how": "left", "on": ["k"], "maintainOrder": "left"})
    calls: list[Any] = []
    real_collect_and_write = haute._chunked_writes._Parts.collect_and_write

    def _recording_collect_and_write(self: Any, lf: pl.LazyFrame) -> None:
        calls.append(lf)
        real_collect_and_write(self, lf)

    monkeypatch.setattr(
        haute._chunked_writes._Parts, "collect_and_write", _recording_collect_and_write
    )
    written_join = write_parts(target_join, recipe.native(), join=recipe, chunk_rows=1)
    assert len(calls) >= 1
    assert list(written_join.digests) == list(written_join.parts)
    for name in written_join.parts:
        assert written_join.digests[name] == content_hash(target_join / name)

    # (c) empty result whose single part comes from ensure_one
    target_empty = tmp_path / "empty"
    target_empty.mkdir()
    lf_empty = pl.LazyFrame({"a": [1, 2, 3, 4]}).filter(pl.col("a") > 100)
    written_empty = write_parts(target_empty, lf_empty)
    assert len(written_empty.parts) == 1
    assert list(written_empty.digests) == list(written_empty.parts)
    for name in written_empty.parts:
        assert written_empty.digests[name] == content_hash(target_empty / name)


def test_a_hot_key_spanning_parts_equals_the_native_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup_file = tmp_path / "lookup.parquet"
    lookup_df = pl.DataFrame(
        {
            "k": ["hot"] * 70 + ["cold1"] * 15 + ["cold2"] * 15,
            "v": list(range(100)),
        }
    )
    lookup_df.write_parquet(lookup_file, row_group_size=10)
    lookup = pl.scan_parquet(lookup_file)
    base = pl.DataFrame({"k": ["hot", "cold1"], "d": [1, 2]}).lazy()
    recipe = JoinRecipe(base, lookup, {"how": "inner", "on": "k"})
    chunk_rows = 15
    target = tmp_path / "parts"
    target.mkdir()

    window_calls = 0
    real_heavy_window = haute._chunked_writes._ChunkJoin._heavy_window

    def _counting_heavy_window(
        self: Any, matches: pl.LazyFrame, cursor: int | None
    ) -> pl.LazyFrame:
        nonlocal window_calls
        window_calls += 1
        return real_heavy_window(self, matches, cursor)

    monkeypatch.setattr(haute._chunked_writes._ChunkJoin, "_heavy_window", _counting_heavy_window)

    written = write_parts(target, recipe.native(), join=recipe, chunk_rows=chunk_rows)
    assert written.strategy == "chunked_join"
    assert window_calls > 1

    got = scan_parts(part_paths(target)).collect()
    native = recipe.native().collect()
    assert got.sort(got.columns).equals(native.sort(native.columns))


def test_a_heavy_rows_windows_survive_a_reordered_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = pl.DataFrame({"k": ["hot"] * 45 + ["other"] * 5, "v": list(range(50))}).lazy()
    base = pl.DataFrame({"k": ["hot", "other"], "d": [1, 2]}).lazy()
    recipe = JoinRecipe(base, lookup, {"how": "inner", "on": "k"})
    chunk_rows = 10
    target = tmp_path / "parts"
    target.mkdir()

    window_calls = 0
    real_heavy_window = haute._chunked_writes._ChunkJoin._heavy_window

    def _reordering_heavy_window(
        self: Any, matches: pl.LazyFrame, cursor: int | None
    ) -> pl.LazyFrame:
        nonlocal window_calls
        call_idx = window_calls
        window_calls += 1
        # Patching _collect instead would prove nothing, because that only
        # reorders rows the selection already chose. The permutation must
        # happen before the window is selected.
        descending = (call_idx % 2) == 1
        reordered = matches.sort("v", descending=descending)
        return real_heavy_window(self, reordered, cursor)

    monkeypatch.setattr(haute._chunked_writes._ChunkJoin, "_heavy_window", _reordering_heavy_window)

    written = write_parts(target, recipe.native(), join=recipe, chunk_rows=chunk_rows)
    assert written.strategy == "chunked_join"
    assert window_calls >= 2

    got = scan_parts(part_paths(target)).collect()
    native = recipe.native().collect()
    assert got.sort(got.columns).equals(native.sort(native.columns))


def test_a_heavy_row_keeps_the_lookups_order_when_the_join_asks_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = pl.DataFrame({"k": ["hot"] * 45 + ["other"] * 5, "v": list(range(50))}).lazy()
    base = pl.DataFrame({"k": ["hot", "other"], "d": [1, 2]}).lazy()
    recipe = JoinRecipe(
        base,
        lookup,
        {"how": "left", "on": "k", "maintainOrder": "left_right"},
    )
    chunk_rows = 10
    target = tmp_path / "parts"
    target.mkdir()

    window_calls = 0
    real_heavy_window = haute._chunked_writes._ChunkJoin._heavy_window
    real_collect = haute._chunked_writes._ChunkJoin._collect

    def _reordering_heavy_window(
        self: Any, matches: pl.LazyFrame, cursor: int | None
    ) -> pl.LazyFrame:
        nonlocal window_calls
        call_idx = window_calls
        window_calls += 1
        descending = (call_idx % 2) == 1
        reordered = matches.sort("v", descending=descending)
        return real_heavy_window(self, reordered, cursor)

    def _reversing_collect(self: Any, lf: pl.LazyFrame) -> pl.DataFrame:
        df = real_collect(self, lf)
        if self._index_column is not None and self._index_column in df.columns:
            return df.reverse()
        return df

    monkeypatch.setattr(haute._chunked_writes._ChunkJoin, "_heavy_window", _reordering_heavy_window)
    monkeypatch.setattr(haute._chunked_writes._ChunkJoin, "_collect", _reversing_collect)

    written = write_parts(target, recipe.native(), join=recipe, chunk_rows=chunk_rows)
    assert written.strategy == "chunked_join"
    assert window_calls >= 2

    got = scan_parts(part_paths(target)).collect()
    native = recipe.native().collect()
    assert got.equals(native)


def test_a_heavy_row_that_loses_a_match_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = pl.DataFrame({"k": ["hot"] * 25, "v": list(range(25))}).lazy()
    base = pl.DataFrame({"k": ["hot"], "d": [1]}).lazy()
    recipe = JoinRecipe(base, lookup, {"how": "inner", "on": "k"})
    chunk_rows = 10
    target = tmp_path / "parts"
    target.mkdir()

    dropped = False
    window_calls = 0
    real_heavy_window = haute._chunked_writes._ChunkJoin._heavy_window
    real_collect = haute._chunked_writes._ChunkJoin._collect

    def _counting_heavy_window(
        self: Any, matches: pl.LazyFrame, cursor: int | None
    ) -> pl.LazyFrame:
        nonlocal window_calls
        window_calls += 1
        return real_heavy_window(self, matches, cursor)

    def _dropping_collect(self: Any, lf: pl.LazyFrame) -> pl.DataFrame:
        nonlocal dropped
        df = real_collect(self, lf)
        if not dropped and self._index_column is not None and self._index_column in df.columns:
            dropped = True
            return df.slice(1)
        return df

    monkeypatch.setattr(haute._chunked_writes._ChunkJoin, "_heavy_window", _counting_heavy_window)
    monkeypatch.setattr(haute._chunked_writes._ChunkJoin, "_collect", _dropping_collect)

    with pytest.raises(RuntimeError, match=r"heavy row wrote 9 rows, expected 25"):
        write_parts(target, recipe.native(), join=recipe, chunk_rows=chunk_rows)

    assert dropped
    assert window_calls == 1


def test_a_write_reports_the_rows_per_part_it_chunked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("POLARS_STREAMING_CHUNK_SIZE", raising=False)
    ambient = 1234
    assert ambient != current_streaming_chunk_size()
    set_streaming_chunk_size(ambient)

    # 1. Sliced write with explicit chunk_rows
    sliced_target = tmp_path / "sliced"
    sliced_target.mkdir()
    sliced_lf = pl.DataFrame({"a": list(range(20))}).lazy()
    sliced_write = write_parts(sliced_target, sliced_lf, chunk_rows=5)
    assert sliced_write.strategy == "sliced"
    assert sliced_write.chunk_rows == 5

    # 2. Keyed chunked join with explicit chunk_rows
    keyed_target = tmp_path / "keyed"
    keyed_target.mkdir()
    base = pl.DataFrame({"k": ["a", "b"] * 10, "v": list(range(20))}).lazy()
    lookup = pl.DataFrame({"k": ["a", "b"], "v2": [1, 2]}).lazy()
    keyed_recipe = JoinRecipe(base, lookup, {"how": "inner", "on": "k"})
    keyed_write = write_parts(keyed_target, keyed_recipe.native(), join=keyed_recipe, chunk_rows=5)
    assert keyed_write.strategy == "chunked_join"
    assert keyed_write.chunk_rows == 5

    # 3. Native write
    native_target = tmp_path / "native"
    native_target.mkdir()
    native_lf = pl.DataFrame({"a": list(range(20))}).lazy().filter(pl.col("a") > 5)
    native_write = write_parts(native_target, native_lf, chunk_rows=5)
    assert native_write.strategy == "native"
    assert native_write.chunk_rows is None

    # 4. Cross join
    cross_target = tmp_path / "cross"
    cross_target.mkdir()
    cross_base = pl.DataFrame({"k": ["a", "b"]}).lazy()
    cross_lookup = pl.DataFrame({"v": [1, 2]}).lazy()
    cross_recipe = JoinRecipe(cross_base, cross_lookup, {"how": "cross"})
    cross_write = write_parts(cross_target, cross_recipe.native(), join=cross_recipe, chunk_rows=5)
    assert cross_write.strategy == "chunked_join"
    assert cross_write.chunk_rows == 5

    # 5. Caller passes no chunk_rows -> equals current_streaming_chunk_size()
    default_target = tmp_path / "default_chunk_rows"
    default_target.mkdir()
    default_write = write_parts(default_target, sliced_lf)
    assert default_write.strategy == "sliced"
    assert default_write.chunk_rows == ambient
    assert default_write.chunk_rows == current_streaming_chunk_size()


def test_input_sliced_filter_and_derived_columns_equal_native_strict_order(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame(
        {
            "id": list(range(100)),
            "val": [float(i * 3) for i in range(100)],
            "grp": [f"g_{i % 5}" for i in range(100)],
        }
    )
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    cases = [
        # (a) Plain filter
        (
            "filter",
            scan.filter(pl.col("id") % 2 == 0),
            WriteRecipe(input=scan, fn=lambda lf: lf.filter(pl.col("id") % 2 == 0)),
        ),
        # (b) Filter with row-local derived columns
        (
            "filter_derived",
            scan.filter(pl.col("id") > 15).with_columns(
                d1=pl.col("val") * 2.0,
                d2=pl.col("id") + 10,
            ),
            WriteRecipe(
                input=scan,
                fn=lambda lf: lf.filter(pl.col("id") > 15).with_columns(
                    d1=pl.col("val") * 2.0,
                    d2=pl.col("id") + 10,
                ),
            ),
        ),
    ]

    for name, frame, recipe in cases:
        target = tmp_path / f"out_{name}"
        target.mkdir()
        res = write_parts(target, frame, recipe=recipe, chunk_rows=15)
        assert res.strategy == "input_sliced"
        assert res.chunks > 1
        assert res.input_slices == 7
        assert res.chunk_rows == 15
        assert res.native_reason is None
        assert res.blocking_operator is None

        got = scan_parts(part_paths(target)).collect()
        expected = frame.collect()
        assert got.schema == expected.schema
        # Strict row order on monotonic key 'id'
        assert got["id"].to_list() == expected["id"].to_list()
        assert got.equals(expected)


def test_unsupported_chunk_local_operations_stay_native_with_reason_and_operator(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame(
        {
            "id": list(range(50)),
            "val": [float(i) for i in range(50)],
            "k": ["a", "b"] * 25,
            "s": [{"sub": i} for i in range(50)],
            "lst": [[1, 2]] * 50,
        }
    )
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    filtered = scan.filter(pl.col("id") > 2)

    # Every one must be built over a non-sliceable input so sliceable(frame) is False.
    unsupported_cases = [
        ("head", "df = parent.head(10)", filtered.head(10), "unsupported_frame_method", "head"),
        (
            "slice",
            "df = parent.slice(2, 10)",
            filtered.slice(2, 10),
            "unsupported_frame_method",
            "slice",
        ),
        (
            "with_row_index",
            "df = parent.with_row_index('idx')",
            filtered.with_row_index("idx"),
            "unsupported_frame_method",
            "with_row_index",
        ),
        ("unique", "df = parent.unique()", filtered.unique(), "unsupported_frame_method", "unique"),
        ("shift", "df = parent.shift(1)", filtered.shift(1), "unsupported_frame_method", "shift"),
        (
            "window",
            "df = parent.select(pl.col('val').over('k'))",
            filtered.select(pl.col("val").over("k")),
            "unsupported_expression_method",
            "over",
        ),
        (
            "group_by",
            "df = parent.group_by('k').len()",
            filtered.group_by("k").len(),
            "unsupported_frame_method",
            "group_by",
        ),
        (
            "unnest",
            "df = parent.unnest('s')",
            filtered.unnest("s"),
            "unsupported_frame_method",
            "unnest",
        ),
        (
            "explode",
            "df = parent.explode('lst')",
            filtered.explode("lst"),
            "unsupported_frame_method",
            "explode",
        ),
    ]

    for name, code_str, frame, expected_reason, expected_op in unsupported_cases:
        decision = classify_chunk_local_polars_code(code_str, frame_names=("parent",))
        assert not decision.eligible, f"Case {name} unexpectedly classified as eligible"
        assert decision.reason == expected_reason, f"Case {name} reason mismatch"
        assert decision.blocking_operator == expected_op, f"Case {name} operator mismatch"

        assert sliceable(frame) is False, f"Frame for {name} must not be sliceable"
        target = tmp_path / f"out_{name}"
        target.mkdir()
        recipe = WriteRecipe(
            input=filtered,
            fn=None,  # A rejected recipe carries no function
            reason=decision.reason,
            blocking_operator=decision.blocking_operator,
        )
        res = write_parts(target, frame, recipe=recipe, chunk_rows=5)
        assert res.strategy == "native", f"Case {name} expected native strategy"
        assert res.native_reason == decision.reason, f"Case {name} reason mismatch"
        assert res.blocking_operator == decision.blocking_operator, (
            f"Case {name} blocking_operator mismatch"
        )
        assert res.chunk_rows is None
        assert res.input_slices is None

        got = scan_parts(part_paths(target)).collect()
        expected = frame.collect()
        assert got.sort(got.columns, nulls_last=True).equals(
            expected.sort(expected.columns, nulls_last=True)
        )


def test_rejected_write_recipe_requires_reason_and_cannot_be_applied() -> None:
    frame = pl.DataFrame({"a": [1]}).lazy()
    with pytest.raises(ValueError, match="rejected write recipe must carry a reason"):
        WriteRecipe(input=frame, fn=None, reason=None)

    rejected = WriteRecipe(input=frame, fn=None, reason="unsupported_frame_method")
    with pytest.raises(ValueError, match="rejected write recipe"):
        rejected.apply(frame)
    with pytest.raises(ValueError, match="rejected write recipe"):
        rejected.native()


def test_recipe_input_not_sliceable_falls_back_to_native(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(30)), "v": list(range(30))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    # Input is a filtered scan, which is NOT sliceable
    unsliceable_input = scan.filter(pl.col("id") > 5)
    assert sliceable(unsliceable_input) is False

    def fn(lf: pl.LazyFrame) -> pl.LazyFrame:
        return lf.filter(pl.col("v") % 2 == 0)

    recipe = WriteRecipe(input=unsliceable_input, fn=fn)
    frame = recipe.native()
    assert sliceable(frame) is False

    target = tmp_path / "fallback_target"
    target.mkdir()
    res = write_parts(target, frame, recipe=recipe, chunk_rows=5)
    assert res.strategy == "native"
    assert res.native_reason == "input_not_sliceable"
    assert res.blocking_operator is None
    assert res.chunk_rows is None
    assert res.input_slices is None

    got = scan_parts(part_paths(target)).collect()
    expected = frame.collect()
    assert got.equals(expected)


def test_no_query_holds_more_than_one_input_slice(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(50)), "val": list(range(50))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    assert sliceable(scan) is True

    chunk_rows = 10
    slice_rows_seen: list[int] = []

    def tracking_fn(slice_lf: pl.LazyFrame) -> pl.LazyFrame:
        n = slice_lf.select(pl.len()).collect().item()
        slice_rows_seen.append(n)
        return slice_lf.filter(pl.col("val") > 5)

    recipe = WriteRecipe(input=scan, fn=tracking_fn)
    frame = scan.filter(pl.col("val") > 5)

    target = tmp_path / "slice_bounded_target"
    target.mkdir()
    res = write_parts(target, frame, recipe=recipe, chunk_rows=chunk_rows)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 5

    # Slicing [1:] assumes exactly one prior full-input call from check_recipe_equivalence;
    # the subsequent length assertion makes any change in prior calls fail loudly.
    write_slice_calls = slice_rows_seen[1:]
    assert len(write_slice_calls) == 5
    for count in write_slice_calls:
        assert count <= chunk_rows, (
            f"Applied function saw {count} rows, expected at most {chunk_rows}"
        )

    got = scan_parts(part_paths(target)).collect()
    expected = frame.collect()
    assert got.equals(expected)

    # An unsliceable input falls back to native under sliceable(recipe.input)
    unsliceable_input = scan.filter(pl.col("id") > 0)
    assert sliceable(unsliceable_input) is False
    unsliceable_recipe = WriteRecipe(
        input=unsliceable_input,
        fn=lambda lf: lf.filter(pl.col("val") > 5),
    )
    unsliceable_target = tmp_path / "unsliceable_target"
    unsliceable_target.mkdir()
    res_unsliceable = write_parts(
        unsliceable_target,
        unsliceable_recipe.native(),
        recipe=unsliceable_recipe,
        chunk_rows=chunk_rows,
    )
    assert res_unsliceable.strategy == "native"
    assert res_unsliceable.native_reason == "input_not_sliceable"


def test_recipe_equivalence_mismatch_raises_and_writes_no_part(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(30)), "val": list(range(30))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    # Frame has predicate val > 2
    frame = scan.filter(pl.col("val") > 2)

    # 1. Recipe has a different predicate (val > 10)
    mismatched_recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(pl.col("val") > 10))
    target1 = tmp_path / "mismatch_plan"
    target1.mkdir()
    with pytest.raises(RecipeEquivalenceError, match=r"plan"):
        write_parts(target1, frame, recipe=mismatched_recipe, chunk_rows=5)
    assert not part_paths(target1)

    # 2. Recipe has a different schema (selects only 'id')
    schema_mismatch_recipe = WriteRecipe(input=scan, fn=lambda lf: lf.select("id"))
    target2 = tmp_path / "mismatch_schema"
    target2.mkdir()
    with pytest.raises(RecipeEquivalenceError, match=r"schema"):
        write_parts(target2, frame, recipe=schema_mismatch_recipe, chunk_rows=5)
    assert not part_paths(target2)


def test_write_strategy_precedence_join_over_recipe_and_sliced_over_input_sliced(
    tmp_path: Path,
) -> None:
    base = pl.DataFrame({"k": [1, 2, 3], "x": [10, 20, 30]}).lazy()
    lookup = pl.DataFrame({"k": [1, 2], "y": [100, 200]}).lazy()
    join_recipe = JoinRecipe(base, lookup, {"how": "inner", "on": "k"})

    # 1. Join recipe wins over write recipe
    recipe = WriteRecipe(input=base, fn=lambda lf: lf.filter(pl.col("x") > 10))
    target1 = tmp_path / "prec_join"
    target1.mkdir()
    res1 = write_parts(
        target1,
        join_recipe.native(),
        join=join_recipe,
        recipe=recipe,
        chunk_rows=2,
    )
    assert res1.strategy == "chunked_join"

    # 2. A frame that is itself sliceable takes 'sliced' rather than 'input_sliced'
    source_path = tmp_path / "sliceable.parquet"
    pl.DataFrame({"a": list(range(20)), "b": list(range(20))}).write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    sliceable_frame = scan.select("a", "b")
    assert sliceable(sliceable_frame) is True

    sliceable_recipe = WriteRecipe(input=scan, fn=lambda lf: lf.select("a", "b"))
    target2 = tmp_path / "prec_sliced"
    target2.mkdir()
    res2 = write_parts(
        target2,
        sliceable_frame,
        recipe=sliceable_recipe,
        chunk_rows=5,
    )
    assert res2.strategy == "sliced"
    assert res2.chunk_rows == 5
    assert len(res2.parts) == 4
    got = scan_parts(part_paths(target2)).collect()
    assert got.equals(sliceable_frame.collect())


def test_input_sliced_empty_input_reports_one_slice_and_one_part(tmp_path: Path) -> None:
    source_path = tmp_path / "empty.parquet"
    df = pl.DataFrame({"id": [], "val": []}, schema={"id": pl.Int64, "val": pl.Float64})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    # Frame has a filter so sliceable(frame) is False, but scan is sliceable.
    frame = scan.filter(pl.col("id") > 0)
    assert sliceable(frame) is False
    assert sliceable(scan) is True

    recipe = WriteRecipe(
        input=scan,
        fn=lambda lf: lf.filter(pl.col("id") > 0),
    )
    target = tmp_path / "empty_out"
    target.mkdir()
    res = write_parts(target, frame, recipe=recipe, chunk_rows=10)
    assert res.strategy == "input_sliced"
    assert res.chunks == 1
    assert res.input_slices == 1
    assert res.input_slices == len(res.parts) == res.chunks
    assert res.chunk_rows == 10
    assert res.native_reason is None
    assert res.blocking_operator is None

    got = scan_parts(part_paths(target)).collect()
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.height == 0
    assert got.equals(expected)


# ---------------------------------------------------------------------------
# Single-file mode
# ---------------------------------------------------------------------------


def test_single_file_filter_equals_native_and_writes_one_file(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(20)), "val": list(range(20))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    assert sliceable(scan) is True

    def fn(lf: pl.LazyFrame) -> pl.LazyFrame:
        filtered = lf.filter(pl.col("val") >= 0)
        n = filtered.select(pl.len()).collect().item()
        if n <= 10:
            return filtered.with_columns(pl.col("val").cast(pl.Int32))
        return filtered.with_columns(pl.col("val").cast(pl.Int64))

    recipe = WriteRecipe(input=scan, fn=fn)
    frame = recipe.native()
    assert sliceable(frame) is False

    dest = tmp_path / "out.parquet"
    res = write_file(dest, frame, recipe=recipe, chunk_rows=10)
    assert res.strategy == "input_sliced"
    assert res.parts == ()
    assert res.chunks == 0
    assert res.input_slices == 2
    assert res.chunk_rows == 10
    assert res.native_reason is None
    assert res.blocking_operator is None
    assert dest.is_file()
    assert len(list(tmp_path.glob("part-*"))) == 0

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.equals(expected)


def test_single_file_each_applied_function_sees_at_most_chunk_rows(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(50)), "val": list(range(50))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    assert sliceable(scan) is True

    chunk_rows = 10
    slice_rows_seen: list[int] = []

    def tracking_fn(slice_lf: pl.LazyFrame) -> pl.LazyFrame:
        n = slice_lf.select(pl.len()).collect().item()
        slice_rows_seen.append(n)
        return slice_lf.filter(pl.col("val") > 5)

    recipe = WriteRecipe(input=scan, fn=tracking_fn)
    frame = scan.filter(pl.col("val") > 5)
    assert sliceable(frame) is False

    dest = tmp_path / "out.parquet"
    res = write_file(dest, frame, recipe=recipe, chunk_rows=chunk_rows)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 5

    # Slicing [1:] assumes exactly one prior full-input call from check_recipe_equivalence
    write_slice_calls = slice_rows_seen[1:]
    assert len(write_slice_calls) == 5
    for count in write_slice_calls:
        assert count <= chunk_rows, (
            f"Applied function saw {count} rows, expected at most {chunk_rows}"
        )

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.equals(expected)


def test_single_file_compression_codec_equals_natively_sunk_file(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(30)), "val": list(range(30))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    frame = scan.filter(pl.col("val") > 2)
    assert sliceable(frame) is False

    recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(pl.col("val") > 2))

    single_dest = tmp_path / "single.parquet"
    native_dest = tmp_path / "native.parquet"

    write_file(single_dest, frame, recipe=recipe, chunk_rows=10)
    bounded_sink(frame, native_dest)

    single_pf = pq.ParquetFile(single_dest)
    native_pf = pq.ParquetFile(native_dest)

    single_codec = single_pf.metadata.row_group(0).column(0).compression
    native_codec = native_pf.metadata.row_group(0).column(0).compression
    assert single_codec == native_codec
    assert single_codec.upper() == "ZSTD"


def test_single_file_largest_row_group_equals_row_group_constant(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    total_rows = 150_000
    df = pl.DataFrame({"a": list(range(total_rows))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    frame = scan.filter(pl.col("a") >= 0)
    assert sliceable(frame) is False

    recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(pl.col("a") >= 0))
    dest = tmp_path / "single.parquet"

    write_file(dest, frame, recipe=recipe, chunk_rows=total_rows)

    pf = pq.ParquetFile(dest)
    largest_rg = max(pf.metadata.row_group(i).num_rows for i in range(pf.metadata.num_row_groups))
    assert largest_rg == SINGLE_FILE_ROW_GROUP_SIZE


def test_single_file_all_empty_input_writes_one_file_with_right_schema_and_zero_rows(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "empty.parquet"
    df = pl.DataFrame({"id": [], "val": []}, schema={"id": pl.Int64, "val": pl.Float64})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    frame = scan.filter(pl.col("id") > 0)
    assert sliceable(frame) is False
    assert sliceable(scan) is True

    recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(pl.col("id") > 0))
    dest = tmp_path / "empty_out.parquet"

    res = write_file(dest, frame, recipe=recipe, chunk_rows=10)
    assert res.strategy == "input_sliced"
    assert res.chunks == 0
    assert res.parts == ()
    assert res.input_slices == 1
    assert res.chunk_rows == 10
    assert res.native_reason is None
    assert res.blocking_operator is None
    assert dest.is_file()

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.height == 0
    assert got.equals(expected)


def test_single_file_selective_filter_writes_no_zero_row_group_and_counts_applied_slices(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(30)), "val": list(range(30))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    # Slices 0..9 and 20..29 have rows matching predicate; slice 10..19 has none.
    filter_expr = (pl.col("id") < 10) | (pl.col("id") >= 20)
    frame = scan.filter(filter_expr)
    assert sliceable(frame) is False

    recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(filter_expr))
    dest = tmp_path / "selective.parquet"

    res = write_file(dest, frame, recipe=recipe, chunk_rows=10)
    # Count of slices applied is 3 (all 3 slices driven through recipe)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 3

    # File contains no zero-row row groups (empty slice skipped)
    pf = pq.ParquetFile(dest)
    assert pf.metadata.num_row_groups == 2
    for i in range(pf.metadata.num_row_groups):
        assert pf.metadata.row_group(i).num_rows > 0

    got = pl.read_parquet(dest)
    assert got.height == 20
    assert got.equals(frame.collect())


def test_single_file_unsliceable_input_falls_back_to_native_with_reason(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(20)), "val": list(range(20))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    unsliceable_input = scan.filter(pl.col("id") > 0)
    assert sliceable(unsliceable_input) is False

    unsliceable_recipe = WriteRecipe(
        input=unsliceable_input,
        fn=lambda lf: lf.filter(pl.col("val") > 5),
    )
    frame = unsliceable_recipe.native()
    assert sliceable(frame) is False

    dest = tmp_path / "unsliceable_out.parquet"
    res = write_file(dest, frame, recipe=unsliceable_recipe, chunk_rows=5)
    assert res.strategy == "native"
    assert res.native_reason == "input_not_sliceable"
    assert res.parts == ()
    assert res.chunks == 0
    assert dest.is_file()

    got = pl.read_parquet(dest)
    assert got.equals(frame.collect())


def test_single_file_non_atomic_write_makes_no_temporary_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``atomic=False`` must reach every strategy, not only the sliced loop.

    A caller passing it is already writing to a staging path it renames or
    discards. A temporary beside that path is a hidden sibling in a directory
    the caller governs and never sweeps, so a hard kill orphans it — and the
    rename on success means a finished run cannot see the difference, which is
    why this watches the write rather than the directory afterwards.
    """
    from haute import _polars_utils

    staged: list[Path] = []
    real_atomic_write = _polars_utils.atomic_write

    def recording(dest: Path, **kwargs: Any) -> Any:
        staged.append(Path(dest))
        return real_atomic_write(dest, **kwargs)

    monkeypatch.setattr(_polars_utils, "atomic_write", recording)

    source = tmp_path / "source.parquet"
    pl.DataFrame({"id": list(range(40)), "val": list(range(40))}).write_parquet(source)
    # Sorted, so neither the frame nor any recipe can be sliced: this is the
    # writer's own native branch.
    frame = pl.scan_parquet(source).sort("val")
    assert sliceable(frame) is False

    dest = tmp_path / "out" / "result.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)

    written = write_file(dest, frame, chunk_rows=10, atomic=False)

    assert written.strategy == "native"
    assert [path for path in staged if path.resolve().parent == dest.parent.resolve()] == []
    assert pl.read_parquet(dest).height == 40


def test_single_file_misbound_recipe_raises_and_creates_no_file(tmp_path: Path) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(20)), "val": list(range(20))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    frame = scan.filter(pl.col("val") > 2)
    assert sliceable(frame) is False

    mismatched_recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(pl.col("val") > 10))
    dest = tmp_path / "never_created.parquet"

    with pytest.raises(RecipeEquivalenceError, match=r"plan"):
        write_file(dest, frame, recipe=mismatched_recipe, chunk_rows=5)
    assert not dest.exists()


def test_single_file_sliceable_frame_no_recipe_written_sliced_across_multiple_slices(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(25)), "val": [float(i * 2) for i in range(25)]})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    frame = scan.select("id", "val")
    assert sliceable(frame) is True

    dest = tmp_path / "out.parquet"
    res = write_file(dest, frame, chunk_rows=10)
    assert res.strategy == "sliced"
    assert res.chunk_rows == 10
    assert res.input_slices is None
    assert res.parts == ()
    assert res.chunks == 0
    assert res.native_reason is None
    assert res.blocking_operator is None
    assert dest.is_file()

    pf = pq.ParquetFile(dest)
    num_slices = pf.metadata.num_row_groups
    assert num_slices == 3
    assert num_slices > 1

    native_dest = tmp_path / "native.parquet"
    bounded_sink(frame, native_dest)
    native_got = pl.read_parquet(native_dest)

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.equals(expected)
    assert got.equals(native_got)


def test_single_file_sliceable_frame_with_recipe_takes_sliced_over_input_sliced(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"a": list(range(20)), "b": list(range(20))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    frame = scan.select("a", "b")
    assert sliceable(frame) is True

    recipe = WriteRecipe(input=scan, fn=lambda lf: lf.select("a", "b"))
    dest = tmp_path / "prec_sliced.parquet"

    res = write_file(dest, frame, recipe=recipe, chunk_rows=5)
    assert res.strategy == "sliced"
    assert res.chunk_rows == 5
    assert res.input_slices is None
    assert res.parts == ()
    assert res.chunks == 0
    assert dest.is_file()

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.equals(expected)


def test_single_file_empty_sliceable_frame_writes_one_file_with_right_schema_and_zero_rows(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "empty_source.parquet"
    df = pl.DataFrame({"id": [], "val": []}, schema={"id": pl.Int64, "val": pl.Float64})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    frame = scan.select("id", "val")
    assert sliceable(frame) is True

    dest = tmp_path / "empty_out.parquet"
    res = write_file(dest, frame, chunk_rows=10)
    assert res.strategy == "sliced"
    assert res.chunk_rows == 10
    assert res.input_slices is None
    assert res.parts == ()
    assert res.chunks == 0
    assert res.native_reason is None
    assert res.blocking_operator is None
    assert dest.is_file()

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.height == 0
    assert got.equals(expected)


def test_single_file_mixed_dtype_frame_sliced_across_slices_equals_bounded_sink(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "mixed_source.parquet"
    df = pl.DataFrame(
        {
            "str_col": [f"text_{i}" for i in range(10)],
            "bin_col": [f"bytes_{i}".encode() for i in range(10)],
            "struct_col": [{"s": f"nested_{i}"} for i in range(10)],
            "date_col": [datetime.date(2026, 1, 1 + i) for i in range(10)],
            "dt_tz_col": [
                datetime.datetime(2026, 1, 1, 12, 0, i, tzinfo=datetime.UTC) for i in range(10)
            ],
            "dec_col": [Decimal(f"{i}.50") for i in range(10)],
            "list_col": [[i, i + 1] for i in range(10)],
            "null_col": [None] * 10,
        }
    )
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)
    assert sliceable(scan) is True

    chunk_rows = 5
    dest = tmp_path / "mixed_sliced.parquet"
    res = write_file(dest, scan, chunk_rows=chunk_rows)
    assert res.strategy == "sliced"
    assert res.chunk_rows == chunk_rows
    assert res.parts == ()
    assert res.chunks == 0
    assert dest.is_file()

    pf = pq.ParquetFile(dest)
    num_slices = pf.metadata.num_row_groups
    assert num_slices == 2
    assert num_slices > 1

    native_dest = tmp_path / "mixed_native.parquet"
    bounded_sink(scan, native_dest)

    got = pl.read_parquet(dest)
    native_got = pl.read_parquet(native_dest)
    assert got.schema == df.schema
    assert got.equals(native_got)
    assert got.equals(df)


def test_single_file_apply_raising_on_second_slice_raises_and_leaves_no_file(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(20)), "val": list(range(20))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    def failing_fn(slice_lf: pl.LazyFrame) -> pl.LazyFrame:
        n = slice_lf.select(pl.len()).collect().item()
        first_val = slice_lf.select(pl.col("val").first()).collect().item()
        if n == 5 and first_val == 5:
            raise RuntimeError("simulated failure on second slice")
        return slice_lf.filter(pl.col("val") >= 0)

    recipe = WriteRecipe(input=scan, fn=failing_fn)
    frame = recipe.native()
    assert sliceable(frame) is False
    assert sliceable(recipe.input) is True

    dest = tmp_path / "failed.parquet"
    with pytest.raises(RuntimeError, match="simulated failure on second slice"):
        write_file(dest, frame, recipe=recipe, chunk_rows=5)

    assert not dest.exists()
    assert not dest.with_suffix(".parquet.tmp").exists()


def test_single_file_positive_rows_with_every_slice_filtered_out_writes_zero_row_file(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(20)), "val": [float(i) for i in range(20)]})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    filter_expr = pl.col("id") > 1000
    recipe = WriteRecipe(input=scan, fn=lambda lf: lf.filter(filter_expr))
    frame = recipe.native()
    assert sliceable(frame) is False
    assert sliceable(recipe.input) is True

    dest = tmp_path / "all_filtered.parquet"
    res = write_file(dest, frame, recipe=recipe, chunk_rows=10)
    assert res.strategy == "input_sliced"
    assert res.input_slices == 2
    assert res.chunk_rows == 10
    assert res.parts == ()
    assert res.chunks == 0
    assert dest.is_file()

    got = pl.read_parquet(dest)
    expected = frame.collect()
    assert got.schema == expected.schema
    assert got.height == 0
    assert got.equals(expected)


def test_single_file_records_chunk_per_written_slice_in_execution_context(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.parquet"
    df = pl.DataFrame({"id": list(range(30))})
    df.write_parquet(source_path)
    scan = pl.scan_parquet(source_path)

    ctx = _FakeExecutionContext(cancel_on_chunk=999)
    dest = tmp_path / "ctx_out.parquet"
    res = write_file(dest, scan, chunk_rows=10, execution_context=ctx)  # type: ignore[arg-type]
    assert res.strategy == "sliced"
    assert ctx.chunks_recorded == 3
