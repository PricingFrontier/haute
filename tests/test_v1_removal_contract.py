"""Contract tests for the v1-removal pivot (commit 5.5).

This file is the verification contract for the v1-apiInput-surface removal.
Per the handover (`worktree-data-model/handovers/v1-removal-...`), every
test here is written FIRST as a failing test (TDD strict-upfront) and
must pass after the implementation lands.

Test ID mapping (T1-T22 per handover):
  - T1: save fresh apiInput -> no auto-written flattenSchema
  - T3: JsonCacheBuildRequest.volatile_schema field exists
  - T4: /api/json-cache/build dispatches on volatile_schema vs disk
  - T6: multi emit:true tables -> one parquet per table, correct columns
  - T7: parquet footer kv_metadata carries per-table v2 schema
  - T8: malformed volatile_schema -> 422 with structured ApiInputSchemaError
  - T13: validate_v2_schema rejects unknown col.type
  - T14: validate_v2_schema rejects sanitised-label collision
  - T15: validator + path parsers raise ApiInputSchemaError(HauteError)
  - T16: corrupt-mix (tables + flattenSchema) uses tables, no error
  - T17: save apiInput with empty tables -> SavePipelineResponse.warnings
  - T18: legacy_to_v2 import raises ImportError
  - T19: no file under tests/fixtures/ contains "flattenSchema"
  - T20: _json_flatten_schema module raises ImportError on import

Frontend tests (T2, T5, T9/T10, T21, T22) live in
`frontend/src/__tests__/editors/v1RemovalContract.test.tsx`.

Playwright tests (T11 migration validation, T12 v2-native persistence)
live under `frontend/e2e/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ─── Backend fixtures ────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd chdir'd to tmp_path for filesystem isolation."""
    monkeypatch.chdir(tmp_path)
    from haute.server import app

    return TestClient(app)


def _write_quotes_json(tmp_path: Path) -> Path:
    """Synthetic two-table JSON: $[:] with a quote_id, plus $[:].drivers[:]."""
    data_path = tmp_path / "data" / "quotes.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(
        json.dumps(
            [
                {
                    "quote_id": "q1",
                    "drivers": [{"id": "d1", "name": "Alice"}, {"id": "d2", "name": "Bob"}],
                },
                {"quote_id": "q2", "drivers": [{"id": "d3", "name": "Carol"}]},
            ]
        ),
        encoding="utf-8",
    )
    return data_path


def _v2_config_two_tables(data_relpath: str) -> dict:
    """V2 config with two emit:true tables — root + drivers child."""
    return {
        "path": data_relpath,
        "tables": [
            {
                "path": "$[:]",
                "label": "quotes",
                "emit": True,
                "columns": [
                    {
                        "name": "quote_id",
                        "path": "$[:].quote_id",
                        "type": "str",
                        "selected": True,
                    }
                ],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "id",
                        "path": "$[:].drivers[:].id",
                        "type": "str",
                        "selected": True,
                    },
                    {
                        "name": "name",
                        "path": "$[:].drivers[:].name",
                        "type": "str",
                        "selected": True,
                    },
                ],
            },
        ],
    }


# ─── T1 — save fresh apiInput → no auto-written schema ────────────────


def test_t1_save_fresh_api_input_does_not_auto_write_flatten_schema(
    client: TestClient, tmp_path: Path
) -> None:
    """Saving an apiInput with no schema on disk must NOT auto-inject `flattenSchema`.

    Today: `_infer_flatten_schemas` reads the JSON, infers v1 flatten
    schema, and writes it into the apiInput node's config. Post-removal,
    the rename target `_validate_api_inputs_have_schemas` only emits a
    save-response warning; no on-disk mutation.

    Asserts at the persistent boundary (the on-disk JSON), not at the
    call argument, per AGENTS.md §UI Test Assertions principle 1.
    """
    _data_path = _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"

    save_body = {
        "name": "fresh_apiinput",
        "description": "",
        "source_file": "fresh_apiinput.py",
        "graph": {
            "nodes": [
                {
                    "id": "quotes",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {"path": rel_data},
                    },
                }
            ],
            "edges": [],
        },
    }
    resp = client.post("/api/pipeline/save", json=save_body)
    assert resp.status_code == 200, resp.text

    config_file = tmp_path / "config" / "quote_input" / "quotes.json"
    assert config_file.exists(), "save should write the apiInput config file"
    persisted = json.loads(config_file.read_text())

    # Negative invariant: no v1 surface was synthesised at save time.
    assert "flattenSchema" not in persisted, (
        f"save must not auto-inject flattenSchema; got persisted={persisted!r}"
    )
    # Positive invariant: only the user-provided keys remain.
    assert persisted == {"path": rel_data}


