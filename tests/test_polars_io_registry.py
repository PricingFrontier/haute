"""Registry + dtype-codec contracts for the dataInput/dataOutput machinery."""

from __future__ import annotations

import datetime as dt
import functools
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._polars_dtypes import dtype_to_spec, parse_dtype, parse_schema_mapping
from haute._polars_io_registry import (
    FORMATS,
    FORMATS_BY_NAME,
    IoFormat,
    PolarsIoConfigError,
    allowed_arguments,
    data_input_is_direct,
    input_callable_key,
    output_callable_key,
    read_polars_input,
    registry_capabilities,
    resolve_input_mode,
    resolve_output_mode,
    validate_data_input_config,
    write_polars_output,
)
from haute._polars_io_schema import io_functions_by_key
from haute.errors import BoundedMemoryUnsupportedError, SchemaMismatchError
from haute.execution import execute_lazy_graph
from haute.executor import _build_node_fn
from tests.conftest import make_graph


def test_partitioned_parquet_prunes_partition_and_columns_before_execution(
    haute_scratch: Path,
) -> None:
    dataset = haute_scratch / "partitioned"
    for year, value in ((2024, 10), (2025, 20)):
        partition = dataset / f"year={year}"
        partition.mkdir(parents=True)
        pl.DataFrame(
            {
                "value": [value],
                "unused_payload": ["x" * 1_024],
            }
        ).write_parquet(partition / "part.parquet")

    config = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": str(dataset),
        "arguments": {"hive_partitioning": True},
        "code": "df = df.filter(pl.col('year') == 2025)",
    }
    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": config,
                    },
                },
                {
                    "id": "target",
                    "data": {
                        "label": "target",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = source.select('value')",
                            "contract": {
                                "inputs": ["value"],
                                "outputs": ["value"],
                            },
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "source-target",
                    "source": "source",
                    "target": "target",
                }
            ],
        }
    )
    context = ExecutionContext(
        operation="partitioned-parquet",
        profile=ExecutionProfile.LAZY_SINK,
    )

    outputs, *_ = execute_lazy_graph(
        graph,
        _build_node_fn,
        target_node_id="target",
        required_columns_by_node={"target": {"value"}},
        execution_context=context,
    )
    output = outputs["target"]
    assert isinstance(output, pl.LazyFrame)
    optimised_plan = output.explain(optimized=True)

    assert 'col("year")' in optimised_plan
    assert "2025" in optimised_plan
    assert "unused_payload" not in optimised_plan
    assert output.collect().to_dict(as_series=False) == {"value": [20]}


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://user:password@db.example/pricing",
        "postgresql://db.example/pricing?access_token=secret",
        "postgresql://db.example/pricing?PASSWORD=secret",
        "postgresql://db.example/pricing?sig=secret",
        "postgresql://db.example/pricing?x-amz-credential=secret",
        "postgresql://db.example/pricing?authorization=secret",
        "postgresql://db.example/pricing?AWS_ACCESS_KEY_ID=secret",
        "postgresql://db.example/pricing?SharedAccessSignature=secret",
    ],
)
def test_database_config_rejects_inline_uri_credentials(uri: str) -> None:
    with pytest.raises(PolarsIoConfigError, match="credentials"):
        validate_data_input_config(
            {
                "inputType": "database",
                "format": "database",
                "uri": uri,
                "query": "SELECT 1",
            }
        )


def test_file_parquet_scan_is_derived_direct_and_stored_cache_mode_is_rejected() -> None:
    config = {
        "inputType": "file",
        "format": "parquet",
        "path": "input.parquet",
    }
    assert validate_data_input_config(config) == config
    assert data_input_is_direct(config)
    assert not data_input_is_direct({**config, "mode": "read"})

    with pytest.raises(PolarsIoConfigError, match="Field 'cacheMode' is not valid"):
        validate_data_input_config({**config, "cacheMode": "direct"})


