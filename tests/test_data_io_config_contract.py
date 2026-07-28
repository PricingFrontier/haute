"""Strict configuration contracts for the retained data I/O node types."""

from __future__ import annotations

import pytest

from haute._polars_io_registry import (
    PolarsIoConfigError,
    validate_data_input_config,
    validate_data_output_config,
)


@pytest.mark.parametrize(
    "config",
    [
        {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "cacheMode": "snapshot",
            "path": "data/input.csv",
            "arguments": {"schema": {"id": "int64"}},
            "code": "df = df.filter(pl.col('id') > 0)",
        },
        {
            "inputType": "database",
            "format": "database",
            "cacheMode": "snapshot",
            "connection": "HAUTE_TEST_DATABASE_URL",
            "query": "SELECT * FROM policies",
        },
        {
            "inputType": "lakehouse",
            "format": "delta",
            "mode": "scan",
            "cacheMode": "snapshot",
            "path": "lake/policies",
        },
        {
            "inputType": "databricks",
            "cacheMode": "snapshot",
            "http_path": "/sql/1.0/warehouses/abc",
            "table": "main.pricing.policies",
        },
        {
            "inputType": "inline",
            "format": "records",
            "cacheMode": "snapshot",
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
            "outputType": "file",
            "format": "parquet",
            "mode": "sink",
            "path": "out/results.parquet",
        },
        {
            "outputType": "database",
            "format": "database",
            "mode": "write",
            "connection": "HAUTE_TEST_DATABASE_URL",
            "table": "results",
        },
        {
            "outputType": "lakehouse",
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
                "inputType": "file",
                "format": "csv",
                "cacheMode": "snapshot",
                "path": "input.csv",
                "query": "SELECT 1",
            },
            "not valid for inputType 'file'",
        ),
        (
            {
                "inputType": "file",
                "format": "delta",
                "cacheMode": "snapshot",
                "path": "lake",
            },
            "belongs to group 'lakehouse'",
        ),
        (
            {
                "inputType": "database",
                "format": "database",
                "mode": "read",
                "cacheMode": "snapshot",
                "connection": "DATABASE_URL",
                "query": "SELECT 1",
            },
            "not valid for inputType 'database'",
        ),
        (
            {
                "inputType": "databricks",
                "mode": "read",
                "cacheMode": "snapshot",
                "http_path": "/sql/1.0/warehouses/abc",
                "table": "main.pricing.policies",
            },
            "not valid for inputType 'databricks'",
        ),
        (
            {
                "inputType": "inline",
                "format": "records",
                "cacheMode": "invalid",
                "records": [],
            },
            "requires cacheMode 'snapshot'",
        ),
        (
            {
                "inputType": "database",
                "format": "database",
                "cacheMode": "snapshot",
                "uri": "postgresql://alice:secret@db.example/pricing",
                "query": "SELECT 1",
            },
            "must not contain credentials",
        ),
        (
            {
                "inputType": "file",
                "format": "parquet",
                "cacheMode": "direct",
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
            "inputType": "file",
            "format": "parquet",
            "cacheMode": "direct",
            "path": "input.parquet",
        },
        {
            "inputType": "database",
            "format": "database",
            "cacheMode": "direct",
            "connection": "DATABASE_URL",
            "query": "SELECT 1",
        },
        {
            "inputType": "lakehouse",
            "format": "delta",
            "cacheMode": "direct",
            "path": "lake",
        },
        {
            "inputType": "databricks",
            "cacheMode": "direct",
            "http_path": "/sql/1.0/warehouses/abc",
            "table": "main.pricing.policies",
        },
        {
            "inputType": "inline",
            "format": "records",
            "cacheMode": "direct",
            "records": [{"id": 1}],
        },
    ],
    ids=["file", "database", "lakehouse", "databricks", "inline"],
)
def test_direct_cache_mode_is_rejected_for_every_provider(
    config: dict[str, object],
) -> None:
    with pytest.raises(
        PolarsIoConfigError,
        match="Data Input requires cacheMode 'snapshot'",
    ):
        validate_data_input_config(config)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            {
                "outputType": "file",
                "format": "parquet",
                "path": "out.parquet",
                "code": "df = df.head(1)",
            },
            "not valid for outputType 'file'",
        ),
        (
            {
                "outputType": "file",
                "format": "ods",
                "path": "out.ods",
            },
            "has no output capability",
        ),
        (
            {
                "outputType": "database",
                "format": "database",
                "uri": "postgresql://alice:secret@db.example/pricing",
                "table": "results",
            },
            "must not contain credentials",
        ),
        (
            {
                "outputType": "lakehouse",
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
    with pytest.raises(PolarsIoConfigError, match="inputType"):
        validate_data_input_config({"inputType": "warehouse"})
    with pytest.raises(PolarsIoConfigError, match="outputType"):
        validate_data_output_config({"outputType": "databricks"})