# ─── T3 — JsonCacheBuildRequest.volatile_schema exists ────────────────


def test_t3_json_cache_build_request_accepts_volatile_schema() -> None:
    """`JsonCacheBuildRequest` exposes `volatile_schema: dict | None = None`.

    Today: the Pydantic model has `flatten_schema` (v1 inline) but no
    `volatile_schema`. The new field carries the editor's in-memory v2
    `tables[]` shape directly into the cache build call (working
    principle 4: volatile vs persistent at the schema plane mirrors
    PR13's data plane).
    """
    from haute.schemas import JsonCacheBuildRequest

    # Field declared with the right name + default.
    fields = JsonCacheBuildRequest.model_fields
    assert "volatile_schema" in fields, "volatile_schema field must exist"
    assert fields["volatile_schema"].default is None
    # Model accepts the field with a dict value.
    instance = JsonCacheBuildRequest(path="data.json", volatile_schema={"tables": []})
    assert instance.volatile_schema == {"tables": []}
    # And accepts None (the default fallback to disk-read path).
    instance_none = JsonCacheBuildRequest(path="data.json")
    assert instance_none.volatile_schema is None


# ─── T4 — /api/json-cache/build dispatch on volatile_schema vs disk ───


def test_t4_cache_build_uses_volatile_schema_when_present(
    client: TestClient, tmp_path: Path
) -> None:
    """When `volatile_schema is not None`, route uses it over disk config.

    Even an empty dict ({}) should be treated as "user provided this"
    (handover spec calls out "NOT just truthy"). An empty `volatile_schema`
    differs from None — None means "fall back to disk", {} means "user
    explicitly passed nothing" (probably an editor bug that we want to
    surface as an error, not silently fall through).

    Asserts the dispatch by setting up a v2 schema in volatile_schema
    that differs from the on-disk config; the cache built should match
    volatile_schema's tables, not the disk's.
    """
    _data_path = _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"

    # Write a v2 config to disk with ONLY the root table.
    disk_only_root = {
        "path": rel_data,
        "tables": [
            {
                "path": "$[:]",
                "label": "quotes_disk",
                "emit": True,
                "columns": [{"name": "quote_id", "path": "$[:].quote_id", "type": "str"}],
            }
        ],
    }
    config_file = tmp_path / "config" / "quote_input" / "quotes.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(disk_only_root))

    # Volatile schema with TWO tables (root + drivers child).
    volatile = _v2_config_two_tables(rel_data)
    resp = client.post(
        "/api/json-cache/build",
        json={
            "path": rel_data,
            "config_path": "config/quote_input/quotes.json",
            "volatile_schema": volatile,
        },
    )
    assert resp.status_code == 200, resp.text

    # The cache should have built using volatile_schema -> two parquet
    # files exist (one per emit:true table). The disk-config-only-root
    # would have produced ONE parquet, so the count distinguishes the
    # two dispatch paths.
    # The cache layout is `.haute_cache/<layer>/<json_<hash>>/*.parquet` —
    # glob recursively to find them regardless of the hash directory name.
    cache_dir = tmp_path / ".haute_cache"
    parquets = list(cache_dir.rglob("*.parquet"))
    assert len(parquets) == 2, (
        f"volatile_schema dispatch should produce 2 parquets; got {[p.name for p in parquets]}"
    )


def test_t4_cache_build_falls_back_to_disk_when_volatile_is_none(
    client: TestClient, tmp_path: Path
) -> None:
    """When `volatile_schema is None`, route reads config_path from disk."""
    _data_path = _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"

    # Write a v2 config to disk with two tables.
    on_disk = _v2_config_two_tables(rel_data)
    config_file = tmp_path / "config" / "quote_input" / "quotes.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(on_disk))

    resp = client.post(
        "/api/json-cache/build",
        json={
            "path": rel_data,
            "config_path": "config/quote_input/quotes.json",
            # volatile_schema omitted = None
        },
    )
    assert resp.status_code == 200, resp.text

    # The cache layout is `.haute_cache/<layer>/<json_<hash>>/*.parquet` —
    # glob recursively to find them regardless of the hash directory name.
    cache_dir = tmp_path / ".haute_cache"
    parquets = list(cache_dir.rglob("*.parquet"))
    assert len(parquets) == 2, (
        f"disk dispatch should produce 2 parquets; got {[p.name for p in parquets]}"
    )