@pytest.mark.parametrize(
    "config",
    [
        {"inputType": "file", "format": "csv", "mode": "scan", "path": "input.csv"},
        {
            "inputType": "database",
            "format": "database",
            "connection": "DATABASE_URL",
            "query": "SELECT 1",
        },
        {"inputType": "lakehouse", "format": "delta", "path": "lake/table"},
        {
            "inputType": "databricks",
            "http_path": "/sql/1.0/warehouses/abc",
            "table": "main.pricing.policies",
        },
        {"inputType": "inline", "format": "records", "records": [{"id": 1}]},
    ],
)
def test_non_parquet_inputs_are_derived_snapshot_backed(config: dict[str, object]) -> None:
    assert validate_data_input_config(config) == config
    assert not data_input_is_direct(config)


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

    @pytest.mark.parametrize("extension", ["csv", ".CSV", "", ".csv/backup"])
    def test_registry_rejects_noncanonical_extensions(self, extension: str) -> None:
        with pytest.raises(ValueError, match="lower-case leading-dot suffix"):
            IoFormat(
                name="bad",
                label="Bad",
                source_kind="path",
                reader="read_csv",
                extensions=(extension,),
            )

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
                    assert "sinked_paths_callback" not in allowed
                    assert not (fmt.source_owned_args & allowed)
                    if name.startswith("sink_"):
                        assert "lazy" not in allowed
                        assert "optimizations" not in allowed

    def test_registry_capabilities_payload_shape(self) -> None:
        payload = registry_capabilities()
        assert payload["schema_version"] == 1
        groups = {group["name"]: group for group in payload["groups"]}
        assert groups["file"]["output_fields"][0]["label"] == "Filename or path"
        assert groups["file"]["cache_modes"] == ["direct", "snapshot"]
        assert all(
            group["cache_modes"] == ["snapshot"] for name, group in groups.items() if name != "file"
        )
        formats = {
            entry["name"]: entry for group in payload["groups"] for entry in group["formats"]
        }
        assert set(formats) == set(FORMATS_BY_NAME)
        csv = formats["csv"]
        assert csv["input"]["modes"] == ["scan"]
        assert "schema_overrides" in csv["input"]["arguments"]["scan"]
        assert csv["input"]["engines_missing"] == []
        assert formats["parquet"]["input"]["modes"] == ["scan"]
        assert formats["parquet"]["input"]["cache_mode"] == "direct"
        json_format = formats["json"]
        assert json_format["input"]["modes"] == ["read"]
        assert json_format["input"]["cache_mode"] == "snapshot"
        delta = formats["delta"]
        # Core haute ships no deltalake engine: the capability payload must
        # say so rather than pretending delta is runnable.
        assert delta["input"]["engines_missing"] == ["deltalake"]
        # A Delta table is a folder; every other format's source is a file.
        assert [name for name, entry in formats.items() if entry["input"]["source_is_folder"]] == [
            "delta"
        ]
        records = formats["records"]
        assert records["input"]["snapshot_build"] == "bounded"
        assert records["input"]["cached_read"] is True


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

    @pytest.mark.parametrize(
        ("config_format", "argument"),
        [("csv", "rechunk"), ("csv", "n_threads"), ("parquet", "allow_missing_columns")],
    )
    def test_an_argument_polars_2_removed_is_refused_by_name(
        self, haute_scratch, config_format: str, argument: str
    ) -> None:
        path = haute_scratch / f"t.{config_format}"
        frame = pl.DataFrame({"id": [1, 2]})
        if config_format == "csv":
            frame.write_csv(path)
        else:
            frame.write_parquet(path)

        with pytest.raises(PolarsIoConfigError, match=rf"Unknown or unsupported .*'{argument}'"):
            read_polars_input(
                {"format": config_format, "path": str(path), "arguments": {argument: True}}
            )

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


