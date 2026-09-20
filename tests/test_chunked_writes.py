"""Tests for chunked writes and join recipes."""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.io.plugins import register_io_source

import haute._chunked_writes
from haute._chunked_writes import (
    JoinRecipe,
    RecipeEquivalenceError,
    WriteRecipe,
    is_part_name,
    part_paths,
    scan_parts,
    sliceable,
    write_parts,
)
from haute._hashing import content_hash
from haute._polars_utils import current_streaming_chunk_size, temporary_streaming_chunk_size
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

    def fault_point(self, name: str, node_id: str | None = None) -> None:
        pass

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


def test_a_write_reports_the_rows_per_part_it_chunked_at(tmp_path: Path) -> None:
    ambient = 1234
    assert ambient != current_streaming_chunk_size()
    with temporary_streaming_chunk_size(ambient):
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
        keyed_write = write_parts(
            keyed_target, keyed_recipe.native(), join=keyed_recipe, chunk_rows=5
        )
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
        cross_write = write_parts(
            cross_target, cross_recipe.native(), join=cross_recipe, chunk_rows=5
        )
        assert cross_write.strategy == "chunked_join"
        assert cross_write.chunk_rows is None

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