# ─── T6 — multi-port emit:true ───────────────────────────────────────


def test_t6_multiple_emit_true_tables_produce_one_parquet_each(
    client: TestClient, tmp_path: Path
) -> None:
    """Two emit:true tables → two parquets; columns match their table spec."""
    import polars as pl

    _data_path = _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"
    cfg = _v2_config_two_tables(rel_data)

    resp = client.post(
        "/api/json-cache/build",
        json={"path": rel_data, "volatile_schema": cfg},
    )
    assert resp.status_code == 200, resp.text

    cache_dir = tmp_path / ".haute_cache"
    parquets = {p.stem: p for p in cache_dir.rglob("*.parquet")}
    # Filenames derive from sanitised labels.
    assert "quotes" in parquets
    assert "drivers" in parquets

    df_quotes = pl.read_parquet(parquets["quotes"])
    df_drivers = pl.read_parquet(parquets["drivers"])

    # quotes table: one quote_id column, two rows (q1, q2).
    assert set(df_quotes.columns) == {"quote_id"}
    assert df_quotes.height == 2

    # drivers table: id + name columns, three rows (d1, d2, d3).
    assert set(df_drivers.columns) == {"id", "name"}
    assert df_drivers.height == 3


# ─── T7 — parquet footer kv_metadata carries v2 schema ────────────────


def test_t7_parquet_footer_carries_per_table_v2_schema(client: TestClient, tmp_path: Path) -> None:
    """Each emit:true parquet's footer carries its per-table v2 schema slice.

    The handover requires the on-disk parquet to embed enough schema
    info to round-trip without re-reading the JSON config — a downstream
    consumer reading the parquet directly should be able to discover the
    column types and path mapping.
    """
    import pyarrow.parquet as pq

    _data_path = _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"
    cfg = _v2_config_two_tables(rel_data)

    resp = client.post(
        "/api/json-cache/build",
        json={"path": rel_data, "volatile_schema": cfg},
    )
    assert resp.status_code == 200, resp.text

    cache_dir = tmp_path / ".haute_cache"
    drivers_candidates = list(cache_dir.rglob("drivers.parquet"))
    assert drivers_candidates, "drivers parquet should exist post-build"
    drivers_parquet = drivers_candidates[0]

    # Read the parquet's footer key-value metadata directly. The per-port
    # shred attaches `b"haute_per_frame_schema"` carrying the table's
    # label + columns ({name, leaf, type}). A downstream consumer reading
    # the parquet alone can reconstruct the schema without re-reading
    # the apiInput config.
    arrow_schema = pq.read_schema(str(drivers_parquet))
    kv = arrow_schema.metadata or {}
    payload_bytes = kv.get(b"haute_per_frame_schema")
    assert payload_bytes is not None, (
        f"parquet footer must carry b'haute_per_frame_schema'; got keys={list(kv)!r}"
    )
    payload = json.loads(payload_bytes)
    assert payload.get("port_label") == "drivers"
    col_names = {c["name"] for c in payload.get("columns", [])}
    assert col_names == {"id", "name"}, (
        f"per-frame schema should list id and name columns; got {col_names!r}"
    )


# ─── T8 — malformed volatile_schema → 422 + structured body ───────────


def test_t8_malformed_volatile_schema_returns_structured_422(
    client: TestClient, tmp_path: Path
) -> None:
    """Malformed `volatile_schema` -> 422 with {detail, type:"ApiInputSchemaError"}.

    Today: a bare ValueError from the validator becomes a vague 422
    with a string message. The new contract:
      - status code 422
      - response.json() includes a `type` discriminator equal to
        "ApiInputSchemaError"
      - response.json()["detail"] is a non-empty string

    Frontend can branch on `type` without string-matching on `detail`.
    """
    _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"

    # Several malformed shapes — each should 422 with the discriminator.
    malformed_cases = [
        # Not a dict.
        "not a dict",
        # Dict with no tables key.
        {"some_other_key": "x"},
        # Tables key not a list.
        {"tables": "not a list"},
        # Table missing required `path` key.
        {"tables": [{"label": "x", "emit": True, "columns": []}]},
        # Unknown column type.
        {
            "tables": [
                {
                    "path": "$[:]",
                    "label": "x",
                    "emit": True,
                    "columns": [{"name": "n", "path": "$[:].n", "type": "stirng"}],
                }
            ]
        },
    ]

    for bad in malformed_cases:
        resp = client.post(
            "/api/json-cache/build",
            json={"path": rel_data, "volatile_schema": bad},
        )
        assert resp.status_code == 422, (
            f"malformed volatile_schema must 422; got {resp.status_code} for {bad!r}"
        )
        body = resp.json()
        assert body.get("type") == "ApiInputSchemaError", (
            f"422 body must carry type=ApiInputSchemaError discriminator; got body={body!r}"
        )
        assert isinstance(body.get("detail"), str) and body["detail"], (
            f"422 body must have non-empty detail string; got body={body!r}"
        )


