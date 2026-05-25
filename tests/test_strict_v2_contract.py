"""Contract tests for the strict-v2 backend bundle (Bundle 2).

Three layered defences enforce the v2 config contract at the
disk↔memory boundary:

- **α** (write-allowlist) — `_prepare_config_for_sidecar` drops any
  key not in `VALID_KEYS[node_type]` before persisting. Logs the
  drop at WARNING. Runs on every node type. Catches off-spec keys
  smuggled in by external tooling, future code paths, or a frontend
  bug that hasn't yet been hardened.

- **a** (load-time legacy strip) — `_normalise_loaded_config` for
  apiInput strips the v1-only keys `selected_columns`,
  `column_renames`, and `flattenSchema` from the dict before it
  reaches `node.data.config`. Promotes the D9 "silently ignored at
  read" tolerance to "silently stripped" so the keys cannot leak
  back out via the spread-merge in the frontend editor. Also
  applied in `_read_v2_config` for the JSON-cache build path.

- **executor guard** — `_extract_column_refs` accepts an optional
  `node_type`. When the type is `API_INPUT`, the `selected_columns`
  scoop is skipped — apiInput v2 has no concept of a
  `selected_columns` list; the per-column `selected` field inside
  `tables[].columns[]` is the v2-native surface. Defence in depth
  against any future code path that reintroduces the key.

Note. `selected_columns` and `column_renames` remain LEGITIMATE
keys for non-apiInput node types (polars transforms author them).
Stripping is apiInput-specific. α leaves them alone because they
appear in `_UNIVERSAL_KEYS`; `a` removes them only when the node
type is API_INPUT.

Pairs with the Bundle 1 contract at
``tests/test_config_validation.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from haute._config_io import (
    _normalise_loaded_config,
    _prepare_config_for_sidecar,
)
from haute._config_validation import VALID_KEYS
from haute._types import NodeType
from haute.executor import _extract_column_refs


# ---------------------------------------------------------------------------
# α — write-time allowlist
# ---------------------------------------------------------------------------


class TestPrepareConfigForSidecarAllowlist:
    def test_api_input_drops_unknown_top_level_key(self) -> None:
        """A bogus key on an apiInput config is dropped at write time."""
        config: dict[str, Any] = {
            "path": "data.json",
            "contract": "opaque",
            "tables": [],
            "bogus_key": 42,
        }
        out = _prepare_config_for_sidecar(NodeType.API_INPUT, config)
        assert "bogus_key" not in out
        assert out["path"] == "data.json"
        assert out["contract"] == "opaque"
        assert out["tables"] == []

    def test_api_input_drops_legacy_flatten_schema_at_write(self) -> None:
        """v1 flattenSchema is not in apiInput's v2 allowlist — stripped."""
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "flattenSchema": {"version": 1, "columns": []},
        }
        out = _prepare_config_for_sidecar(NodeType.API_INPUT, config)
        assert "flattenSchema" not in out

    def test_api_input_keeps_universal_keys_at_write(self) -> None:
        """Universal keys (selected_columns, column_renames) are NOT dropped by α.

        Bundle 2's load-time strip (`a`) is the layer that scrubs these
        for apiInput specifically. α leaves them because they're
        legitimate on other node types.
        """
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "selected_columns": ["foo"],
            "column_renames": {"a": "b"},
        }
        out = _prepare_config_for_sidecar(NodeType.API_INPUT, config)
        assert out["selected_columns"] == ["foo"]
        assert out["column_renames"] == {"a": "b"}

    def test_data_source_drops_keys_not_in_its_allowlist(self) -> None:
        """A key valid on apiInput (tables) is not valid on dataSource — dropped."""
        config: dict[str, Any] = {
            "path": "data.parquet",
            "sourceType": "flat_file",
            "tables": [{"path": "$[*]"}],  # apiInput-only key
        }
        out = _prepare_config_for_sidecar(NodeType.DATA_SOURCE, config)
        assert "tables" not in out
        assert out["path"] == "data.parquet"
        assert out["sourceType"] == "flat_file"

    def test_existing_code_key_strip_still_works(self) -> None:
        """α composes with the existing _CODE_KEYS filter."""
        config: dict[str, Any] = {
            "code": "df = df.with_columns(...)",
            "selected_columns": ["foo"],
        }
        out = _prepare_config_for_sidecar(NodeType.POLARS, config)
        assert "code" not in out
        assert out["selected_columns"] == ["foo"]

    def test_existing_underscore_prefix_strip_still_works(self) -> None:
        """α composes with the existing _* prefix filter."""
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "_internalThing": "should not be persisted",
            "_schemaWarnings": [{"column": "x", "status": "stale"}],
        }
        out = _prepare_config_for_sidecar(NodeType.API_INPUT, config)
        assert not any(k.startswith("_") for k in out)

    def test_drop_emits_warning_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dropped keys are logged at WARNING level for observability.

        We spy on the module logger directly rather than going through
        caplog/capsys — haute uses structlog (configured in
        `haute._logging`) which renders to stdout in test context but
        capsys capture is fragile across test orderings. Spying on the
        logger is the durable contract: the function calls
        ``logger.warning("config_keys_dropped_at_write", ...)`` with
        the node type and sorted dropped-key list.
        """
        import haute._config_io as cio

        events: list[tuple[str, dict[str, object]]] = []

        class _Spy:
            def warning(self, event: str, **kwargs: object) -> None:
                events.append((event, kwargs))

            # Other methods used by _config_io still need to pass through —
            # `info`, etc. — so delegate.
            def __getattr__(self, name: str) -> object:
                return getattr(cio.logger, name)

        monkeypatch.setattr(cio, "logger", _Spy())

        _prepare_config_for_sidecar(
            NodeType.API_INPUT,
            {"path": "x.json", "tables": [], "bogus_key": 42, "other_bogus": "y"},
        )

        drop_events = [(e, kw) for e, kw in events if e == "config_keys_dropped_at_write"]
        assert drop_events, (
            "α must emit a `config_keys_dropped_at_write` warning when dropping keys"
        )
        event, kwargs = drop_events[0]
        assert kwargs.get("node_type") == "apiInput"
        keys = kwargs.get("keys")
        assert isinstance(keys, list)
        assert "bogus_key" in keys and "other_bogus" in keys

    def test_unknown_node_type_skips_allowlist(self) -> None:
        """If node_type has no entry in VALID_KEYS, α is a no-op (current behaviour)."""
        # SUBMODEL_PORT has no TypedDict, so isn't in VALID_KEYS.
        # The filter should pass everything through (modulo _* + code strip).
        assert NodeType.SUBMODEL_PORT not in VALID_KEYS
        config: dict[str, Any] = {"some_key": "value", "another": 42}
        out = _prepare_config_for_sidecar(NodeType.SUBMODEL_PORT, config)
        assert out == config


# ---------------------------------------------------------------------------
# a — load-time legacy strip (apiInput-specific)
# ---------------------------------------------------------------------------


class TestNormaliseLoadedConfigApiInputStrip:
    def test_api_input_strips_selected_columns_on_load(self) -> None:
        """v1 `selected_columns` is silently stripped from apiInput on load.

        Promotes D9 "silently ignored at read" to "silently stripped at
        read" so the key cannot leak back out via the editor's
        spread-merge after the user opens then saves a v1-residue file.
        """
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "selected_columns": ["foo", "bar"],
        }
        out = _normalise_loaded_config(config, NodeType.API_INPUT)
        assert "selected_columns" not in out

    def test_api_input_strips_column_renames_on_load(self) -> None:
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "column_renames": {"old": "new"},
        }
        out = _normalise_loaded_config(config, NodeType.API_INPUT)
        assert "column_renames" not in out

    def test_api_input_strips_flatten_schema_on_load(self) -> None:
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "flattenSchema": {"version": 1, "columns": []},
        }
        out = _normalise_loaded_config(config, NodeType.API_INPUT)
        assert "flattenSchema" not in out

    def test_api_input_keeps_v2_keys_on_load(self) -> None:
        """Stripping must not touch the v2-native keys."""
        config: dict[str, Any] = {
            "path": "data.json",
            "contract": "opaque",
            "tables": [{"path": "$[*]", "label": "T", "emit": True, "columns": []}],
        }
        out = _normalise_loaded_config(config, NodeType.API_INPUT)
        assert out["path"] == "data.json"
        assert out["contract"] == "opaque"
        assert len(out["tables"]) == 1

    def test_strip_is_api_input_specific_polars_keeps_selected_columns(self) -> None:
        """selected_columns is legitimate on polars transforms — not stripped there."""
        config: dict[str, Any] = {
            "code": "return df",
            "selected_columns": ["quote_id", "premium"],
            "column_renames": {"old": "new"},
        }
        out = _normalise_loaded_config(config, NodeType.POLARS)
        assert out["selected_columns"] == ["quote_id", "premium"]
        assert out["column_renames"] == {"old": "new"}

    def test_load_node_config_strips_legacy_keys_round_trip(
        self, tmp_path: Any
    ) -> None:
        """End-to-end: a v1-residue file on disk loads as a clean v2 dict.

        Pins the integration: file with leaked legacy keys + v2 tables
        → load_node_config returns a dict with no legacy keys.
        """
        import json as _json

        from haute._config_io import load_node_config

        cfg_dir = tmp_path / "config" / "quote_input"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "leaked.json"
        cfg_path.write_text(
            _json.dumps(
                {
                    "path": "data.json",
                    "contract": "opaque",
                    "tables": [],
                    "selected_columns": ["stale_col"],
                    "column_renames": {"x": "y"},
                    "flattenSchema": {"version": 1},
                }
            )
        )

        loaded = load_node_config(cfg_path, base_dir=tmp_path)
        assert "selected_columns" not in loaded
        assert "column_renames" not in loaded
        assert "flattenSchema" not in loaded
        assert loaded["path"] == "data.json"
        assert loaded["tables"] == []


class TestReadV2ConfigStripsLegacyKeys:
    def test_json_cache_read_v2_strips_legacy_keys(self, tmp_path: Any) -> None:
        """The JSON-cache build path also strips legacy keys on disk read.

        `_read_v2_config` is the funnel for the cache build/status
        routes; if it returned a config with leaked `selected_columns`,
        downstream code that consults the same dict object would still
        see the v1 cruft. Strip at the source.
        """
        import json as _json

        from haute.routes.json_cache import _read_v2_config

        cfg = tmp_path / "leaked.json"
        cfg.write_text(
            _json.dumps(
                {
                    "path": "data.json",
                    "tables": [],
                    "selected_columns": ["stale_col"],
                    "column_renames": {"x": "y"},
                    "flattenSchema": {"version": 1},
                }
            )
        )

        loaded = _read_v2_config(str(cfg))
        assert loaded is not None
        assert "selected_columns" not in loaded
        assert "column_renames" not in loaded
        assert "flattenSchema" not in loaded
        assert loaded["tables"] == []


# ---------------------------------------------------------------------------
# Executor guard — _extract_column_refs node-type-aware
# ---------------------------------------------------------------------------


class TestExtractColumnRefsNodeTypeGuard:
    def test_api_input_skips_selected_columns_scoop(self) -> None:
        """For apiInput nodes, selected_columns must NOT contribute to refs.

        v2 apiInput has no concept of a selected_columns list — the
        per-column `selected` boolean inside tables[].columns[] is the
        v2-native surface. Even if a legacy key leaks into config
        (which `a` is supposed to prevent), the executor's stale-column
        diff must not act on it for apiInput. Belt-and-braces.
        """
        config: dict[str, Any] = {
            "path": "data.json",
            "tables": [],
            "selected_columns": ["stale_a", "stale_b"],
        }
        refs = _extract_column_refs(config, node_type=NodeType.API_INPUT)
        assert refs == set()

    def test_polars_still_scoops_selected_columns(self) -> None:
        """Polars/transform nodes legitimately author selected_columns.

        The guard is apiInput-specific; other node types are unaffected.
        """
        config: dict[str, Any] = {
            "code": "return df",
            "selected_columns": ["quote_id", "premium"],
        }
        refs = _extract_column_refs(config, node_type=NodeType.POLARS)
        assert refs == {"quote_id", "premium"}

    def test_no_node_type_kwarg_preserves_existing_behaviour(self) -> None:
        """Existing callers that don't pass node_type get the original behaviour.

        Backwards-compatibility contract for the signature change.
        """
        config: dict[str, Any] = {"selected_columns": ["x", "y"]}
        refs = _extract_column_refs(config)  # no node_type
        assert refs == {"x", "y"}

    def test_api_input_with_modelling_keys_skips_only_selected_columns(self) -> None:
        """Other scoops (target, weight, etc.) still fire even with apiInput guard.

        Realistic apiInput configs wouldn't carry these, but the
        guard's scope is narrow — only the selected_columns scoop is
        skipped. If a future apiInput v2 contract carried modelling
        refs (it doesn't today), they'd still be picked up.
        """
        config: dict[str, Any] = {
            "selected_columns": ["should_be_skipped"],
            "target": "should_be_kept",
        }
        refs = _extract_column_refs(config, node_type=NodeType.API_INPUT)
        assert "should_be_skipped" not in refs
        assert "should_be_kept" in refs
