"""Registry + dtype-codec contracts for the dataInput/dataOutput machinery."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import ExecutionProfile
from haute._polars_dtypes import dtype_to_spec, parse_dtype, parse_schema_mapping
from haute._polars_io_registry import (
    FORMATS,
    FORMATS_BY_NAME,
    PolarsIoConfigError,
    allowed_arguments,
    input_callable_key,
    output_callable_key,
    read_polars_input,
    registry_capabilities,
    resolve_input_mode,
    resolve_output_mode,
    write_polars_output,
)
from haute._polars_io_schema import io_functions_by_key
from haute.errors import BoundedMemoryUnsupportedError, SchemaMismatchError


class TestRegistrySchemaCompleteness:
    """Every registry callable must exist in the committed interface schema."""

    def test_every_registry_callable_is_in_the_committed_schema(self) -> None:
        known = io_functions_by_key()
        problems: list[str] = []
        for fmt in FORMATS:
            for owner, name in (
                ("polars", fmt.reader),
                ("polars", fmt.scanner),
                ("DataFrame", fmt.writer),
                ("LazyFrame", fmt.sinker),
            ):
                if name is not None and f"{owner}.{name}" not in known:
                    problems.append(f"{fmt.name}: {owner}.{name}")
        assert problems == []

    def test_source_owned_args_are_real_arguments(self) -> None:
        # Guard against typos: every source-owned name must appear in at least
        # one of the format's callables' signatures.
        known = io_functions_by_key()
        for fmt in FORMATS:
            all_args: set[str] = set()
            for owner, name in (
                ("polars", fmt.reader),
                ("polars", fmt.scanner),
                ("DataFrame", fmt.writer),
                ("LazyFrame", fmt.sinker),
            ):
                if name is not None:
                    record = known[f"{owner}.{name}"]
                    all_args |= {a["name"] for a in record["arguments"]}
            # `connection`/`table_name`/`path` style names may only exist on
            # one side; each owned name must exist somewhere for the format.
            missing = {n for n in fmt.source_owned_args if n not in all_args}
            assert not missing, f"{fmt.name}: source_owned_args not in any signature: {missing}"

    def test_allowed_arguments_exclude_the_excluded_classes(self) -> None:
        for fmt in FORMATS:
            for mode_resolver, key_fn in (
                (("scan", "read"), input_callable_key),
                (("sink", "write"), output_callable_key),
            ):
                for mode in mode_resolver:
                    try:
                        owner, name = key_fn(fmt, mode)  # type: ignore[arg-type]
                    except PolarsIoConfigError:
                        continue
                    allowed = allowed_arguments(fmt, owner, name)
                    assert not any(a.startswith("_") for a in allowed)
                    assert "storage_options" not in allowed
                    assert "credential_provider" not in allowed
                    assert not (fmt.source_owned_args & allowed)
                    if name.startswith("sink_"):
                        assert "lazy" not in allowed
                        assert "optimizations" not in allowed

    def test_registry_capabilities_payload_shape(self) -> None:
        payload = registry_capabilities()
        assert payload["schema_version"] == 1
        formats = {
            entry["name"]: entry for group in payload["groups"] for entry in group["formats"]
        }
        assert set(formats) == set(FORMATS_BY_NAME)
        csv = formats["csv"]
        assert csv["input"]["modes"] == ["scan", "read"]
        assert "schema_overrides" in csv["input"]["arguments"]["scan"]
        assert csv["input"]["engines_missing"] == []
        delta = formats["delta"]
        # Core haute ships no deltalake engine: the capability payload must
        # say so rather than pretending delta is runnable.
        assert delta["input"]["engines_missing"] == ["deltalake"]


class TestDtypeCodec:
    ROUND_TRIP_CASES = [
        pl.Int64(),
        pl.String(),
        pl.Boolean(),
        pl.Date(),
        pl.Datetime("us", None),
        pl.Datetime("ms", "Europe/London"),
        pl.Duration("ns"),
        pl.Decimal(precision=38, scale=2),
        pl.Categorical(),
        pl.Enum(["a", "b"]),
        pl.List(pl.Int64()),
        pl.Array(pl.Float64(), 3),
        pl.Struct({"a": pl.Int64(), "b": pl.List(pl.String())}),
        pl.List(pl.Struct({"x": pl.Datetime("us", None)})),
    ]

    @pytest.mark.parametrize("dtype", ROUND_TRIP_CASES, ids=str)
    def test_spec_round_trip(self, dtype: pl.DataType) -> None:
        spec = dtype_to_spec(dtype)
        assert parse_dtype(spec) == dtype

    def test_scalar_aliases(self) -> None:
        assert parse_dtype("int") == pl.Int64
        assert parse_dtype("str") == pl.String
        assert parse_dtype("float") == pl.Float64
        assert parse_dtype("Int32") == pl.Int32

    def test_unknown_dtype_fails_loudly(self) -> None:
        with pytest.raises(SchemaMismatchError):
            parse_dtype("NotADtype", column="c")

    def test_unknown_spec_keys_fail_loudly(self) -> None:
        with pytest.raises(SchemaMismatchError):
            parse_dtype({"type": "List", "inner": "int", "surprise": 1})

    def test_struct_requires_fields(self) -> None:
        with pytest.raises(SchemaMismatchError):
            parse_dtype({"type": "Struct", "fields": {}})

    def test_schema_mapping_decodes_in_order(self) -> None:
        decoded = parse_schema_mapping(
            {"a": "int64", "b": {"type": "List", "inner": "str"}}, argument="schema"
        )
        assert list(decoded) == ["a", "b"]
        assert decoded["b"] == pl.List(pl.String())


@pytest.fixture
def struct_fixture_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "id": [1, 2],
            "name": ["a", None],
            "when": [dt.datetime(2026, 1, 1, 12, 0, 0), None],
            "amount": [Decimal("1.25"), Decimal("2.50")],
            "tags": [["x", "y"], []],
            "nested": [{"k": 1, "deep": [1, 2]}, {"k": 2, "deep": []}],
        },
        schema_overrides={"amount": pl.Decimal(precision=10, scale=2)},
    )


class TestReadPolarsInput:
    def test_csv_scan_with_struct_free_schema(self, haute_scratch, struct_fixture_frame) -> None:
        path = haute_scratch / "t.csv"
        struct_fixture_frame.select("id", "name").write_csv(path)
        lf = read_polars_input(
            {
                "format": "csv",
                "path": str(path),
                "arguments": {"schema_overrides": {"id": "int32"}},
            }
        )
        assert isinstance(lf, pl.LazyFrame)
        out = lf.collect()
        assert out.schema["id"] == pl.Int32

    def test_parquet_round_trip_preserves_structs(
        self, haute_scratch, struct_fixture_frame
    ) -> None:
        path = haute_scratch / "t.parquet"
        struct_fixture_frame.write_parquet(path)
        out = read_polars_input({"format": "parquet", "path": str(path)}).collect()
        assert_frame_equal(out, struct_fixture_frame)

    def test_json_eager_reads_structs(self, haute_scratch) -> None:
        path = haute_scratch / "t.json"
        path.write_text(
            '[{"a": 1, "obj": {"x": [1, 2]}}, {"a": 2, "obj": {"x": []}}]',
            encoding="utf-8",
        )
        out = read_polars_input({"format": "json", "path": str(path)}).collect()
        assert out.schema["obj"] == pl.Struct({"x": pl.List(pl.Int64)})

    def test_inline_records_with_schema(self) -> None:
        out = read_polars_input(
            {
                "format": "records",
                "records": [{"a": 1, "s": {"k": "v"}}, {"a": 2, "s": None}],
                "arguments": {
                    "schema": {
                        "a": "int64",
                        "s": {"type": "Struct", "fields": {"k": "str"}},
                    }
                },
            }
        ).collect()
        assert out.schema["s"] == pl.Struct({"k": pl.String})
        assert out["a"].to_list() == [1, 2]

    def test_unknown_format_lists_supported(self) -> None:
        with pytest.raises(PolarsIoConfigError, match="Supported formats"):
            read_polars_input({"format": "sas", "path": "x"})

    def test_unknown_argument_names_polars_function(self, haute_scratch) -> None:
        with pytest.raises(PolarsIoConfigError, match="scan_csv"):
            read_polars_input(
                {"format": "csv", "path": str(haute_scratch / "x.csv"), "arguments": {"nope": 1}}
            )

    def test_remote_arguments_are_rejected(self, haute_scratch) -> None:
        with pytest.raises(PolarsIoConfigError, match="storage_options"):
            read_polars_input(
                {
                    "format": "parquet",
                    "path": str(haute_scratch / "x.parquet"),
                    "arguments": {"storage_options": {"aws_region": "eu-west-1"}},
                }
            )

    def test_url_shaped_path_is_refused(self) -> None:
        with pytest.raises(ValueError, match="looks like a URL"):
            read_polars_input({"format": "parquet", "path": "s3://bucket/x.parquet"})

    def test_bounded_profile_refuses_eager_only_format(self, haute_scratch) -> None:
        path = haute_scratch / "t.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(BoundedMemoryUnsupportedError):
            read_polars_input(
                {"format": "json", "path": str(path)},
                profile=ExecutionProfile.LAZY_SINK,
            )

    def test_bounded_profile_refuses_explicit_eager_mode(self, haute_scratch) -> None:
        path = haute_scratch / "t.parquet"
        pl.DataFrame({"a": [1]}).write_parquet(path)
        with pytest.raises(BoundedMemoryUnsupportedError, match="scan"):
            read_polars_input(
                {"format": "parquet", "path": str(path), "mode": "read"},
                profile=ExecutionProfile.LAZY_SINK,
            )

    def test_bounded_csv_requires_full_schema(self, haute_scratch) -> None:
        path = haute_scratch / "t.csv"
        path.write_text("a,b\n1,x\n", encoding="utf-8")
        with pytest.raises(BoundedMemoryUnsupportedError, match="schema"):
            read_polars_input(
                {"format": "csv", "path": str(path)},
                profile=ExecutionProfile.LAZY_SINK,
            )
        out = read_polars_input(
            {
                "format": "csv",
                "path": str(path),
                "arguments": {"schema": {"a": "int64", "b": "str"}},
            },
            profile=ExecutionProfile.LAZY_SINK,
        ).collect()
        assert out.schema["a"] == pl.Int64

    def test_engine_gated_format_fails_actionably_when_absent(self, haute_scratch) -> None:
        # Core haute ships no ODS engine; the error must say what to
        # install rather than surfacing a bare ImportError mid-parse.
        import importlib.util

        if importlib.util.find_spec("fastexcel"):
            pytest.skip(
                "fastexcel is installed, so the engine-absence error path this "
                "test pins is not exercisable in this environment"
            )
        with pytest.raises(PolarsIoConfigError, match="install one of"):
            read_polars_input({"format": "ods", "path": str(haute_scratch / "t.ods")})

    def test_mode_scan_unavailable_fails_loudly(self, haute_scratch) -> None:
        with pytest.raises(PolarsIoConfigError, match="no lazy scan"):
            read_polars_input(
                {"format": "json", "path": str(haute_scratch / "t.json"), "mode": "scan"}
            )


class TestWritePolarsOutput:
    def test_parquet_sink_and_rescan(self, haute_scratch, struct_fixture_frame) -> None:
        target = haute_scratch / "out.parquet"
        rows = write_polars_output(
            struct_fixture_frame.lazy(),
            {"format": "parquet", "path": str(target)},
            resolved_path=target,
        )
        assert rows is None  # streaming sink: caller re-scans
        assert_frame_equal(pl.read_parquet(target), struct_fixture_frame)

    def test_ndjson_sink(self, haute_scratch) -> None:
        target = haute_scratch / "out.jsonl"
        df = pl.DataFrame({"a": [1, 2], "tags": [["x"], []]})
        write_polars_output(
            df.lazy(),
            {"format": "ndjson", "path": str(target)},
            resolved_path=target,
        )
        assert target.read_text(encoding="utf-8").count("\n") == 2

    def test_eager_write_reports_row_count(self, haute_scratch) -> None:
        target = haute_scratch / "out.json"
        df = pl.DataFrame({"a": [1, 2, 3]})
        rows = write_polars_output(
            df.lazy(),
            {"format": "json", "path": str(target), "mode": "write"},
            resolved_path=target,
        )
        assert rows == 3

    def test_avro_eager_write(self, haute_scratch) -> None:
        target = haute_scratch / "out.avro"
        df = pl.DataFrame({"a": [1, 2]})
        rows = write_polars_output(
            df.lazy(),
            {"format": "avro", "path": str(target)},
            resolved_path=target,
        )
        assert rows == 2
        assert pl.read_avro(target)["a"].to_list() == [1, 2]

    def test_csv_write_arguments_pass_through(self, haute_scratch) -> None:
        target = haute_scratch / "out.csv"
        write_polars_output(
            pl.DataFrame({"a": [1]}).lazy(),
            {"format": "csv", "path": str(target), "arguments": {"separator": ";"}},
            resolved_path=target,
        )
        assert "a" in target.read_text(encoding="utf-8")

    def test_read_only_format_has_no_write(self) -> None:
        fmt = FORMATS_BY_NAME["ods"]
        with pytest.raises(PolarsIoConfigError, match="no write support"):
            resolve_output_mode(fmt, {"format": "ods"})

    def test_output_mode_defaults(self) -> None:
        assert resolve_output_mode(FORMATS_BY_NAME["parquet"], {}) == "sink"
        assert resolve_output_mode(FORMATS_BY_NAME["avro"], {}) == "write"
        assert resolve_input_mode(FORMATS_BY_NAME["parquet"], {}) == "scan"
        assert resolve_input_mode(FORMATS_BY_NAME["json"], {}) == "read"
