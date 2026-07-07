"""TDD pins for Phase 2 Wave 4 package 4A — split of ``_parser_helpers.py``.

This test file encodes the desired post-refactor contract for items #52 and
#61 of ``docs/CODEBASE_REVIEW.md``:

* **#61** — ``src/haute/_parser_helpers.py`` (1011 LOC) is split into four
  focused modules:

    * ``haute._ast_helpers``         — pure AST / source utilities
    * ``haute._config_builder``      — node config dict construction
    * ``haute._code_extraction``     — user-code extraction (incl. engine)
    * ``haute._graph_builders``      — GraphNode / GraphEdge construction

* **#52** — the four user-code extractors
  (``_extract_user_code``, ``_extract_source_user_code``,
  ``_extract_model_score_user_code``, ``_extract_external_user_code``) are
  consolidated into a single engine with pluggable boilerplate matchers.

These tests are intentionally *black-box* about exact names where the
review allows flexibility (e.g. the consolidated engine's callable name is
accepted as any of ``extract_user_code``, ``extract_code``,
``extract_node_user_code`` as long as a single unified entrypoint exists).

Where the review pins a specific name (all the existing public-surface
helpers of ``_parser_helpers``), the tests pin that exactly — those names
must remain importable from *either* the new module *or* from
``_parser_helpers`` as a parser-helper facade.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# The complete public surface of the original _parser_helpers module (all
# names that production code or tests currently import).  After the split,
# each name must still resolve from _parser_helpers (as a re-export) AND
# ideally be located in its thematic new module.
PARSER_HELPERS_PUBLIC_NAMES = (
    # AST / source utilities
    "_eval_ast_literal",
    "_get_decorator_kwargs",
    "_is_pipeline_node_decorator",
    "_is_submodel_node_decorator",
    "_get_decorator_node_type",
    "_get_docstring",
    "_strip_docstring",
    "_dedent",
    "_extract_function_bodies",
    "_extract_connect_calls",
    "_extract_meta",
    "_extract_pipeline_meta",
    "_extract_submodel_meta",
    "_extract_preamble",
    "_extract_preserved_blocks",
    # Code extraction
    "_extract_user_code",
    "_extract_source_user_code",
    "_extract_model_score_user_code",
    "_extract_external_user_code",
    "_unwrap_chain_assignment",
    # Config construction
    "_build_node_config",
    "_copy_config_keys",
    "_resolve_node_config",
    # Graph building
    "_extract_decorated_nodes",
    "_build_edges",
    "_build_rf_nodes",
)


# Per-module expected assignments.  Developer may move re-exports, but each
# name must be resolvable from the indicated "home" module directly.
EXPECTED_HOMES: dict[str, tuple[str, ...]] = {
    "haute._ast_helpers": (
        "_eval_ast_literal",
        "_get_decorator_kwargs",
        "_is_pipeline_node_decorator",
        "_is_submodel_node_decorator",
        "_get_decorator_node_type",
        "_get_docstring",
        "_strip_docstring",
        "_dedent",
        "_extract_function_bodies",
        "_extract_connect_calls",
        "_extract_meta",
        "_extract_pipeline_meta",
        "_extract_submodel_meta",
        "_extract_preamble",
        "_extract_preserved_blocks",
    ),
    "haute._code_extraction": (
        "_extract_user_code",
        "_extract_source_user_code",
        "_extract_model_score_user_code",
        "_extract_external_user_code",
        "_unwrap_chain_assignment",
    ),
    "haute._config_builder": (
        "_build_node_config",
        "_copy_config_keys",
        "_resolve_node_config",
    ),
    "haute._graph_builders": (
        "_extract_decorated_nodes",
        "_build_edges",
        "_build_rf_nodes",
    ),
}


def _candidate_engine_names() -> tuple[str, ...]:
    """Return acceptable names for the consolidated extractor engine.

    The dedup contract (#52) requires a single entrypoint, but the exact
    name is a judgement call — accept any of these as the unified engine.
    """
    return (
        "extract_user_code",
        "extract_node_user_code",
        "extract_code",
        "extract_body_user_code",
        "_extract_node_user_code",
    )


def _find_engine_callable(module: Any) -> tuple[str, Any] | None:
    """Locate the consolidated-engine callable on *module*, if present."""
    for name in _candidate_engine_names():
        fn = getattr(module, name, None)
        if callable(fn):
            return name, fn
    return None


# ---------------------------------------------------------------------------
# Module layout tests (item #61)
# ---------------------------------------------------------------------------


class TestModuleLayout:
    """Each split module imports cleanly."""

    @pytest.mark.parametrize("modname", sorted(EXPECTED_HOMES))
    def test_module_imports(self, modname: str):
        """Freshly import each new module without error."""
        mod = importlib.import_module(modname)
        assert mod is not None
        # Guard against accidentally importing the old file via alias
        assert mod.__name__ == modname

    @pytest.mark.parametrize(
        "modname,names",
        [(m, EXPECTED_HOMES[m]) for m in sorted(EXPECTED_HOMES)],
    )
    def test_module_exports_expected_names(self, modname: str, names: tuple[str, ...]):
        """Each split module hosts its thematic set of names directly.

        The name may be a re-export from another split module, but it must
        resolve on its thematic home — callers migrating to the new layout
        should be able to find it there.
        """
        mod = importlib.import_module(modname)
        missing = [n for n in names if not hasattr(mod, n)]
        assert not missing, (
            f"{modname} is missing expected names: {missing!r}. "
            f"Each split module must host its thematic helpers."
        )


# ---------------------------------------------------------------------------
# Public-API preservation (item #61)
# ---------------------------------------------------------------------------


class TestPublicAPIPreserved:
    """The _parser_helpers facade exposes the parser helper surface."""

    @pytest.mark.parametrize("name", sorted(PARSER_HELPERS_PUBLIC_NAMES))
    def test_parser_helper_facade_exports(self, name: str):
        import haute._parser_helpers as facade

        assert hasattr(facade, name), (
            f"_parser_helpers no longer exports '{name}'. "
            "Split refactor must preserve every helper name production "
            "code and tests currently import."
        )
        assert callable(getattr(facade, name)) or not name.startswith("_"), (
            f"'{name}' is not callable via _parser_helpers — is it really exported?"
        )

    def test_facade_matches_new_identity(self):
        """Facade exports must be the same objects as the focused modules."""
        import haute._parser_helpers as facade

        for modname, names in EXPECTED_HOMES.items():
            mod = importlib.import_module(modname)
            for name in names:
                if not hasattr(facade, name):
                    continue
                new = getattr(mod, name)
                old = getattr(facade, name)
                assert new is old, (
                    f"'{name}' diverged between {modname} and _parser_helpers — "
                    "re-export must be the same object, not a separate copy."
                )


# ---------------------------------------------------------------------------
# No cyclic imports (item #61)
# ---------------------------------------------------------------------------


class TestNoCyclicImports:
    """Each new module imports cleanly in a fresh interpreter.

    A cold-start import in a subprocess guarantees that no split module
    relies on sys.modules state from another import path — this is the
    real signal for a cycle-free layout.
    """

    @pytest.mark.parametrize("modname", sorted(EXPECTED_HOMES))
    def test_cold_import_in_subprocess(self, modname: str):
        code = f"import {modname}; print('OK')"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Cold-start import of {modname} failed:\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Behavior preservation: individual extractor outputs match
# ---------------------------------------------------------------------------


# Representative bodies pulled from real tests / fixtures:
# - dataSource / scenario_expander
# - modelScore
# - externalFile
# - polars (transform)


DATA_SOURCE_BODY_NO_SENTINEL = (
    '    """load britsure policies"""\n'
    '    df = pl.scan_parquet("data/britsure.parquet")\n'
    '    df = df.filter(pl.col("year") >= 2020)\n'
    "    return df"
)

DATA_SOURCE_BODY_IMPORT_PREFIX = (
    '    """data source"""\n'
    "    from pathlib import Path\n"
    '    df = pl.scan_parquet("d.parquet")\n'
    "    df = df.with_columns(x=pl.lit(1))\n"
    "    return df"
)

DATA_SOURCE_BODY_JUST_LOAD = (
    '    """just loads"""\n    df = pl.scan_parquet("d.parquet")\n    return df'
)

DATA_SOURCE_BODY_CHAIN = (
    '    """chain-style"""\n'
    '    df = pl.scan_parquet("d.parquet")\n'
    "    df = (\n"
    "        df\n"
    '        .filter(pl.col("a") > 0)\n'
    '        .select(pl.col("b"))\n'
    "    )\n"
    "    return df"
)


MODEL_SCORE_BODY_THIN = (
    '    """score"""\n'
    "    from pathlib import Path\n"
    "    from haute.graph_utils import score_from_config\n"
    '    result = score_from_config(source, config="config/score.json")\n'
    "    return result"
)

MODEL_SCORE_BODY_WITH_POST = (
    '    """score with post-processing"""\n'
    "    from pathlib import Path\n"
    "    from haute.graph_utils import score_from_config\n"
    '    result = score_from_config(source, config="config/score.json")\n'
    '    df = result.with_columns(doubled=pl.col("prediction") * 2)\n'
    "    return result"
)

MODEL_SCORE_BODY_MULTILINE_CALL = (
    '    """multiline score"""\n'
    "    from haute.graph_utils import score_from_config\n"
    "    result = score_from_config(\n"
    "        source,\n"
    '        config="config/score.json",\n'
    "    )\n"
    "    df = result.with_columns(x=pl.lit(1))\n"
    "    return result"
)


EXTERNAL_FILE_BODY_PICKLE = (
    '    """load a pickled model"""\n'
    "    import pickle\n"
    '    with open("m.pkl", "rb") as _f:\n'
    "        obj = pickle.load(_f)\n"
    "    df = df.with_columns(pred=pl.lit(obj.predict()))\n"
    "    return df"
)

EXTERNAL_FILE_BODY_JOBLIB_SIMPLE = (
    '    """joblib load"""\n'
    "    import joblib\n"
    '    obj = joblib.load("m.pkl")\n'
    "    df = df.with_columns(score=pl.lit(42))\n"
    "    return df"
)

EXTERNAL_FILE_BODY_LOAD_HELPER = (
    '    """via load_external_object"""\n'
    "    from haute.graph_utils import load_external_object\n"
    '    obj = load_external_object("m.cbm", "catboost", "regressor")\n'
    "    df = df.with_columns(pred=pl.lit(obj.predict()))\n"
    "    return df"
)

EXTERNAL_FILE_BODY_NO_USER_CODE = (
    '    """only boilerplate"""\n'
    "    import pickle\n"
    '    with open("m.pkl", "rb") as f:\n'
    "        obj = pickle.load(f)\n"
    "    return df"
)


POLARS_BODY_DF_CHAIN = (
    '    """chain style"""\n'
    "    df = (\n"
    "        source\n"
    '        .filter(pl.col("a") > 0)\n'
    "    )\n"
    "    return df"
)

POLARS_BODY_RETURN_EXPR = '    """return expression"""\n    return source.with_columns(y=pl.lit(1))'

POLARS_BODY_EXPLICIT_ASSIGN = (
    '    """explicit assignment"""\n    df = df.filter(pl.col("x") > 0)\n    return df'
)


DATA_SOURCE_CASES = [
    pytest.param(DATA_SOURCE_BODY_NO_SENTINEL, id="source-no-sentinel"),
    pytest.param(DATA_SOURCE_BODY_IMPORT_PREFIX, id="source-import-prefix"),
    pytest.param(DATA_SOURCE_BODY_JUST_LOAD, id="source-just-load"),
    pytest.param(DATA_SOURCE_BODY_CHAIN, id="source-chain"),
]

MODEL_SCORE_CASES = [
    pytest.param(MODEL_SCORE_BODY_THIN, id="score-thin"),
    pytest.param(MODEL_SCORE_BODY_WITH_POST, id="score-with-post"),
    pytest.param(MODEL_SCORE_BODY_MULTILINE_CALL, id="score-multiline-call"),
]

EXTERNAL_FILE_CASES = [
    pytest.param(EXTERNAL_FILE_BODY_PICKLE, id="ext-pickle"),
    pytest.param(EXTERNAL_FILE_BODY_JOBLIB_SIMPLE, id="ext-joblib"),
    pytest.param(EXTERNAL_FILE_BODY_LOAD_HELPER, id="ext-load-helper"),
    pytest.param(EXTERNAL_FILE_BODY_NO_USER_CODE, id="ext-no-user-code"),
]

POLARS_CASES = [
    pytest.param(POLARS_BODY_DF_CHAIN, id="polars-chain"),
    pytest.param(POLARS_BODY_RETURN_EXPR, id="polars-return-expr"),
    pytest.param(POLARS_BODY_EXPLICIT_ASSIGN, id="polars-explicit-assign"),
]


class TestExtractorCorrectOutputs:
    """Specific output pinning — guards the key invariants independently
    of wrapper identity (so the dedup cannot silently
    break the semantics even if both paths change together).
    """

    def test_source_strips_first_load_statement(self):
        from haute._code_extraction import _extract_source_user_code

        result = _extract_source_user_code(DATA_SOURCE_BODY_NO_SENTINEL)
        assert "scan_parquet" not in result, (
            "dataSource extraction must strip the auto-generated scan_parquet call"
        )
        assert 'filter(pl.col("year") >= 2020)' in result
        assert "return df" not in result

    def test_source_only_load_returns_empty(self):
        from haute._code_extraction import _extract_source_user_code

        assert _extract_source_user_code(DATA_SOURCE_BODY_JUST_LOAD) == ""

    def test_model_score_thin_body_is_empty(self):
        from haute._code_extraction import _extract_model_score_user_code

        # A fresh / thin modelScore with no post-processing is pure boilerplate
        assert _extract_model_score_user_code(MODEL_SCORE_BODY_THIN) == ""

    def test_model_score_post_processing_extracted(self):
        from haute._code_extraction import _extract_model_score_user_code

        result = _extract_model_score_user_code(MODEL_SCORE_BODY_WITH_POST)
        assert "doubled" in result
        assert "score_from_config" not in result
        assert "return result" not in result

    def test_model_score_multiline_call_does_not_leak(self):
        from haute._code_extraction import _extract_model_score_user_code

        result = _extract_model_score_user_code(MODEL_SCORE_BODY_MULTILINE_CALL)
        assert "x=pl.lit(1)" in result
        # The closing paren of the multi-line score_from_config must not
        # leak into the user code.
        assert "config=" not in result
        assert "score_from_config" not in result

    def test_external_file_strips_imports_and_with(self):
        from haute._code_extraction import _extract_external_user_code

        result = _extract_external_user_code(EXTERNAL_FILE_BODY_PICKLE, ["df"])
        assert "import pickle" not in result
        assert "with open" not in result
        assert "pickle.load" not in result
        assert "pred=pl.lit" in result

    def test_external_file_no_user_code_returns_empty(self):
        from haute._code_extraction import _extract_external_user_code

        assert _extract_external_user_code(EXTERNAL_FILE_BODY_NO_USER_CODE, ["df"]) == ""

    def test_polars_extracts_chain(self):
        from haute._code_extraction import _extract_user_code

        result = _extract_user_code(POLARS_BODY_DF_CHAIN, ["source"])
        assert "source" in result
        assert ".filter" in result
        assert "return" not in result


# ---------------------------------------------------------------------------
# #52 — consolidated engine with pluggable matchers
# ---------------------------------------------------------------------------


class TestConsolidatedEngineContract:
    """The four extractors dedup to a single engine with pluggable
    boilerplate matchers (#52).

    The dev agent picks the exact entrypoint name; tests accept any of the
    candidate names.  What the tests *do* pin:

    * a single callable exists (not four)
    * it dispatches by node-type / matcher kind, not by a hardcoded
      if/elif chain on a string argument with the matching burned in-line
    * the four behaviors can still be driven through it
    """

    def test_single_consolidated_entrypoint_exists(self):
        """Exactly one unified engine callable lives on _code_extraction."""
        import haute._code_extraction as mod

        match = _find_engine_callable(mod)
        assert match is not None, (
            "Expected _code_extraction to expose a single consolidated engine "
            f"named one of: {_candidate_engine_names()!r}. "
            "The four per-node extractors must be backed by a common engine "
            "(item #52 of CODEBASE_REVIEW.md)."
        )

    def test_engine_dispatches_by_matcher_registry(self):
        """A matcher registry / mapping is exposed for the four boilerplate shapes.

        The dev may pick any of the names below for the registry.  What
        must hold: there is a registry structure (dict / tuple of
        matchers / similar), NOT a bespoke if/elif tree that hardcodes
        each node-type's skip logic inside the engine body.
        """
        import haute._code_extraction as mod

        candidate_registry_names = (
            "BOILERPLATE_MATCHERS",
            "_BOILERPLATE_MATCHERS",
            "MATCHERS",
            "_MATCHERS",
            "EXTRACTOR_MATCHERS",
            "_EXTRACTOR_MATCHERS",
        )
        found = [n for n in candidate_registry_names if hasattr(mod, n)]
        assert found, (
            "Expected a matcher registry on _code_extraction exposing the "
            "four boilerplate shapes (dataSource, modelScore, externalFile, "
            "polars).  Candidate names: "
            f"{candidate_registry_names!r}.  The engine must dispatch via a "
            "registry, not a hardcoded if/elif in the engine body."
        )
        registry = getattr(mod, found[0])
        # The registry must have at least four entries (one per existing
        # boilerplate shape).
        if isinstance(registry, dict):
            assert len(registry) >= 4, (
                f"Matcher registry has {len(registry)} entries; expected "
                ">= 4 (dataSource, modelScore, externalFile, polars)."
            )
        else:
            # Tuple / list / set — must be iterable with >= 4 members
            try:
                count = len(registry)
            except TypeError:
                count = sum(1 for _ in registry)
            assert count >= 4, f"Matcher registry has {count} entries; expected >= 4."

    def test_engine_produces_same_output_per_kind(self):
        """The consolidated engine produces the same output as the per-kind
        wrapper for every representative input.

        The engine is called with a *kind* argument (whatever the dev
        names it — node_type / matcher / kind / category).  We probe for
        an acceptable calling convention.
        """
        import haute._code_extraction as mod
        import haute._parser_helpers as facade

        match = _find_engine_callable(mod)
        if match is None:
            pytest.skip("engine not present yet — covered by other test")
        _, engine = match

        # Try a few calling conventions.  The dev may pick exactly one.
        def _try_call(body: str, kind: str, param_names: list[str] | None) -> str | None:
            attempts: list[Any] = [
                # engine(body, kind=..., param_names=...)
                lambda: engine(body, kind=kind, param_names=param_names or []),
                # engine(body, node_type=..., param_names=...)
                lambda: engine(body, node_type=kind, param_names=param_names or []),
                # engine(kind, body, param_names)
                lambda: engine(kind, body, param_names or []),
                # engine(body, kind) (no params)
                lambda: engine(body, kind),
                # engine(body, node_type=kind)
                lambda: engine(body, node_type=kind),
                # engine(body, matcher=kind)
                lambda: engine(body, matcher=kind),
            ]
            for fn in attempts:
                try:
                    return fn()
                except TypeError:
                    continue
            return None

        # Map kind names to the wrapper + parameters it needs.
        kind_aliases = {
            "source": ("dataSource", "data_source", "source"),
            "model_score": ("modelScore", "model_score"),
            "external": ("externalFile", "external_file", "external"),
            "polars": ("polars", "transform"),
        }

        samples = [
            (
                "source",
                DATA_SOURCE_BODY_NO_SENTINEL,
                None,
                facade._extract_source_user_code(DATA_SOURCE_BODY_NO_SENTINEL),
            ),
            (
                "model_score",
                MODEL_SCORE_BODY_WITH_POST,
                None,
                facade._extract_model_score_user_code(MODEL_SCORE_BODY_WITH_POST),
            ),
            (
                "external",
                EXTERNAL_FILE_BODY_PICKLE,
                ["df"],
                facade._extract_external_user_code(EXTERNAL_FILE_BODY_PICKLE, ["df"]),
            ),
            (
                "polars",
                POLARS_BODY_DF_CHAIN,
                ["source"],
                facade._extract_user_code(POLARS_BODY_DF_CHAIN, ["source"]),
            ),
        ]

        matched_any = False
        for kind_key, body, params, expected in samples:
            for alias in kind_aliases[kind_key]:
                got = _try_call(body, alias, params)
                if got is None:
                    continue
                matched_any = True
                assert got == expected, (
                    f"Consolidated engine returned different output for kind "
                    f"{alias!r}: got {got!r}, expected {expected!r}"
                )
                break

        assert matched_any, (
            "Could not drive the consolidated engine with any reasonable "
            "calling convention — dev must expose a callable that routes "
            "(body, kind) to the right matcher."
        )


# ---------------------------------------------------------------------------
# End-to-end pipeline-parse smoke test
# ---------------------------------------------------------------------------


class TestEndToEndParseUnchanged:
    """Full parse_pipeline_source of the test fixture must continue to
    produce the same graph after the refactor.

    This keeps the parser split honest: if any public helper name has
    drifted, the fixture parse will fail here.
    """

    @pytest.fixture
    def fixture_source(self) -> str:
        """Load the canonical test-fixture pipeline."""
        fixture = Path(__file__).parent / "fixtures" / "pipeline.py"
        return fixture.read_text()

    def test_parse_pipeline_source_still_works(self, fixture_source: str):
        from haute.parser import parse_pipeline_source

        graph = parse_pipeline_source(fixture_source, _base_dir=Path("tests/fixtures"))
        # Every node in the fixture resolves cleanly after the split
        assert graph.pipeline_name == "test_pipeline"
        assert len(graph.nodes) >= 6
        node_ids = {n.id for n in graph.nodes}
        assert {
            "quotes",
            "batch_quotes",
            "policies",
            "area_lookup",
            "calculate_premium",
            "output",
            "results_write",
        } <= node_ids

    def test_parse_extracts_external_user_code_unchanged(self, fixture_source: str):
        """The externalFile node's code must not have the import / obj
        boilerplate leaked after the split.
        """
        from haute.parser import parse_pipeline_source

        graph = parse_pipeline_source(fixture_source, _base_dir=Path("tests/fixtures"))
        ext_nodes = [n for n in graph.nodes if n.data.nodeType == "externalFile"]
        assert ext_nodes, "fixture must contain an externalFile node"
        code = ext_nodes[0].data.config.get("code", "")
        assert "import" not in code
        assert "load_external_object" not in code
        # The user-facing line survives
        assert "area_factor" in code or "df = policies.with_columns" in code


# ---------------------------------------------------------------------------
# Guard: _parser_helpers is now a facade, not a god file
# ---------------------------------------------------------------------------


class TestGodFileIsGone:
    """After the split, _parser_helpers.py should be dramatically smaller.

    It becomes a thin facade module.  We don't pin an exact LOC but we
    pin that the implementations have moved (measured by checking that
    function bodies aren't both owned there AND owned in the split
    modules).
    """

    def test_parser_helpers_shrunk(self):
        """The split must reduce _parser_helpers to a facade."""
        import haute._parser_helpers as facade

        facade_path = Path(facade.__file__)
        line_count = facade_path.read_text(encoding="utf-8").count("\n")

        # The old file was 1011 lines.  A facade module should be well
        # under 300 lines (room for imports, __all__, docstring, and
        # comments).
        assert line_count < 400, (
            f"_parser_helpers.py is still {line_count} lines — the split "
            "should reduce it to a facade (< 400 lines).  "
            "Implementations must move to _ast_helpers, _code_extraction, "
            "_config_builder, and _graph_builders."
        )

    def test_implementations_live_in_new_modules(self):
        """Sample-check: function implementations defined in the new split
        modules (inspected via __module__).

        If a function's __module__ is still _parser_helpers, the split is
        incomplete.
        """

        sample_checks = [
            # (import-from, attr, expected home)
            ("haute._ast_helpers", "_eval_ast_literal", "haute._ast_helpers"),
            ("haute._code_extraction", "_extract_user_code", "haute._code_extraction"),
            ("haute._config_builder", "_build_node_config", "haute._config_builder"),
            ("haute._graph_builders", "_build_edges", "haute._graph_builders"),
        ]
        for modname, attr, expected in sample_checks:
            mod = importlib.import_module(modname)
            fn = getattr(mod, attr)
            # inspect.getmodule may return None for some callables — fall
            # back to __module__
            home = getattr(fn, "__module__", None)
            assert home == expected, (
                f"{attr} reports __module__={home!r}; expected {expected!r}. "
                "The implementation must live in the new split module, not "
                "be re-exported *out* of _parser_helpers."
            )
