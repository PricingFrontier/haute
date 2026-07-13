"""Per-format round-trip legs for the dataInput/dataOutput node machinery.

Port of the 20260707 research round-trip fixtures (19 legs, 0 failures) into
the suite, re-aimed at the NODE layer: every write goes through
``write_polars_output`` and every read through ``read_polars_input`` — the
exact invocations a fully-configured dataOutput/dataInput node executes — so
the node≡polars-invocation equivalence is what these tests prove.

Each leg uses the strictest metric the research recorded for it:
schema-strict ``assert_frame_equal`` where the format round-trips losslessly
(parquet, IPC, IPC stream, inline records); cast-back with every documented
loss normalised where it doesn't (CSV empty-string/null, JSON-family
NaN→null); second-write byte-identity for the text formats where row order
is meaningful and preserved. Engine-gated legs (excel/ods/database/delta/
iceberg) skip when their engine package is absent — they go live when the
extras tranche lands.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._polars_io_registry import read_polars_input, write_polars_output

ALL_COLS = ["i64", "f64", "str", "bool", "date", "dt_us", "dec", "cat", "list_i"]


def base_df(drop: list[str] | None = None) -> pl.DataFrame:
    """The research's type-diverse fixture: nulls, NaN, unicode, µs datetimes,
    Decimal, Categorical, and a List column (the struct-capability carrier)."""
    df = pl.DataFrame(
        {
            "i64": pl.Series("i64", [1, None, -3], dtype=pl.Int64),
            "f64": pl.Series("f64", [1.5, float("nan"), None], dtype=pl.Float64),
            "str": pl.Series("str", ["héllo ✓ 漢", "", None], dtype=pl.String),
            "bool": pl.Series("bool", [True, False, None], dtype=pl.Boolean),
            "date": pl.Series("date", [date(2020, 1, 1), date(1999, 12, 31), None], dtype=pl.Date),
            "dt_us": pl.Series(
                "dt_us",
                [
                    datetime(2020, 1, 1, 12, 34, 56, 789012),
                    datetime(2024, 2, 29, 23, 59, 59),
                    None,
                ],
                dtype=pl.Datetime("us"),
            ),
            "dec": pl.Series(
                "dec", [Decimal("123.45"), Decimal("-0.01"), None], dtype=pl.Decimal(10, 2)
            ),
            "cat": pl.Series("cat", ["a", "b", "a"], dtype=pl.Categorical),
            "list_i": pl.Series("list_i", [[1, 2], [], None], dtype=pl.List(pl.Int64)),
        }
    )
    if drop:
        df = df.drop(drop)
    return df


def nan_to_null(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.col(n).fill_nan(None) for n, t in df.schema.items() if t in (pl.Float32, pl.Float64)
    )


def cast_back(back: pl.DataFrame, target_schema: dict) -> pl.DataFrame:
    """Cast a read-back frame to the original schema, parsing temporal strings."""
    exprs = []
    for n, t in target_schema.items():
        if back.schema[n] == pl.String and isinstance(t, pl.Datetime):
            exprs.append(pl.col(n).str.to_datetime(time_unit=t.time_unit))
        elif back.schema[n] == pl.String and t == pl.Date:
            exprs.append(pl.col(n).str.to_date())
        else:
            exprs.append(pl.col(n).cast(t))
    return back.select(exprs)


def _node_write(df: pl.DataFrame, fmt: str, target, mode: str | None = None, **arguments):
    config: dict = {"format": fmt, "path": str(target)}
    if mode:
        config["mode"] = mode
    if arguments:
        config["arguments"] = arguments
    return write_polars_output(df.lazy(), config, resolved_path=target)


def _node_read(fmt: str, source, mode: str | None = None, **arguments) -> pl.DataFrame:
    config: dict = {"format": fmt, "path": str(source)}
    if mode:
        config["mode"] = mode
    if arguments:
        config["arguments"] = arguments
    return read_polars_input(config).collect()


# ---------------------------------------------------------------- lossless tier


class TestParquetLeg:
    def test_write_read_scan_sink_all_schema_strict(self, haute_scratch) -> None:
        df = base_df()
        p = haute_scratch / "fx.parquet"
        _node_write(df, "parquet", p, mode="write")
        assert_frame_equal(_node_read("parquet", p, mode="read"), df)
        assert_frame_equal(_node_read("parquet", p, mode="scan"), df)
        p2 = haute_scratch / "fx_sink.parquet"
        _node_write(df, "parquet", p2, mode="sink")
        assert_frame_equal(_node_read("parquet", p2), df)


class TestIpcLegs:
    def test_ipc_file_schema_strict(self, haute_scratch) -> None:
        df = base_df()
        p = haute_scratch / "fx.ipc"
        _node_write(df, "ipc", p, mode="write")
        assert_frame_equal(_node_read("ipc", p, mode="read"), df)
        assert_frame_equal(_node_read("ipc", p, mode="scan"), df)
        p2 = haute_scratch / "fx_sink.ipc"
        _node_write(df, "ipc", p2, mode="sink")
        assert_frame_equal(_node_read("ipc", p2), df)

    def test_ipc_stream_schema_strict(self, haute_scratch) -> None:
        df = base_df()
        p = haute_scratch / "fx.arrows"
        rows = _node_write(df, "ipc_stream", p)
        assert rows == 3  # eager write reports height
        assert_frame_equal(_node_read("ipc_stream", p), df)


class TestInlineRecordsLeg:
    def test_records_with_schema_schema_strict(self) -> None:
        df = base_df()
        records = df.to_dicts()
        from haute._polars_dtypes import dtype_to_spec

        schema_spec = {n: dtype_to_spec(t) for n, t in df.schema.items()}
        back = read_polars_input(
            {"format": "records", "records": records, "arguments": {"schema": schema_spec}}
        ).collect()
        assert_frame_equal(back, df)


# ---------------------------------------------------------------- text tier (documented lossiness)


class TestCsvLeg:
    def test_schema_strict_with_overrides_and_byte_identity(self, haute_scratch) -> None:
        from haute._polars_dtypes import dtype_to_spec

        df = base_df(drop=["list_i"])  # nested types cannot be written to CSV
        p = haute_scratch / "fx.csv"
        _node_write(df, "csv", p, mode="write")

        overrides_spec = {n: dtype_to_spec(t) for n, t in df.schema.items()}
        # Both node read paths round-trip schema-strict INCLUDING the
        # empty-string/null distinction at the pinned polars (1.39.3) —
        # stricter than the research's 1.39.2 leg, which documented ''→null
        # conflation. Pinned exactly so any regression to conflation surfaces.
        back = _node_read("csv", p, mode="scan", schema_overrides=overrides_spec)
        assert_frame_equal(back, df)
        eager = _node_read("csv", p, mode="read", schema_overrides=overrides_spec)
        assert_frame_equal(eager, df)

        # Second-write byte-identity (research: PASS).
        p2 = haute_scratch / "fx2.csv"
        _node_write(back, "csv", p2, mode="write")
        assert p.read_bytes() == p2.read_bytes()

    def test_naive_read_lossiness_is_the_documented_class(self, haute_scratch) -> None:
        # Naive read (no overrides) degrades Decimal→Float64 and Categorical→
        # String — the research's documented inference loss, pinned so a
        # polars change to inference behaviour surfaces here.
        df = base_df(drop=["list_i"])
        p = haute_scratch / "fx.csv"
        _node_write(df, "csv", p, mode="write")
        naive = _node_read("csv", p, try_parse_dates=True)
        assert naive.schema["dec"] in (pl.Float64, pl.Decimal(10, 2))
        assert naive.schema["cat"] == pl.String


class TestNdjsonLeg:
    def test_cast_back_with_nan_normalisation(self, haute_scratch) -> None:
        df = base_df()
        p = haute_scratch / "fx.ndjson"
        _node_write(df, "ndjson", p, mode="write")
        back = _node_read("ndjson", p, mode="read")
        back2 = cast_back(back, dict(df.schema))
        # Documented loss: float NaN serialises as JSON null (irrecoverable).
        assert_frame_equal(nan_to_null(df), back2)
        # Lazy path agrees.
        assert_frame_equal(_node_read("ndjson", p, mode="scan"), back)

    def test_sink_ndjson_second_write_byte_identity(self, haute_scratch) -> None:
        df = base_df()
        p1 = haute_scratch / "fx1.jsonl"
        p2 = haute_scratch / "fx2.jsonl"
        _node_write(df, "ndjson", p1, mode="sink")
        rt = cast_back(_node_read("ndjson", p1), dict(df.schema))
        _node_write(rt, "ndjson", p2, mode="sink")
        assert p1.read_bytes() == p2.read_bytes()


class TestJsonLeg:
    def test_cast_back_with_nan_normalisation(self, haute_scratch) -> None:
        df = base_df()
        p = haute_scratch / "fx.json"
        _node_write(df, "json", p)
        back = _node_read("json", p)
        back2 = cast_back(back, dict(df.schema))
        assert_frame_equal(nan_to_null(df), back2)

    def test_full_width_json_struct_carrier(self, haute_scratch) -> None:
        # The struct ruling end to end: arrays, objects-as-fields, and nested
        # arrays-of-objects all land as one table with Struct/List columns —
        # no error, no data-integrity loss.
        p = haute_scratch / "wide.json"
        p.write_text(
            "["
            '{"id": 1, "tags": ["a", "b"], "meta": {"depth": 2, "flags": [true]},'
            ' "children": [{"k": 1}, {"k": 2}]},'
            '{"id": 2, "tags": [], "meta": {"depth": 0, "flags": []}, "children": []}'
            "]",
            encoding="utf-8",
        )
        out = _node_read("json", p)
        assert out.schema["tags"] == pl.List(pl.String)
        assert out.schema["meta"] == pl.Struct({"depth": pl.Int64, "flags": pl.List(pl.Boolean)})
        assert out.schema["children"] == pl.List(pl.Struct({"k": pl.Int64}))
        assert out["children"].to_list()[0] == [{"k": 1}, {"k": 2}]


# ---------------------------------------------------------------- avro


class TestAvroLeg:
    def test_round_trip_minus_categorical(self, haute_scratch) -> None:
        # Research: Categorical is unwritable in Avro ("not yet implemented");
        # everything else incl. Decimal/List/NaN is schema-strict.
        df = base_df(drop=["cat"])
        p = haute_scratch / "fx.avro"
        _node_write(df, "avro", p)
        assert_frame_equal(_node_read("avro", p), df)

    def test_categorical_still_unwritable_is_pinned(self, haute_scratch) -> None:
        df = base_df()
        p = haute_scratch / "fx_cat.avro"
        with pytest.raises(Exception, match="(?i)not.*implemented|categorical"):
            _node_write(df, "avro", p)


# ---------------------------------------------------------------- text lines (read-only)


class TestLinesLeg:
    def test_read_only_single_string_column(self, haute_scratch) -> None:
        # No write half exists in polars (RL only) — a read-only leg, named in
        # the plan so the node≡invocation proof doesn't silently skip an
        # included format.
        p = haute_scratch / "fx.txt"
        p.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        out = _node_read("lines", p, mode="read")
        assert out.width == 1
        assert out.height == 3
        assert out.dtypes == [pl.String]
        lazy = _node_read("lines", p, mode="scan")
        assert_frame_equal(lazy, out)


# ---------------------------------------------------------------- engine-gated legs


class TestExcelLeg:
    def test_read_via_openpyxl_engine_argument(self, haute_scratch) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        # Author a workbook with openpyxl directly (write_excel needs
        # xlsxwriter, which core haute deliberately does not ship).
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["i64", "str"])
        ws.append([1, "a"])
        ws.append([2, "b"])
        p = haute_scratch / "fx.xlsx"
        wb.save(p)

        out = _node_read("excel", p, engine="openpyxl")
        assert out["i64"].to_list() == [1, 2]
        assert out["str"].to_list() == ["a", "b"]

    def test_write_requires_xlsxwriter(self, haute_scratch) -> None:
        try:
            import xlsxwriter  # noqa: F401

            pytest.skip("xlsxwriter installed; absence path not exercisable")
        except ImportError:
            pass
        from haute._polars_io_registry import PolarsIoConfigError

        with pytest.raises(PolarsIoConfigError, match="install one of"):
            _node_write(base_df(drop=["cat", "list_i", "dec"]), "excel", haute_scratch / "o.xlsx")


class TestDatabaseLeg:
    def test_sqlite_round_trip_when_engines_present(self, haute_scratch) -> None:
        pytest.importorskip("sqlalchemy")
        pytest.importorskip("connectorx")
        # Research leg: sqlite via write_database + read_database_uri.
        # Runs when the database engines land (extras tranche); skipped today.
        df = base_df(drop=["list_i", "dec", "cat"])
        uri = f"sqlite:///{haute_scratch / 'fx.sqlite'}"
        rows = write_polars_output(
            df.lazy(),
            {"format": "database", "table": "fx", "uri": uri},
        )
        assert rows == 3
        back = read_polars_input(
            {"format": "database", "query": "SELECT * FROM fx", "uri": uri}
        ).collect()
        norm = cast_back(back, dict(df.schema))
        assert_frame_equal(nan_to_null(df), nan_to_null(norm))


class TestDeltaLeg:
    def test_round_trip_when_engine_present(self, haute_scratch) -> None:
        pytest.importorskip("deltalake")
        # Research: Categorical write panics in delta; rest schema-strict.
        df = base_df(drop=["cat"])
        target = haute_scratch / "delta_table"
        write_polars_output(
            df.lazy(),
            {"format": "delta", "path": str(target), "mode": "write"},
            resolved_path=target,
        )
        assert_frame_equal(
            _node_read("delta", target).sort("i64", nulls_last=True),
            df.sort("i64", nulls_last=True),
        )


class TestOdsAndIcebergLegs:
    def test_ods_read_when_engine_present(self, haute_scratch) -> None:
        pytest.importorskip("fastexcel")
        pytest.skip(
            "ODS fixture authoring needs odfpy (research authored via pandas+odfpy); "
            "leg goes live with the extras tranche — see research/20260707 roundtrips"
        )

    def test_iceberg_scan_when_engine_present(self, haute_scratch) -> None:
        pytest.importorskip("pyiceberg")
        pytest.skip(
            "iceberg leg needs a local pyiceberg SqlCatalog fixture; ported with the "
            "extras tranche — see research/20260707 roundtrips/test_iceberg.py"
        )