class TestSnapshotBuildReads:
    """Build-only complete inspection and the scanner preference."""

    @pytest.mark.parametrize("arguments", [{}, {"schema": None}], ids=["unset", "null-schema"])
    def test_a_csv_without_a_declared_schema_scans_with_whole_file_inference(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        arguments: dict[str, object],
    ) -> None:
        from haute._polars_io_registry import read_polars_input_for_snapshot

        path = tmp_path / "rows.csv"
        path.write_text("id\n1\n2\n", encoding="utf-8")
        captured: dict[str, object] = {}
        real_scan = pl.scan_csv

        # wraps keeps scan_csv's signature visible to argument validation.
        @functools.wraps(real_scan)
        def recording_scan(*args: object, **kwargs: object) -> pl.LazyFrame:
            captured.update(kwargs)
            return real_scan(*args, **kwargs)

        monkeypatch.setattr(pl, "scan_csv", recording_scan)
        config = {"inputType": "file", "format": "csv", "path": str(path), "arguments": arguments}

        frame, warning_code = read_polars_input_for_snapshot(config)

        assert captured["infer_schema_length"] is None
        assert warning_code is None
        assert frame.collect().height == 2

    @pytest.mark.parametrize("arguments", [{}, {"schema": None}], ids=["unset", "null-schema"])
    def test_a_direct_bounded_read_still_refuses_an_undeclared_csv_schema(
        self,
        tmp_path: Path,
        arguments: dict[str, object],
    ) -> None:
        from haute.errors import BoundedMemoryUnsupportedError

        path = tmp_path / "rows.csv"
        path.write_text("id\n1\n", encoding="utf-8")
        config = {"inputType": "file", "format": "csv", "path": str(path), "arguments": arguments}

        with pytest.raises(BoundedMemoryUnsupportedError, match="declared 'schema'"):
            read_polars_input(config, profile=ExecutionProfile.LAZY_SINK)

    def test_a_configured_eager_read_is_scanned_when_every_argument_is_accepted(self) -> None:
        from haute._polars_io_registry import snapshot_input_plan

        fmt = FORMATS_BY_NAME["csv"]
        config = {"format": "csv", "mode": "read", "arguments": {"separator": ";"}}

        assert snapshot_input_plan(fmt, config) == ("scan", "bounded", "eager_read_mode_scanned")

    def test_a_reader_only_argument_keeps_the_admitted_eager_class(self) -> None:
        from haute._polars_io_registry import snapshot_input_plan

        fmt = FORMATS_BY_NAME["csv"]
        reader_only = sorted(
            allowed_arguments(fmt, "polars", "read_csv")
            - allowed_arguments(fmt, "polars", "scan_csv")
        )
        assert reader_only, "the installed polars read_csv must own at least one argument"
        config = {"format": "csv", "mode": "read", "arguments": {reader_only[0]: 1}}

        assert snapshot_input_plan(fmt, config) == ("read", "admitted_eager", None)

    def test_a_scanner_narrowed_argument_value_keeps_the_eager_read(self) -> None:
        from haute._polars_io_registry import snapshot_input_plan

        fmt = FORMATS_BY_NAME["csv"]
        # ``scan_csv`` accepts the name ``encoding`` but only decodes UTF-8.
        latin = {"format": "csv", "mode": "read", "arguments": {"encoding": "latin-1"}}
        assert snapshot_input_plan(fmt, latin) == ("read", "admitted_eager", None)

        utf8 = {"format": "csv", "mode": "read", "arguments": {"encoding": "utf8"}}
        assert snapshot_input_plan(fmt, utf8) == ("scan", "bounded", "eager_read_mode_scanned")

    def test_input_snapshot_build_class_reports_the_effective_class(
        self,
        tmp_path: Path,
    ) -> None:
        from haute._input_providers import input_snapshot_build_class

        path = tmp_path / "rows.csv"
        path.write_text("id\n1\n", encoding="utf-8")
        scanned = {
            "inputType": "file",
            "format": "csv",
            "mode": "read",
            "path": str(path),
            "arguments": {"separator": ","},
        }
        assert (
            input_snapshot_build_class(
                scanned, base_dir=tmp_path, profile=ExecutionProfile.LAZY_SINK
            )
            == "bounded"
        )

        json_path = tmp_path / "rows.json"
        json_path.write_text('[{"id": 1}]', encoding="utf-8")
        eager = {"inputType": "file", "format": "json", "mode": "read", "path": str(json_path)}
        assert (
            input_snapshot_build_class(
                eager,
                base_dir=tmp_path,
                profile=ExecutionProfile.LAZY_SINK,
                allow_admitted_eager=True,
            )
            == "admitted_eager"
        )


