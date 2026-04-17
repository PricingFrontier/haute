"""Tests for Phase 1 Package 1A — Parser / codegen fail-loudly sweep.

TDD: these tests are written BEFORE the implementation lands.  They pin the
contract for items #18-#22 from ``docs/CODEBASE_REVIEW.md`` / ``CODEBASE_REVIEW_PLAN.md``.

Covered items:

    #18 — Silent config-path recovery on Windows
          ``src/haute/_parser_helpers.py:985-999``

    #19 — Instance mapping overrides stale explicit entries
          ``src/haute/codegen.py:884-887`` + ``src/haute/_types.py:597-642``

    #20 — Submodel cross-boundary edge resolution unvalidated
          ``src/haute/codegen.py:1180-1189`` + ``src/haute/_parser_submodels.py:125-133``

    #21 — Graph fingerprint cache extend-path retains stale data
          ``src/haute/executor.py:391-449``

    #22 — Polars codegen empty-code fragile edge case
          ``src/haute/codegen.py:813-829`` + ``_wrap_user_code:357``

Project principle (from ``CLAUDE.md``): code must fail loudly with a typed
``haute.errors.*`` exception carrying structured context, instead of
silently papering over configuration or structural problems.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from haute._parser_helpers import _resolve_node_config
from haute._types import NodeType, build_instance_mapping
from haute.codegen import _gen_transform, _instance_to_code, graph_to_code_multi
from haute.errors import ConfigError, HauteError, ParseError
from haute.executor import execute_graph
from tests.conftest import (
    make_edge as _edge,
    make_graph as _g,
    make_node as _n,
    make_source_node as _source_node,
    make_transform_node as _transform_node,
)

# ===========================================================================
# Item #18 — Silent config-path recovery on Windows
# ===========================================================================
#
# Current behaviour (``_parser_helpers.py:985-999``): when ``load_node_config``
# fails with ``FileNotFoundError`` / ``json.JSONDecodeError`` / ``OSError`` /
# ``ValueError``, the code logs a warning and silently falls back to scanning
# every config folder for a file named ``{func_name}.json``.  If that scan
# also fails, it writes an empty config with a ``_load_error`` marker.
#
# Problems:
#   1. A genuinely missing config is masked by the recovery scan — users
#      never learn the real path is wrong.
#   2. If the recovery scan finds an unrelated file with the same name in a
#      different folder, the wrong config is silently loaded for the node.
#   3. The ``_load_error`` marker is inconsistently respected by downstream
#      code paths, producing subtle divergence between parse and save.
#
# After the fix:
#   - A missing config raises ``ConfigError`` with the original path and a
#     remediation hint.
#   - The recovery scan is either removed outright OR opt-in via an explicit
#     kwarg (e.g. ``recover_mangled_paths=True``); it is never silent.
#   - No ``_load_error`` marker is written on disk.
# ===========================================================================


class TestItem18ConfigPathFailsLoudly:
    """Missing config JSON must raise ``ConfigError`` with remediation hints.

    The warning-and-scan fallback cannot mask genuinely wrong paths.
    """

    def test_missing_config_raises_config_error(self, tmp_path: Path) -> None:
        """Legitimately missing config file → ``ConfigError``, not silent empty dict."""
        # No config file exists at the referenced path.
        with patch("haute._parser_helpers.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError):
                _resolve_node_config(
                    {"config": "config/data_source/does_not_exist.json"},
                    body="",
                    param_names=[],
                    n_params=0,
                    base_dir=tmp_path,
                    func_name="does_not_exist",
                    explicit_node_type=NodeType.DATA_SOURCE,
                )

    def test_missing_config_error_names_original_path(self, tmp_path: Path) -> None:
        """The raised ``ConfigError`` must surface the original (possibly
        mangled) path so the user can see what was actually referenced."""
        referenced_path = "config/data_source/missing_file.json"
        with patch("haute._parser_helpers.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError) as exc_info:
                _resolve_node_config(
                    {"config": referenced_path},
                    body="",
                    param_names=[],
                    n_params=0,
                    base_dir=tmp_path,
                    func_name="missing_file",
                    explicit_node_type=NodeType.DATA_SOURCE,
                )
            # Either the rendered string or the structured context must
            # contain the original path verbatim (without the "forward
            # slash normalization" mask).
            rendered = str(exc_info.value)
            ctx_values = [str(v) for v in exc_info.value.context.values()]
            path_present = referenced_path in rendered or any(
                referenced_path in v for v in ctx_values
            )
            assert path_present, (
                f"Original path {referenced_path!r} missing from error context. "
                f"Got: {rendered!r} / context={exc_info.value.context!r}"
            )

    def test_missing_config_error_contains_remediation_hint(
        self, tmp_path: Path
    ) -> None:
        """Users should get actionable guidance (e.g. "check the path",
        "create the file", or similar) — not a bare error string."""
        with patch("haute._parser_helpers.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError) as exc_info:
                _resolve_node_config(
                    {"config": "config/banding/no_such_band.json"},
                    body="",
                    param_names=["df"],
                    n_params=1,
                    base_dir=tmp_path,
                    func_name="no_such_band",
                    explicit_node_type=NodeType.BANDING,
                )
            # A "remediation hint" is any human-pointing follow-up — we
            # accept any of these signal strings (case-insensitive).
            rendered = str(exc_info.value).lower()
            hint_signals = ("check", "verify", "create", "exist", "path", "hint")
            assert any(s in rendered for s in hint_signals), (
                f"Error should include remediation guidance. Got: {rendered!r}"
            )

    def test_no_silent_load_error_marker_written(self, tmp_path: Path) -> None:
        """After the fix, a missing config must NOT silently return a dict
        containing ``_load_error`` — it must raise instead."""
        with patch("haute._parser_helpers.warn_unrecognized_config_keys"):
            # The old behaviour returned ``(node_type, {"_load_error": ...})``.
            # The new behaviour is to raise.  Either way the internal
            # marker must never surface.
            try:
                _, cfg = _resolve_node_config(
                    {"config": "config/data_source/missing.json"},
                    body="",
                    param_names=[],
                    n_params=0,
                    base_dir=tmp_path,
                    explicit_node_type=NodeType.DATA_SOURCE,
                )
            except ConfigError:
                return  # raising is acceptable
            # If it does NOT raise, then at minimum it must not have
            # inserted a silent _load_error marker.
            assert "_load_error" not in cfg, (
                "Silent _load_error marker is a fallback that hides the real "
                "path problem. Fail loudly instead."
            )

    def test_windows_path_recovery_is_not_silent(self, tmp_path: Path) -> None:
        """Even when a recovery-by-func-name scan would succeed, it must not
        happen silently.  Either the recovery is removed entirely (→ raise
        ``ConfigError``) or it is gated behind an explicit opt-in kwarg."""
        # Write a valid config file for a banding node.
        cfg = {"factors": [{"column": "age", "banding": "continuous"}]}
        cfg_dir = tmp_path / "config" / "banding"
        cfg_dir.mkdir(parents=True)
        cfg_file = cfg_dir / "age_band.json"
        cfg_file.write_text(json.dumps(cfg))

        # Simulate a Windows-mangled path (\b → backspace).  The old code
        # silently recovered from this via ``find_config_by_func_name``.
        mangled_path = "config/\x08anding/age_band.json"

        with patch("haute._parser_helpers.warn_unrecognized_config_keys"):
            # Expected post-fix: either (a) raise ConfigError because the
            # original path was bogus, or (b) require an explicit opt-in
            # kwarg for recovery.  Default behaviour must NOT silently load
            # the recovered config.
            try:
                _, loaded = _resolve_node_config(
                    {"config": mangled_path},
                    body="",
                    param_names=["df"],
                    n_params=1,
                    base_dir=tmp_path,
                    func_name="age_band",
                    explicit_node_type=NodeType.BANDING,
                )
            except ConfigError:
                return  # raising is acceptable and preferred
            # If the call does not raise, it must not have returned a
            # silently-recovered config either.  A successful recovery
            # without an explicit opt-in kwarg violates the fail-loudly
            # contract.
            assert loaded.get("factors") != cfg["factors"], (
                "Silent Windows path recovery must not succeed without an "
                "explicit opt-in kwarg. Default behaviour must fail loudly."
            )

    def test_invalid_json_raises_config_error(self, tmp_path: Path) -> None:
        """Corrupted JSON (as opposed to missing file) also fails loudly."""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        bad = cfg_dir / "broken.json"
        bad.write_text("{ this is not valid json")

        with patch("haute._parser_helpers.warn_unrecognized_config_keys"):
            with pytest.raises(ConfigError):
                _resolve_node_config(
                    {"config": "config/data_source/broken.json"},
                    body="",
                    param_names=[],
                    n_params=0,
                    base_dir=tmp_path,
                    func_name="broken",
                    explicit_node_type=NodeType.DATA_SOURCE,
                )


# ===========================================================================
# Item #19 — Instance mapping overrides stale explicit entries
# ===========================================================================
#
# Current behaviour (``codegen.py:884-887`` + ``_types.py:597-642``): the
# instance builder consumes ``data.config["inputMapping"]`` verbatim, then
# fills in any missing ``orig_name`` via substring / positional fallbacks.
# If the UI-stored ``inputMapping`` contains **stale** keys (the user
# previously had ``inputMapping={"claims": "raw"}`` but ``raw`` is no longer
# a real instance input), the stale entry is kept silently and the positional
# fallback fills the rest — producing wrong wiring.
#
# After the fix:
#   - ``build_instance_mapping`` validates explicit keys against ``orig_names``
#     and values against ``inst_names``.  Stale keys OR values raise
#     ``ConfigError`` with the stale key(s) named.
#   - The caller (``_instance_to_code``) surfaces the same error so users
#     can see exactly which inputMapping entries are no longer valid.
# ===========================================================================


class TestItem19StaleInstanceMappingRaises:
    """Stale ``inputMapping`` entries must raise ``ConfigError`` instead of
    being silently overridden by positional fallbacks."""

    def test_happy_path_valid_explicit_mapping(self) -> None:
        """Sanity: a fully-valid explicit mapping still produces the expected result."""
        mapping = build_instance_mapping(
            orig_names=["claims", "premiums"],
            inst_names=["claims_v2", "premiums_v2"],
            explicit={"claims": "claims_v2", "premiums": "premiums_v2"},
        )
        assert mapping == {"claims": "claims_v2", "premiums": "premiums_v2"}

    def test_stale_explicit_key_raises_config_error(self) -> None:
        """An explicit key naming an orig_name that no longer exists is stale.

        Scenario: the user previously had an original parameter ``old_param``.
        The pipeline was edited so the original is now called ``new_param``.
        The frontend-stored inputMapping still references ``old_param``.
        """
        with pytest.raises(ConfigError):
            build_instance_mapping(
                orig_names=["new_param"],
                inst_names=["something"],
                explicit={"old_param": "something"},
            )

    def test_stale_explicit_value_raises_config_error(self) -> None:
        """An explicit value pointing to an inst_name that is no longer an
        upstream input (e.g. upstream node deleted) is stale."""
        with pytest.raises(ConfigError):
            build_instance_mapping(
                orig_names=["claims"],
                inst_names=["actual_upstream"],
                explicit={"claims": "deleted_upstream"},
            )

    def test_stale_key_error_names_stale_keys(self) -> None:
        """The raised ``ConfigError`` must name the stale key(s) so users
        know exactly which inputMapping entries to fix."""
        with pytest.raises(ConfigError) as exc_info:
            build_instance_mapping(
                orig_names=["fresh"],
                inst_names=["upstream"],
                explicit={"stale_key": "upstream", "also_stale": "upstream"},
            )
        rendered = str(exc_info.value)
        ctx_values = [str(v) for v in exc_info.value.context.values()]
        # At least one of the stale keys must appear either in the
        # rendered string or in the context.
        assert "stale_key" in rendered or any("stale_key" in v for v in ctx_values), (
            f"Stale key not named in error: {rendered!r}, ctx={exc_info.value.context!r}"
        )

    def test_instance_to_code_surfaces_stale_mapping_error(self) -> None:
        """The codegen path must propagate the ``ConfigError`` rather than
        silently calling ``build_instance_mapping`` with broken data."""
        instance_node = _n(
            {
                "id": "inst1",
                "data": {
                    "label": "MyInstance",
                    "nodeType": "polars",
                    "config": {
                        "instanceOf": "original_node_id",
                        # This mapping is stale: "deleted_orig_param" is no
                        # longer in the original node's parameter list.
                        "inputMapping": {"deleted_orig_param": "upstream_a"},
                    },
                },
            }
        )
        with pytest.raises(ConfigError):
            _instance_to_code(
                instance_node,
                original_func_name="original_node_id",
                source_names=["upstream_a"],
                orig_source_names=["current_orig_param"],
            )

    def test_partial_explicit_no_stale_entries_still_works(self) -> None:
        """A partial explicit mapping with only fresh keys still fills
        the gaps via the existing match + positional rules."""
        mapping = build_instance_mapping(
            orig_names=["a", "b"],
            inst_names=["x", "y"],
            explicit={"a": "x"},
        )
        assert mapping["a"] == "x"
        assert mapping["b"] == "y"  # positional fill for the remaining orig


# ===========================================================================
# Item #20 — Submodel cross-boundary edge resolution unvalidated
# ===========================================================================
#
# Current behaviour (``codegen.py:1180-1189``):
#
#     for edge in edges:
#         if edge.target == sm_node_id and edge.targetHandle:
#             child_id = edge.targetHandle.removeprefix("in__")
#             if child_id in sm_child_ids:
#                 ...
#
# Failure modes masked silently today:
#   - ``targetHandle`` is ``None`` → the branch is skipped; edge is lost.
#   - ``targetHandle`` has no ``in__`` prefix → ``removeprefix`` returns the
#     original string; ``child_id in sm_child_ids`` likely fails; edge is lost.
#   - ``targetHandle`` has the prefix but the remainder is not a real child
#     ID → edge is silently dropped.
#
# After the fix: each of the above raises ``ParseError`` with structured
# context (edge id, handle value, submodel name) so the user sees exactly
# which edge is malformed.
# ===========================================================================


class TestItem20SubmodelCrossBoundaryHandleValidation:
    """Malformed submodel cross-boundary handles must raise ``ParseError``."""

    @staticmethod
    def _submodel_graph_with_handle(handle: str | None) -> dict:
        """Build a minimal graph-dict with one cross-boundary edge whose
        ``targetHandle`` value is ``handle`` (may be ``None``)."""
        edge_dict: dict = {
            "id": "e1",
            "source": "src",
            "target": "submodel__sm1",
        }
        if handle is not None:
            edge_dict["targetHandle"] = handle
        return {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Source",
                        "nodeType": "dataSource",
                        "config": {"path": "d.parquet"},
                    },
                },
                {
                    "id": "child_a",
                    "data": {"label": "ChildA", "nodeType": "polars", "config": {}},
                },
            ],
            "edges": [edge_dict],
            "submodels": {
                "sm1": {
                    "file": "modules/sm1.py",
                    "childNodeIds": ["child_a"],
                    "graph": {
                        "nodes": [
                            {
                                "id": "child_a",
                                "data": {
                                    "label": "ChildA",
                                    "nodeType": "polars",
                                    "config": {},
                                },
                            },
                        ],
                        "edges": [],
                    },
                },
            },
        }

    def test_happy_path_valid_in_prefix_handle(self) -> None:
        """Well-formed handle ``in__child_a`` wires the edge correctly."""
        graph = _g(self._submodel_graph_with_handle("in__child_a"))
        files = graph_to_code_multi(graph, pipeline_name="main")
        main_code = files["main.py"]
        # ChildA receives Source as an input — the wiring succeeded.
        assert "connect" in main_code

    def test_missing_in_prefix_raises_parse_error(self) -> None:
        """Handle without the ``in__`` prefix is malformed.

        Previously ``removeprefix("in__")`` silently returned the original
        string and the membership check silently failed.  Now: raise."""
        graph = _g(self._submodel_graph_with_handle("child_a"))  # no prefix
        with pytest.raises(ParseError):
            graph_to_code_multi(graph, pipeline_name="main")

    def test_malformed_handle_raises_parse_error(self) -> None:
        """Handle with a different prefix (``out__``, stray text, …) is
        malformed in the context of a target edge."""
        graph = _g(self._submodel_graph_with_handle("out__child_a"))
        with pytest.raises(ParseError):
            graph_to_code_multi(graph, pipeline_name="main")

    def test_parse_error_includes_edge_context(self) -> None:
        """The raised ``ParseError`` must expose the edge id and the raw
        handle value so users can find and fix the bad edge."""
        graph = _g(self._submodel_graph_with_handle("in__ghost_child"))
        with pytest.raises(ParseError) as exc_info:
            graph_to_code_multi(graph, pipeline_name="main")
        rendered = str(exc_info.value)
        ctx_values = [str(v) for v in exc_info.value.context.values()]
        # Either the edge id OR the raw handle string should surface.
        signals = ("e1", "in__ghost_child", "ghost_child")
        assert any(s in rendered for s in signals) or any(
            s in v for v in ctx_values for s in signals
        ), (
            f"ParseError must surface edge id / handle. Got: {rendered!r}, "
            f"ctx={exc_info.value.context!r}"
        )

    def test_prefix_but_nonexistent_child_raises(self) -> None:
        """``in__not_a_real_child`` is well-prefixed but refers to a child
        that does not exist in the submodel — must raise."""
        graph = _g(self._submodel_graph_with_handle("in__not_a_real_child"))
        with pytest.raises(ParseError):
            graph_to_code_multi(graph, pipeline_name="main")

    def test_target_edge_without_handle_raises(self) -> None:
        """When an edge targets a submodel node but has no ``targetHandle``,
        the codegen cannot resolve it — must fail loudly rather than
        silently skipping the edge."""
        graph = _g(self._submodel_graph_with_handle(None))
        # Either a ParseError or a ConfigError is acceptable; HauteError
        # covers both.
        with pytest.raises(HauteError):
            graph_to_code_multi(graph, pipeline_name="main")


# ===========================================================================
# Item #21 — Graph fingerprint cache extend-path retains stale data
# ===========================================================================
#
# Current behaviour (``executor.py:391-449``): when the extend-path fires,
# the merge is ``{**eager_outputs, **prev_outputs}`` which lets cached
# ``prev_outputs`` overwrite freshly-computed results for any shared node id.
# The comment justifies this as preserving trace row identity, but the side
# effect is that stale data can survive a "delete-then-re-add with same ID
# and altered config" edit.
#
# After the fix:
#   - A delete-then-re-add of a node with the same ID but different config
#     must produce the NEW output, not the cached stale one.
#   - The fingerprint OR the merge logic must detect that the node has been
#     invalidated and refresh its cache entry rather than preserving it.
# ===========================================================================


class TestItem21ExtendPathNoStaleData:
    """The extend-path must not preserve stale outputs when a node is
    deleted and re-added with altered config."""

    def test_happy_path_extend_preserves_unaltered_nodes(self, tmp_path: Path) -> None:
        """Extending the cache to a new target node must still return the
        correct results for nodes that really are unchanged."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid", "df = df.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("leaf", "df = df.with_columns(z=pl.col('y') * 10)"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "leaf")],
            }
        )

        # Populate cache up to 'mid'.
        r1 = execute_graph(graph, target_node_id="mid")
        assert r1["mid"].status == "ok"
        # Extend to 'leaf' — no nodes invalidated.
        r2 = execute_graph(graph, target_node_id="leaf")
        assert r2["leaf"].status == "ok"
        assert r2["leaf"].row_count == 3

    def test_node_reconfigured_with_new_code_serves_fresh_result(
        self, tmp_path: Path
    ) -> None:
        """The canonical scenario from Item #21: a node was executed,
        then re-added with the same ID but altered config.  The next
        execution must serve the NEW code's output, not the stale one."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [10, 20, 30]}).write_parquet(p)

        graph_v1 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # v1 code: y = x
                    _transform_node("mid", "df = df.with_columns(y=pl.col('x'))"),
                ],
                "edges": [_edge("src", "mid")],
            }
        )

        r1 = execute_graph(graph_v1, target_node_id="mid")
        assert r1["mid"].status == "ok"
        y_v1 = [row["y"] for row in r1["mid"].preview]
        assert y_v1 == [10, 20, 30]

        # Simulate delete + re-add with same ID but new code.
        graph_v2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    # v2 code: y = x * 100 — entirely different output
                    _transform_node("mid", "df = df.with_columns(y=pl.col('x') * 100)"),
                ],
                "edges": [_edge("src", "mid")],
            }
        )

        r2 = execute_graph(graph_v2, target_node_id="mid")
        y_v2 = [row["y"] for row in r2["mid"].preview]
        # Freshly computed — must match v2 code, not v1's cached output.
        assert y_v2 == [1000, 2000, 3000], (
            f"Stale cache survived delete+re-add with altered config. "
            f"Expected [1000, 2000, 3000], got {y_v2}"
        )

    def test_extend_path_refreshes_stale_node_when_reaching_new_target(
        self, tmp_path: Path
    ) -> None:
        """When extending the cache to a deeper node but an intermediate
        node had its config altered, the fresh value must reach the
        downstream computation — the stale cached intermediate must not
        poison the result."""
        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2]}).write_parquet(p)

        graph_v1 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid", "df = df.with_columns(y=pl.col('x') + 1)"),
                ],
                "edges": [_edge("src", "mid")],
            }
        )
        # Warm cache to 'mid' with v1 code.
        execute_graph(graph_v1, target_node_id="mid")

        # Now user edits 'mid' in-place (same ID, new code) and appends
        # a downstream leaf.
        graph_v2 = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node(
                        "mid", "df = df.with_columns(y=pl.col('x') * 1000)"
                    ),  # altered
                    _transform_node("leaf", "df = df.with_columns(z=pl.col('y'))"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "leaf")],
            }
        )
        r2 = execute_graph(graph_v2, target_node_id="leaf")
        assert r2["leaf"].status == "ok"
        z_vals = [row["z"] for row in r2["leaf"].preview]
        # Must use v2 'mid' code (y = x * 1000), not the stale v1 (y = x + 1)
        assert z_vals == [1000, 2000], (
            f"Downstream computed against stale cached 'mid'. Expected "
            f"[1000, 2000], got {z_vals}"
        )

    def test_extend_path_does_not_return_stale_outputs_from_cache(
        self, tmp_path: Path
    ) -> None:
        """Directly exercises the ``{**eager_outputs, **prev_outputs}`` merge.

        This is the structural heart of Item #21: when the extend-path fires
        the merge currently lets cached ``prev_outputs`` override freshly
        computed outputs for any shared node id.  If prev_outputs contains
        stale entries (e.g. left over from an earlier state), the caller
        gets the STALE preview for those node ids, even though the execution
        ran fresh.  We poke the cache to seed stale data under the current
        fingerprint, then run the extend-path and inspect the caller-visible
        results for a node whose stale cache entry would be served.
        """
        from haute.executor import _preview_cache
        from haute.graph_utils import graph_fingerprint

        p = tmp_path / "d.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(p)

        graph = _g(
            {
                "nodes": [
                    _source_node("src", str(p)),
                    _transform_node("mid", "df = df.with_columns(y=pl.col('x') + 1)"),
                    _transform_node("leaf", "df = df.with_columns(z=pl.col('y') * 10)"),
                ],
                "edges": [_edge("src", "mid"), _edge("mid", "leaf")],
            }
        )

        # Seed the cache with DELIBERATELY STALE outputs for src and mid
        # under the real fingerprint.  This simulates a prior execution
        # state whose invalidation was missed.
        fp = graph_fingerprint(graph, "None:live")
        stale_src = pl.DataFrame({"x": [999, 999, 999]})
        stale_mid = pl.DataFrame({"x": [999, 999, 999], "y": [0, 0, 0]})
        _preview_cache.store(
            fp,
            eager_outputs={"src": stale_src, "mid": stale_mid},
            order=["src", "mid"],
            errors={},
            timings={"src": 1.0, "mid": 1.0},
            memory_bytes={"src": 1, "mid": 1},
            error_lines={},
            available_columns={
                "src": [("x", "Int64")],
                "mid": [("x", "Int64"), ("y", "Int64")],
            },
        )

        # Now request 'leaf' — triggers extend-path (fingerprint matches,
        # 'leaf' not in prev_outputs).  The bug is that the merge order
        # ``{**eager_outputs, **prev_outputs}`` preserves the STALE src
        # and mid outputs in the returned result dict.  After the fix,
        # the caller must see fresh, non-stale previews for ALL nodes.
        results = execute_graph(graph, target_node_id="leaf")

        # mid should reflect the real y = x + 1 computation, not the
        # stale cached y = 0.
        mid_y = [row["y"] for row in results["mid"].preview]
        assert mid_y == [2, 3, 4], (
            f"Extend-path merge served stale 'mid' output. "
            f"Expected y=[2, 3, 4], got {mid_y}. "
            f"Fix: don't merge stale prev_outputs over fresh eager_outputs."
        )
        # src should reflect the real source contents, not the stale
        # seed values.
        src_x = [row["x"] for row in results["src"].preview]
        assert src_x == [1, 2, 3], (
            f"Extend-path merge served stale 'src' output. "
            f"Expected x=[1, 2, 3], got {src_x}."
        )


# ===========================================================================
# Item #22 — Polars codegen empty-code fragile edge case
# ===========================================================================
#
# Current behaviour (``codegen.py:813-829`` + ``_wrap_user_code:357``):
#
#     if not code:
#         body = _wrap_user_code(code, source_names)  # returns "return <first>"
#         return (...)
#
# The empty-code branch silently picks ``source_names[0]`` as the single
# source.  For a transform with >1 upstream node, this drops all but the
# first input with no warning; for zero-source the code returns ``return df``
# where ``df`` is never bound.
#
# After the fix: the empty-code branch is explicit and tested per scenario:
#   - single-source: returns ``return <src>`` (current behaviour).
#   - multi-source: either explicitly joins them (no silent drop) OR
#     raises ``ConfigError`` naming the multi-source situation.
#   - zero-source: raises ``ConfigError`` (polars transform with no code AND
#     no inputs is incoherent).
# ===========================================================================


class TestItem22EmptyPolarsCodeExplicit:
    """The empty-code path in ``_gen_transform`` must be explicit and
    safe for all source-count scenarios."""

    def test_happy_path_single_source_returns_input(self) -> None:
        """One upstream + empty code → passthrough of that upstream."""
        node = _n(
            {
                "id": "t",
                "data": {"label": "Pass", "nodeType": "polars", "config": {}},
            }
        )
        code = _gen_transform(node, ["upstream"])
        assert "return upstream" in code
        assert "def Pass(upstream: pl.LazyFrame)" in code

    def test_zero_sources_raises_config_error(self) -> None:
        """A polars transform with NO upstream sources AND no user code is
        incoherent — must fail loudly rather than emit ``return df`` where
        ``df`` is unbound."""
        node = _n(
            {
                "id": "t",
                "data": {"label": "Orphan", "nodeType": "polars", "config": {}},
            }
        )
        with pytest.raises(ConfigError):
            _gen_transform(node, [])

    def test_multi_source_empty_code_is_explicit(self) -> None:
        """With multiple upstreams and no code, the behaviour must be
        explicit: either (a) raise ``ConfigError`` because the user did
        not say which input to pass through, or (b) produce code that
        references ALL inputs (not a silent first-only drop)."""
        node = _n(
            {
                "id": "t",
                "data": {"label": "Merge", "nodeType": "polars", "config": {}},
            }
        )
        try:
            code = _gen_transform(node, ["left", "right"])
        except ConfigError:
            return  # raising is an acceptable resolution
        # Non-raising path must NOT silently drop 'right'.
        # The generated code must either:
        #   - reference both inputs, OR
        #   - make the multi-source decision explicit via a deterministic
        #     mechanism that users can see in the source.
        # A bare ``return left`` that silently drops ``right`` is not ok.
        assert "right" in code or "join" in code, (
            f"Multi-source empty-code silently dropped 'right'. Code: {code!r}"
        )

    def test_single_source_with_code_unchanged(self) -> None:
        """Non-empty user code path continues to work as before."""
        node = _n(
            {
                "id": "t",
                "data": {
                    "label": "Filt",
                    "nodeType": "polars",
                    "config": {"code": "df = df.filter(pl.col('x') > 0)"},
                },
            }
        )
        code = _gen_transform(node, ["upstream"])
        assert "df = upstream" in code  # aliasing line
        assert "df.filter" in code
        assert "return df" in code

    def test_empty_code_branch_is_distinguishable_from_nonempty(self) -> None:
        """Smoke: the empty-code and non-empty branches produce visibly
        different output — useful as a change-detection test when the
        implementation is refactored to be explicit."""
        base_node = {
            "id": "t",
            "data": {"label": "X", "nodeType": "polars", "config": {}},
        }
        empty_node = _n(base_node)
        nonempty_node = _n(
            {
                "id": "t",
                "data": {
                    "label": "X",
                    "nodeType": "polars",
                    "config": {"code": "df = df"},
                },
            }
        )
        empty_code = _gen_transform(empty_node, ["up"])
        nonempty_code = _gen_transform(nonempty_node, ["up"])
        # Non-empty branch includes the "df = <first>" alias line.
        assert "df = up" in nonempty_code
        # Empty branch does NOT need the alias — it returns the source
        # directly.
        assert "df = up" not in empty_code