# ─── T13 — validate_v2_schema rejects unknown col.type ───────────────


def test_t13_validate_v2_schema_rejects_unknown_col_type() -> None:
    """`validate_v2_schema` raises ApiInputSchemaError on unknown col.type.

    Today: `_json_shred.py:278` silently downgrades unknown types to
    `pl.String`. This silently corrupts data when a user typoes a type
    (e.g. "stirng" instead of "str"). Guardrail B1 forces an early loud
    failure.

    Allowed types per the v2 contract: int, float, str, bool, date.
    """
    from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema

    cfg_bad_type = {
        "tables": [
            {
                "path": "$[:]",
                "label": "x",
                "emit": True,
                "columns": [{"name": "n", "path": "$[:].n", "type": "stirng"}],
            }
        ]
    }
    with pytest.raises(ApiInputSchemaError):
        validate_v2_schema(cfg_bad_type)


# ─── T14 — sanitised-label collision rejected ────────────────────────


def test_t14_validate_v2_schema_rejects_sanitised_label_collision() -> None:
    """Two table labels sanitising to the same parquet filename → reject.

    Today: `build_per_port_cache` silently overwrites the parquet — the
    second write clobbers the first, the first table's data is lost
    after the cache builds. Guardrail B2.

    The sanitisation rule (per `_FILESYSTEM_SAFE` / new
    `_sanitise_label_for_filesystem`): non-filesystem-safe characters
    collapse to a single character. Two distinct labels that produce
    the same sanitised name must be rejected at validate-time.
    """
    from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema

    cfg_collision = {
        "tables": [
            {
                "path": "$[:].a",
                "label": "my$table",  # $ → underscore
                "emit": True,
                "columns": [{"name": "x", "path": "$[:].a.x", "type": "str"}],
            },
            {
                "path": "$[:].b",
                "label": "my%table",  # % → underscore (collision: both → "my_table")
                "emit": True,
                "columns": [{"name": "y", "path": "$[:].b.y", "type": "str"}],
            },
        ]
    }
    with pytest.raises(ApiInputSchemaError):
        validate_v2_schema(cfg_collision)


# ─── T15 — validator + path parsers raise ApiInputSchemaError ────────


def test_t15_validate_v2_schema_raises_api_input_schema_error() -> None:
    """validate_v2_schema raises ApiInputSchemaError(HauteError), not ValueError.

    The bare ValueError contract is the current source of the json_cache
    route's "Invalid v2 schema: <e>" 422 message catching too broadly.
    Switching to a typed exception lets the route catch specifically
    (T8 contract).
    """
    from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
    from haute.errors import HauteError

    assert issubclass(ApiInputSchemaError, HauteError)

    with pytest.raises(ApiInputSchemaError):
        validate_v2_schema({"tables": "not a list"})


def test_t15_parse_table_path_raises_api_input_schema_error() -> None:
    """`parse_table_path` raises ApiInputSchemaError on malformed input."""
    from haute._api_input_schema import ApiInputSchemaError, parse_table_path

    with pytest.raises(ApiInputSchemaError):
        parse_table_path("drivers[:]")  # missing $[:] prefix


def test_t15_parse_column_path_raises_api_input_schema_error() -> None:
    """`parse_column_path` raises ApiInputSchemaError on malformed input."""
    from haute._api_input_schema import ApiInputSchemaError, parse_column_path

    with pytest.raises(ApiInputSchemaError):
        parse_column_path("drivers.id", "$[:].drivers[:]")


# ─── T16 — corrupt-mix (tables + flattenSchema) tolerated ────────────