class TestColumnsAreNamedByTheHeaderOrTheConfig:
    """Polars 2 names unnamed columns column_0, ...; a Haute input never relies on them."""

    @pytest.mark.parametrize(
        ("config_format", "suffix", "how"),
        [
            ("csv", "csv", "with new_columns, or a full schema"),
            ("excel", "xlsx", "with new_columns"),
            ("ods", "ods", "with new_columns"),
        ],
    )
    def test_a_headerless_input_without_names_is_refused(
        self, config_format: str, suffix: str, how: str
    ) -> None:
        config = {
            "inputType": "file",
            "format": config_format,
            "path": f"quotes.{suffix}",
            "arguments": {"has_header": False},
        }

        with pytest.raises(PolarsIoConfigError, match=f"must name every column, in order, {how}:"):
            validate_data_input_config(config)

    @pytest.mark.parametrize("names", [[], ["id", "id"], ["id", ""], "id"])
    def test_new_columns_must_name_each_column_once(self, names: object) -> None:
        config = {
            "inputType": "file",
            "format": "csv",
            "path": "quotes.csv",
            "arguments": {"has_header": False, "new_columns": names},
        }

        with pytest.raises(PolarsIoConfigError, match="each column's name once, in order"):
            validate_data_input_config(config)

    def test_a_headerless_csv_reads_with_every_column_named(self, haute_scratch) -> None:
        path = haute_scratch / "headerless.csv"
        path.write_bytes(b"1,a,10\n2,b,20\n")

        named = read_polars_input(
            {
                "format": "csv",
                "path": str(path),
                "arguments": {"has_header": False, "new_columns": ["id", "name", "amount"]},
            }
        ).collect()
        typed = read_polars_input(
            {
                "format": "csv",
                "path": str(path),
                "arguments": {
                    "has_header": False,
                    "schema": {"id": "int64", "name": "str", "amount": "int64"},
                },
            }
        ).collect()

        assert named.columns == ["id", "name", "amount"]
        assert typed.rows() == [(1, "a", 10), (2, "b", 20)]

    def test_new_columns_naming_some_columns_is_refused_with_the_counts(
        self, haute_scratch
    ) -> None:
        path = haute_scratch / "headerless.csv"
        path.write_bytes(b"1,a,10\n2,b,20\n")

        with pytest.raises(PolarsIoConfigError, match="has 3 columns and new_columns names 2"):
            read_polars_input(
                {
                    "format": "csv",
                    "path": str(path),
                    "arguments": {"has_header": False, "new_columns": ["id", "name"]},
                }
            )

    def test_a_schema_that_renames_by_position_is_refused(self, haute_scratch) -> None:
        path = haute_scratch / "header.csv"
        path.write_bytes(b"id,name,amount\n1,a,10\n")

        with pytest.raises(
            PolarsIoConfigError,
            match=r"schema names \['x', 'y', 'z'\] the header does not have.*use new_columns.*"
            r"use schema_overrides",
        ):
            read_polars_input(
                {
                    "format": "csv",
                    "path": str(path),
                    "arguments": {"schema": {"x": "int64", "y": "str", "z": "int64"}},
                }
            )

    def test_a_schema_in_another_order_reads_each_column_by_name(self, haute_scratch) -> None:
        path = haute_scratch / "header.csv"
        path.write_bytes(b"id,name,amount\n1,a,10\n")

        out = read_polars_input(
            {
                "format": "csv",
                "path": str(path),
                "arguments": {"schema": {"amount": "int64", "id": "int64", "name": "str"}},
            }
        ).collect()

        assert out.to_dicts() == [{"amount": 10, "id": 1, "name": "a"}]

    def test_a_windows_1252_csv_is_checked_as_the_eager_reader_decodes_it(
        self, haute_scratch
    ) -> None:
        """The scanner decodes only UTF-8, so the header check decodes the file itself."""
        headerless = haute_scratch / "headerless.csv"
        headerless.write_bytes("1,Zo\u00eb,10\n2,Bront\u00eb,20\n".encode("cp1252"))
        headed = haute_scratch / "headed.csv"
        headed.write_bytes("id,caf\u00e9,amount\n1,a,10\n".encode("cp1252"))

        def read(path: Path, **arguments: object) -> pl.DataFrame:
            config = {
                "format": "csv",
                "mode": "read",
                "path": str(path),
                "arguments": {"encoding": "windows-1252", **arguments},
            }
            return read_polars_input(config).collect()

        named = read(headerless, has_header=False, new_columns=["id", "name", "amount"])
        typed = read(headed, schema={"amount": "int64", "caf\u00e9": "str", "id": "int64"})

        assert named["name"].to_list() == ["Zo\u00eb", "Bront\u00eb"]
        assert typed.to_dicts() == [{"amount": 10, "caf\u00e9": "a", "id": 1}]
        with pytest.raises(PolarsIoConfigError, match="has 3 columns and new_columns names 2"):
            read(headerless, has_header=False, new_columns=["id", "name"])
        with pytest.raises(PolarsIoConfigError, match=r"schema names \['cafe'\] the header"):
            read(headed, schema={"amount": "int64", "cafe": "str", "id": "int64"})

    def test_a_header_beyond_the_first_read_of_a_windows_1252_csv_is_found(
        self, haute_scratch
    ) -> None:
        path = haute_scratch / "preamble.csv"
        preamble = "".join(f"report line {index:05d}, caf\u00e9\n" for index in range(4000))
        path.write_bytes((preamble + "id,name\n1,a\n").encode("cp1252"))
        config = {
            "format": "csv",
            "mode": "read",
            "path": str(path),
            "arguments": {"encoding": "windows-1252", "skip_lines": 4000},
        }

        typed = read_polars_input(
            {
                **config,
                "arguments": {**config["arguments"], "schema": {"name": "str", "id": "int64"}},
            }
        ).collect()

        assert typed.to_dicts() == [{"name": "a", "id": 1}]
        with pytest.raises(PolarsIoConfigError, match=r"leaves out \['name'\]"):
            read_polars_input(
                {**config, "arguments": {**config["arguments"], "schema": {"id": "int64"}}}
            )

    def test_haute_names_a_headerless_sheet(self, haute_scratch) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        path = haute_scratch / "headerless.xlsx"
        workbook = openpyxl.Workbook()
        for row in ([1, "a"], [2, "b"]):
            workbook.active.append(row)
        workbook.save(path)

        def read(names: list[str]) -> pl.DataFrame:
            arguments = {"engine": "openpyxl", "has_header": False, "new_columns": names}
            config = {"format": "excel", "path": str(path), "arguments": arguments}
            return read_polars_input(config).collect()

        assert read(["id", "name"]).to_dicts() == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        with pytest.raises(
            PolarsIoConfigError, match="sheet has 2 columns and new_columns names 3"
        ):
            read(["id", "name", "extra"])

    def test_spreadsheets_publish_the_naming_argument(self) -> None:
        formats = {
            fmt["name"]: fmt
            for group in registry_capabilities()["groups"]
            for fmt in group["formats"]
        }

        for name in ("excel", "ods"):
            assert "new_columns" in formats[name]["input"]["arguments"]["read"]
