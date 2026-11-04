"""Strict configuration contracts for the retained data I/O node types."""

from __future__ import annotations

import pytest

from haute._polars_io_registry import (
    PolarsIoConfigError,
    data_input_is_direct,
    validate_data_input_config,
    validate_data_output_config,
)


@pytest.mark.parametrize(
    "config",
    [
        {
            "input_type": "file",
            "format": "csv",
            "mode": "scan",
            "path": "data/input.csv",
            "arguments": {"schema": {"id": "int64"}},
            "code": "df = df.filter(pl.col('id') > 0)",
        },
        {
            "input_type": "database",
            "format": "database",
            "connection": "HAUTE_TEST_DATABASE_URL",
            "query": "SELECT * FROM policies",
        },
        {
            "input_type": "lakehouse",
            "format": "delta",
            "mode": "scan",
            "path": "lake/policies",
        },
        {
            "input_type": "databricks",
            "http_path": "/sql/1.0/warehouses/abc",
            "table": "main.pricing.policies",
        },
        {
            "input_type": "inline",
            "format": "records",
            "records": [{"id": 1}],
        },
    ],
    ids=["file", "database", "lakehouse", "databricks", "inline"],
)
def test_valid_data_input_branches(config: dict[str, object]) -> None:
    assert validate_data_input_config(config) == config


@pytest.mark.parametrize(
    "config",
    [
        {
            "output_type": "file",
            "format": "parquet",
            "mode": "sink",
            "path": "out/results.parquet",
        },
        {
            "output_type": "database",
            "format": "database",
            "mode": "write",
            "connection": "HAUTE_TEST_DATABASE_URL",
            "table": "results",
        },
        {
            "output_type": "lakehouse",
            "format": "iceberg",
            "mode": "sink",
            "path": "catalog.schema.results",
        },
    ],
    ids=["file", "database", "lakehouse"],
)
def test_valid_data_output_branches(config: dict[str, object]) -> None:
    assert validate_data_output_config(config) == config


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            {
                "input_type": "file",
                "format": "csv",
                "path": "input.csv",
                "query": "SELECT 1",
            },
            "not valid for input_type 'file'",
        ),
        (
            {
                "input_type": "file",
                "format": "delta",
                "path": "lake",
            },
            "belongs to group 'lakehouse'",
        ),
        (
            {
                "input_type": "database",
                "format": "database",
                "mode": "read",
                "connection": "DATABASE_URL",
                "query": "SELECT 1",
            },
            "not valid for input_type 'database'",
        ),
        (
            {
                "input_type": "databricks",
                "mode": "read",
                "http_path": "/sql/1.0/warehouses/abc",
                "table": "main.pricing.policies",
            },
            "not valid for input_type 'databricks'",
        ),
        (
            {
                "input_type": "database",
                "format": "database",
                "uri": "postgresql://alice:secret@db.example/pricing",
                "query": "SELECT 1",
            },
            "must not contain credentials",
        ),
        (
            {
                "input_type": "file",
                "format": "parquet",
            },
            "requires a non-empty 'path'",
        ),
    ],
)
def test_invalid_data_input_branch_fails(config: dict[str, object], message: str) -> None:
    with pytest.raises(PolarsIoConfigError, match=message):
        validate_data_input_config(config)


@pytest.mark.parametrize(
    "config",
    [
        {
            "input_type": "file",
            "format": "parquet",
            "cacheMode": "direct",  # the removed field under test
            "path": "input.parquet",
        },
        {
            "input_type": "database",
            "format": "database",
            "cacheMode": "snapshot",
            "connection": "DATABASE_URL",
            "query": "SELECT 1",
        },
        {
            "input_type": "lakehouse",
            "format": "delta",
            "cacheMode": "snapshot",
            "path": "lake",
        },
        {
            "input_type": "databricks",
            "cacheMode": "snapshot",
            "http_path": "/sql/1.0/warehouses/abc",
            "table": "main.pricing.policies",
        },
        {
            "input_type": "inline",
            "format": "records",
            "cacheMode": "snapshot",
            "records": [{"id": 1}],
        },
    ],
    ids=["file", "database", "lakehouse", "databricks", "inline"],
)
def test_cache_mode_field_is_rejected_for_every_provider(
    config: dict[str, object],
) -> None:
    """The execution mode is derived, never stored — even a "correct" value is invalid."""
    with pytest.raises(
        PolarsIoConfigError,
        match="Field 'cacheMode' is not valid",
    ):
        validate_data_input_config(config)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"input_type": "file", "format": "parquet", "mode": "scan", "path": "p.parquet"}, True),
        ({"input_type": "file", "format": "parquet", "path": "p.parquet"}, True),
        ({"input_type": "file", "format": "parquet", "mode": "", "path": "p.parquet"}, True),
        ({"input_type": "file", "format": "parquet", "mode": None, "path": "p.parquet"}, True),
        ({"input_type": "file", "format": "parquet", "mode": "read", "path": "p.parquet"}, False),
        ({"input_type": "file", "format": "csv", "mode": "scan", "path": "p.csv"}, False),
        ({"input_type": "lakehouse", "format": "delta", "path": "lake"}, False),
        ({"input_type": "database", "format": "database", "query": "SELECT 1"}, False),
        ({"input_type": "databricks", "http_path": "/sql/1.0/w/abc", "table": "t"}, False),
        ({"input_type": "inline", "format": "records", "records": []}, False),
    ],
    ids=[
        "parquet-scan",
        "parquet-default-mode",
        "parquet-blank-mode",
        "parquet-null-mode",
        "parquet-read",
        "csv",
        "lakehouse",
        "database",
        "databricks",
        "inline",
    ],
)
def test_direct_execution_is_derived_from_the_provider_branch(
    config: dict[str, object], expected: bool
) -> None:
    """File-backed Parquet scans read directly; everything else snapshots."""
    assert data_input_is_direct(config) is expected


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            {
                "output_type": "file",
                "format": "parquet",
                "path": "out.parquet",
                "code": "df = df.head(1)",
            },
            "not valid for output_type 'file'",
        ),
        (
            {
                "output_type": "file",
                "format": "ods",
                "path": "out.ods",
            },
            "has no output capability",
        ),
        (
            {
                "output_type": "database",
                "format": "database",
                "uri": "postgresql://alice:secret@db.example/pricing",
                "table": "results",
            },
            "must not contain credentials",
        ),
        (
            {
                "output_type": "lakehouse",
                "format": "parquet",
                "path": "out",
            },
            "belongs to group 'file'",
        ),
    ],
)
def test_invalid_data_output_branch_fails(config: dict[str, object], message: str) -> None:
    with pytest.raises(PolarsIoConfigError, match=message):
        validate_data_output_config(config)


def test_unknown_discriminants_fail_loudly() -> None:
    with pytest.raises(PolarsIoConfigError, match="input_type"):
        validate_data_input_config({"input_type": "warehouse"})
    with pytest.raises(PolarsIoConfigError, match="output_type"):
        validate_data_output_config({"output_type": "warehouse"})