def test_t16_corrupt_mix_uses_tables_ignores_flatten_schema(
    client: TestClient, tmp_path: Path
) -> None:
    """Config file with BOTH tables[] AND flattenSchema → use tables, no error.

    Per D9: "as if v1 doesn't exist" applies even when v1 keys are still
    present alongside v2 keys. flattenSchema is silently ignored; cache
    builds from tables[]. No error, no warning at the cache plane.
    """
    _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"

    corrupt_mix = _v2_config_two_tables(rel_data)
    corrupt_mix["flattenSchema"] = {"some": "v1 key that should be ignored"}

    config_file = tmp_path / "config" / "quote_input" / "quotes.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps(corrupt_mix))

    resp = client.post(
        "/api/json-cache/build",
        json={
            "path": rel_data,
            "config_path": "config/quote_input/quotes.json",
        },
    )
    assert resp.status_code == 200, resp.text

    # The cache layout is `.haute_cache/<layer>/<json_<hash>>/*.parquet` —
    # glob recursively to find them regardless of the hash directory name.
    cache_dir = tmp_path / ".haute_cache"
    parquets = list(cache_dir.rglob("*.parquet"))
    assert len(parquets) == 2  # two emit:true tables in `tables[]`


# ─── T17 — save empty tables → warnings ───────────────────────────────


def test_t17_save_empty_tables_emits_save_response_warning(
    client: TestClient, tmp_path: Path
) -> None:
    """JSON apiInput with empty tables[] → SavePipelineResponse.warnings non-empty.

    Per D2 + B5: empty tables is a non-blocking state (pipeline can be
    saved without being functional) but the user gets a navigational
    hint via the warning ledger. Message must reference the node label
    and "Infer Tables" so the user knows what to click.
    """
    _data_path = _write_quotes_json(tmp_path)
    rel_data = "data/quotes.json"

    save_body = {
        "name": "empty_tables",
        "description": "",
        "source_file": "empty_tables.py",
        "graph": {
            "nodes": [
                {
                    "id": "quotes",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "quotes",
                        "nodeType": "apiInput",
                        "config": {"path": rel_data, "tables": []},
                    },
                }
            ],
            "edges": [],
        },
    }
    resp = client.post("/api/pipeline/save", json=save_body)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    warnings = body.get("warnings") or []
    # Find a warning that mentions the node label + Infer Tables.
    matching = [w for w in warnings if "quotes" in w and "Infer Tables" in w]
    assert matching, (
        "save response should warn about empty tables w/ Infer Tables hint; "
        f"got warnings={warnings!r}"
    )


# ─── T18 — legacy_to_v2 import raises ImportError ────────────────────


def test_t18_legacy_to_v2_import_raises() -> None:
    """`from haute._api_input_schema import legacy_to_v2` → ImportError.

    Negative test: confirms the v1→v2 migration codec is deleted. The
    function does not have a deprecation shim; it must not be importable.
    """
    with pytest.raises(ImportError):
        from haute._api_input_schema import legacy_to_v2  # noqa: F401


# ─── T19 — no flattenSchema literal under tests/fixtures/ ────────────


def test_t19_no_flatten_schema_in_tests_fixtures() -> None:
    """No file under `tests/fixtures/` contains the literal "flattenSchema".

    Regression guard: prevents accidentally re-introducing v1 fixtures
    after v1 removal. This may pass today (the codec-WIP fixtures were
    deleted as part of the Cat B discard) — kept as a permanent
    guard.
    """
    fixtures_root = Path(__file__).parent / "fixtures"
    offenders: list[str] = []
    for path in fixtures_root.rglob("*"):
        if not path.is_file():
            continue
        # Skip binary fixtures (parquet etc) to avoid decode noise.
        if path.suffix in {".parquet", ".pyc"} or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "flattenSchema" in text:
            offenders.append(str(path.relative_to(fixtures_root)))
    assert not offenders, (
        f"tests/fixtures/ must not contain 'flattenSchema'; offenders: {offenders}"
    )


# ─── T20 — _json_flatten_schema module import raises ─────────────────


def test_t20_json_flatten_schema_module_import_raises() -> None:
    """`from haute import _json_flatten_schema` → ImportError.

    Negative test: the entire v1 flatten-schema module is deleted. The
    handover lists this as a stronger contract than just deleting one
    function — the module is gone.
    """
    with pytest.raises(ImportError):
        from haute import _json_flatten_schema  # noqa: F401
