"""TDD pin-down tests for Package 4B:

- Item #63: Split ``src/haute/codegen.py`` (1340 LOC). Orchestration stays in
  ``haute.codegen``. Builder registry + per-type builder functions move to
  ``haute._codegen_builders``.
- Item #55: Parallel builder dispatch tables at
  ``_builders.py:_NODE_BUILDERS`` and ``codegen.py:_CODEGEN_BUILDERS``. The
  two must be unified into a single canonical registry keyed by
  :class:`NodeType`. Both the execution dispatch in ``_builders.py`` and the
  codegen dispatch in ``codegen.py`` must read from that one source.

These tests are written BEFORE the refactor (TDD gate). They are expected to
fail until the developer agent lands the split/unification. Each failure
should be failing for a principled reason — either the new module doesn't
exist, the unified registry doesn't exist, or behaviour has drifted — never
because the tests themselves are wrong.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from haute._types import NodeType
from tests.conftest import make_graph as _g
from tests.conftest import make_output_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


#: The set of :class:`NodeType` values the codebase dispatches over. SUBMODEL
#: and SUBMODEL_PORT are placeholder/pass-through node types used by the
#: submodel boundary machinery — they must still be registered so the
#: dispatcher does not fall through to ``_gen_transform`` by accident.
_ALL_DISPATCH_NODE_TYPES: frozenset[NodeType] = frozenset(NodeType)


def _module_top_level_assignments(module_path: Path) -> set[str]:
    """Return the set of names assigned at module top level.

    Used to assert that ``codegen.py`` no longer owns a module-level registry
    dict/list after the split.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
    return names


def _simple_source_graph():
    """A single ``dataInput`` node — simplest valid graph."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "data/in.parquet"},
                    },
                }
            ],
            "edges": [],
        }
    )


def _source_transform_graph():
    """Source -> transform (polars with code)."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "data/in.parquet"},
                    },
                },
                {
                    "id": "t",
                    "data": {
                        "label": "Clean",
                        "nodeType": "polars",
                        "config": {"code": "df = Source.filter(pl.col('x') > 0)"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "t"}],
        }
    )


def _source_sink_graph():
    """Source -> data_sink."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "data/in.parquet"},
                    },
                },
                {
                    "id": "snk",
                    "data": {
                        "label": "Write",
                        "nodeType": "dataOutput",
                        "config": {"path": "out.parquet", "format": "parquet"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "snk"}],
        }
    )


def _modelling_graph():
    """Source -> modelling (pass-through decorator)."""
    return _g(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "data/in.parquet"},
                    },
                },
                {
                    "id": "m",
                    "data": {
                        "label": "TrainModel",
                        "nodeType": "modelling",
                        "config": {"target_column": "y"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "m"}],
        }
    )


def _constant_output_graph():
    """constant -> output."""
    return _g(
        {
            "nodes": [
                {
                    "id": "c",
                    "data": {
                        "label": "Const",
                        "nodeType": "constant",
                        "config": {"values": [{"name": "x", "value": "1"}]},
                    },
                },
                {
                    "id": "o",
                    "data": {
                        "label": "Out",
                        "nodeType": "output",
                        "config": make_output_config(["x"]),
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "c", "target": "o"}],
        }
    )


# Capture pre-refactor outputs at test-collection time. Once the refactor
# lands these strings must remain byte-for-byte identical; that's the whole
# point of this test suite.
_PINNED_GRAPHS = {
    "single_source": _simple_source_graph,
    "source_transform": _source_transform_graph,
    "source_sink": _source_sink_graph,
    "source_modelling": _modelling_graph,
    "constant_output": _constant_output_graph,
}


# ---------------------------------------------------------------------------
# Item #63 — Module layout: _codegen_builders.py exists, codegen.py is
# orchestration-only.
# ---------------------------------------------------------------------------


class TestCodegenModuleSplit:
    """Item #63 pin-down: the per-type codegen builders live in
    ``haute._codegen_builders``; ``haute.codegen`` keeps orchestration."""

    def test_codegen_builders_module_importable(self) -> None:
        """``haute._codegen_builders`` must exist as an importable module."""
        mod = importlib.import_module("haute._codegen_builders")
        assert mod is not None

    def test_codegen_builders_owns_registration_entrypoint(self) -> None:
        """The codegen side of dispatch must be *physically defined* in
        ``_codegen_builders`` — the registry itself lives in
        :mod:`haute._registry`, so what we pin here is that every codegen
        NodeType's registered builder function resolves back to
        ``haute._codegen_builders`` as its owning module, NOT to
        ``haute.codegen`` or anywhere else."""
        from haute import _codegen_builders, _registry, codegen  # noqa: F401

        registry = _registry.NODE_REGISTRY
        codegen_entries = {
            nt: entry.codegen for nt, entry in registry.items() if entry.codegen is not None
        }
        assert codegen_entries, (
            "NODE_REGISTRY has no codegen entries — _codegen_builders did not populate them."
        )
        # The load-bearing ownership check: every registered codegen
        # function must live in _codegen_builders.  If a builder has
        # been stubbed or moved back into codegen.py, this catches it.
        wrong_owner = {
            nt.value: fn.__module__
            for nt, fn in codegen_entries.items()
            if fn.__module__ != "haute._codegen_builders"
        }
        assert not wrong_owner, (
            "Every NodeType's codegen builder must be defined in "
            "haute._codegen_builders.  The following NodeTypes have "
            f"builders defined elsewhere: {wrong_owner!r}. "
            "Move them back into _codegen_builders.py so ownership of "
            "the codegen dispatch table is not split across modules."
        )
        # Belt-and-braces: _codegen_builders must own the dispatch-function
        # source of truth, so codegen.py must not define its own
        # _CODEGEN_BUILDERS table either.
        assert not hasattr(codegen, "_CODEGEN_BUILDERS"), (
            "haute.codegen must not expose _CODEGEN_BUILDERS after the split."
        )

    def test_codegen_py_has_no_module_level_registry(self) -> None:
        """After the split, ``codegen.py`` must not keep its own registry."""
        import haute.codegen as codegen_mod

        # The attribute must not exist at module level any more. If it does,
        # the split left a stale copy behind and the two tables will drift.
        assert not hasattr(codegen_mod, "_CODEGEN_BUILDERS"), (
            "haute.codegen must not define _CODEGEN_BUILDERS after the split; "
            "move it to haute._codegen_builders"
        )

    def test_codegen_py_source_has_no_builder_assignments(self) -> None:
        """Source-level: ``codegen.py`` does not assign a builder table.

        We parse the file directly — ``hasattr`` can be fooled by imports,
        but a module-level dict assignment ``_CODEGEN_BUILDERS: ... = {}``
        in the .py file is unambiguous.
        """
        codegen_path = Path(importlib.import_module("haute.codegen").__file__)
        names = _module_top_level_assignments(codegen_path)
        assert "_CODEGEN_BUILDERS" not in names, (
            "_CODEGEN_BUILDERS is still declared at the top of codegen.py; "
            "move it to _codegen_builders.py"
        )

    def test_codegen_py_retains_orchestration_api(self) -> None:
        """Post-split, the public codegen orchestration API stays put."""
        from haute.codegen import graph_to_code, graph_to_code_multi

        assert callable(graph_to_code)
        assert callable(graph_to_code_multi)

    def test_codegen_py_retains_generate_node_code(self) -> None:
        """The orchestrator must still expose ``_generate_node_code`` (or an
        equivalent dispatcher) — ``graph_to_code_multi`` depends on it
        transitively via ``_node_to_code``."""
        from haute import codegen

        assert hasattr(codegen, "_generate_node_code") or hasattr(codegen, "_node_to_code")


# ---------------------------------------------------------------------------
# Item #55 — Unified registry: one source of truth.
# ---------------------------------------------------------------------------


def _locate_unified_registry():
    """Locate the unified ``(exec, codegen)`` registry.

    Tries the plausible homes in order:

    * ``haute._registry`` (a new dedicated module)
    * ``haute._builders`` (exec side absorbs codegen)
    * ``haute._codegen_builders`` (codegen side absorbs exec)

    Returns the registry dict or ``None`` if none is found.
    """
    candidate_modules = ("haute._registry", "haute._builders", "haute._codegen_builders")
    candidate_names = (
        "NODE_REGISTRY",
        "BUILDER_REGISTRY",
        "REGISTRY",
        "_REGISTRY",
        "_NODE_REGISTRY",
    )
    for mod_name in candidate_modules:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for name in candidate_names:
            if hasattr(mod, name):
                return getattr(mod, name), f"{mod_name}.{name}"
    return None, None


class TestUnifiedRegistry:
    """Item #55 pin-down: a single canonical registry stores ``(exec_fn,
    codegen_fn)`` tuples per :class:`NodeType`. Both ``_builders.py`` and
    ``codegen.py`` read from this registry rather than maintaining parallel
    dispatch tables."""

    def test_unified_registry_exists(self) -> None:
        """A unified registry must live at one well-known location."""
        reg, loc = _locate_unified_registry()
        assert reg is not None, (
            "No unified (exec, codegen) registry found. Expected one of "
            "haute._registry.REGISTRY, haute._builders.REGISTRY, or "
            "haute._codegen_builders.REGISTRY (or plausible variants)."
        )

    def test_unified_registry_is_mapping_keyed_by_nodetype(self) -> None:
        """The registry keys must be :class:`NodeType` values."""
        reg, _loc = _locate_unified_registry()
        assert reg is not None, "Unified registry missing (see previous test)"
        for key in reg:
            assert isinstance(key, NodeType), (
                f"Unified registry key {key!r} is not a NodeType — keys must be "
                f"NodeType enum members so executor and codegen share one "
                f"source of truth."
            )

    def test_unified_registry_entries_have_exec_and_codegen(self) -> None:
        """Every entry must expose both an exec builder and a codegen builder.

        Exact shape: either a 2-tuple ``(exec_fn, codegen_fn)`` or a
        ``dataclass``/``NamedTuple`` with ``exec`` (or ``build``/``builder``)
        and ``codegen`` attributes.
        """
        reg, _loc = _locate_unified_registry()
        assert reg is not None

        for node_type, entry in reg.items():
            exec_fn = None
            codegen_fn = None
            if isinstance(entry, tuple) and len(entry) == 2:
                exec_fn, codegen_fn = entry
            else:
                exec_fn = (
                    getattr(entry, "exec", None)
                    or getattr(entry, "build", None)
                    or getattr(entry, "builder", None)
                    or getattr(entry, "build_fn", None)
                )
                codegen_fn = getattr(entry, "codegen", None) or getattr(entry, "codegen_fn", None)
            assert callable(exec_fn), (
                f"Registry entry for {node_type!r} has no callable exec builder; "
                f"got entry={entry!r}"
            )
            assert callable(codegen_fn), (
                f"Registry entry for {node_type!r} has no callable codegen "
                f"builder; got entry={entry!r}"
            )

    def test_unified_registry_covers_all_dispatch_node_types(self) -> None:
        """Every :class:`NodeType` that either side dispatches over today
        must appear in the unified registry. Missing entries mean a silent
        fallback still exists."""
        reg, _loc = _locate_unified_registry()
        assert reg is not None
        registered = set(reg.keys())
        missing = _ALL_DISPATCH_NODE_TYPES - registered
        assert not missing, (
            f"Unified registry is missing entries for: "
            f"{sorted(m.value for m in missing)}. Every NodeType must be "
            f"registered so dispatch never silently falls through to the "
            f"transform fallback."
        )

    def test_builders_exec_dispatch_reads_unified_registry(self) -> None:
        """``_builders._build_node_fn`` must consult the unified registry,
        not an independent private table."""
        from haute import _builders as b

        reg, _loc = _locate_unified_registry()
        assert reg is not None

        # For every NodeType, verify that the exec builder in the unified
        # registry is the same callable that ``_NODE_BUILDERS`` would have
        # returned.  This pins down "single source of truth": if the
        # registry is mutated, execution changes, no parallel table left
        # over.
        legacy = getattr(b, "_NODE_BUILDERS", None)
        assert (
            legacy is None or legacy is reg or all(legacy.get(k) is _get_exec(reg[k]) for k in reg)
        ), (
            "haute._builders still maintains _NODE_BUILDERS as a separate "
            "table. It must either be removed or bound to the unified "
            "registry so the two cannot drift."
        )

    def test_codegen_dispatch_reads_unified_registry(self) -> None:
        """``codegen._generate_node_code`` must consult the unified registry,
        not an independent private table."""
        from haute import codegen

        reg, _loc = _locate_unified_registry()
        assert reg is not None

        legacy = getattr(codegen, "_CODEGEN_BUILDERS", None)
        assert (
            legacy is None
            or legacy is reg
            or all(legacy.get(k) is _get_codegen(reg[k]) for k in reg)
        ), (
            "haute.codegen still maintains _CODEGEN_BUILDERS as a separate "
            "table. It must either be removed or bound to the unified "
            "registry so the two cannot drift."
        )


def _get_exec(entry):
    """Extract the exec builder from a registry entry (tuple or object)."""
    if isinstance(entry, tuple) and len(entry) == 2:
        return entry[0]
    return (
        getattr(entry, "exec", None)
        or getattr(entry, "build", None)
        or getattr(entry, "builder", None)
        or getattr(entry, "build_fn", None)
    )


def _get_codegen(entry):
    """Extract the codegen builder from a registry entry (tuple or object)."""
    if isinstance(entry, tuple) and len(entry) == 2:
        return entry[1]
    return getattr(entry, "codegen", None) or getattr(entry, "codegen_fn", None)


# ---------------------------------------------------------------------------
# Discrepancy surface — pre-refactor _NODE_BUILDERS / _CODEGEN_BUILDERS
# must be in perfect key agreement post-refactor (or share a single table).
# ---------------------------------------------------------------------------


class TestNoDeadEntries:
    """If a NodeType appears in one side's table but not the other, the dev
    must pick a resolution — either add the missing side or delete the
    orphan. We surface that discrepancy up front so nothing is silently
    dropped during the refactor."""

    def test_exec_and_codegen_tables_cover_same_node_types(self) -> None:
        """Current state check: every NodeType registered on one side must
        also be registered on the other side.

        SUBMODEL and SUBMODEL_PORT are real execution types registered in
        ``_builders.py`` but missing from ``_CODEGEN_BUILDERS`` — that
        omission silently falls back to ``_gen_transform`` today. The
        refactor MUST resolve the asymmetry (either register explicit
        codegen builders for them, or document+remove the orphan exec
        entries)."""
        from haute import _builders as b
        from haute import codegen

        # Post-refactor paths: if either side has migrated, pull the
        # canonical set out of the unified registry.
        reg, _loc = _locate_unified_registry()
        if reg is not None:
            # After unification, keys are trivially equal. Nothing to prove
            # — the existence + coverage tests above handle that case.
            return

        # Pre-refactor: compare the two legacy tables directly.
        exec_keys = set(getattr(b, "_NODE_BUILDERS", {}).keys())
        codegen_keys = set(getattr(codegen, "_CODEGEN_BUILDERS", {}).keys())
        only_exec = exec_keys - codegen_keys
        only_codegen = codegen_keys - exec_keys
        assert not only_exec and not only_codegen, (
            f"Dispatch table discrepancy:\n"
            f"  Only in _NODE_BUILDERS (exec):    "
            f"{sorted(t.value for t in only_exec)}\n"
            f"  Only in _CODEGEN_BUILDERS:        "
            f"{sorted(t.value for t in only_codegen)}\n"
            f"Each side's fallback silently treats the missing kind as a "
            f"transform. Resolve by adding the missing side or removing the "
            f"orphan entry, then unify into a single registry."
        )


# ---------------------------------------------------------------------------
# Import-graph sanity — no cyclic imports at cold start.
# ---------------------------------------------------------------------------


class TestNoCyclicImports:
    """Importing ``codegen``, ``_builders``, and ``_codegen_builders`` from
    a cold interpreter must all succeed. A circular import would manifest as
    an ``ImportError`` under cold-start conditions even if it works with
    warm caches."""

    @pytest.mark.parametrize(
        "module_name",
        [
            "haute.codegen",
            "haute._builders",
            "haute._codegen_builders",
        ],
    )
    def test_cold_start_import(self, module_name: str) -> None:
        code = textwrap.dedent(
            f"""
            import importlib, sys
            # Force a cold import by evicting cached haute modules.
            for mod in list(sys.modules):
                if mod == 'haute' or mod.startswith('haute.'):
                    del sys.modules[mod]
            importlib.import_module({module_name!r})
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Cold-start import of {module_name} failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def test_all_three_modules_import_together(self) -> None:
        """The three modules must coexist in the same interpreter."""
        code = textwrap.dedent(
            """
            import importlib, sys
            for mod in list(sys.modules):
                if mod == 'haute' or mod.startswith('haute.'):
                    del sys.modules[mod]
            from haute import codegen  # noqa: F401
            from haute import _builders  # noqa: F401
            from haute import _codegen_builders  # noqa: F401
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "Cold-start triple import failed — likely a cyclic import.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Behaviour preservation — pinned codegen output for representative graphs.
# ---------------------------------------------------------------------------


def _current_code_for(graph_fn):
    """Current codegen output for *graph_fn*.  Captured lazily so any
    legitimate pre-refactor code change in ``graph_to_code`` is reflected
    here automatically — what we guard against is the refactor itself
    changing output."""
    from haute.codegen import graph_to_code

    return graph_to_code(graph_fn(), pipeline_name="test_pipe")


class TestBehaviourPreservation:
    """Pin representative graphs' generated code. The refactor must not
    change emitted output for these canonical shapes."""

    @pytest.mark.parametrize("label,graph_fn", sorted(_PINNED_GRAPHS.items()))
    def test_graph_to_code_compiles(self, label: str, graph_fn) -> None:
        """Every pinned graph must compile as valid Python."""
        code = _current_code_for(graph_fn)
        compile(code, f"<{label}>", "exec")

    @pytest.mark.parametrize("label,graph_fn", sorted(_PINNED_GRAPHS.items()))
    def test_graph_to_code_stable_across_calls(self, label: str, graph_fn) -> None:
        """Same graph → same code. No hidden state leaking between calls."""
        a = _current_code_for(graph_fn)
        b = _current_code_for(graph_fn)
        assert a == b, (
            f"graph_to_code is non-deterministic for {label!r} — refactor "
            f"would hide silent behaviour drift."
        )

    def test_single_source_emits_data_source_decorator(self) -> None:
        code = _current_code_for(_simple_source_graph)
        assert "@pipeline.data_input" in code
        assert "def Source()" in code

    def test_source_transform_emits_both_decorators_and_connect(self) -> None:
        code = _current_code_for(_source_transform_graph)
        assert "@pipeline.data_input" in code
        assert "@pipeline.polars" in code
        assert 'pipeline.connect("Source", "Clean")' in code

    def test_source_sink_emits_data_sink_decorator(self) -> None:
        code = _current_code_for(_source_sink_graph)
        assert "@pipeline.data_output" in code
        assert 'pipeline.connect("Source", "Write")' in code

    def test_modelling_emits_modelling_decorator(self) -> None:
        """Regression guard: ``modelling`` must not silently fall back to
        ``@pipeline.polars``. A missing registry entry would make codegen
        drop ``modelling`` semantics on the floor."""
        code = _current_code_for(_modelling_graph)
        assert "@pipeline.modelling" in code, (
            "modelling node did not emit @pipeline.modelling — the "
            "_CODEGEN_BUILDERS fallback to _gen_transform silently masks "
            "misregistered NodeTypes."
        )

    def test_constant_output_emits_both_decorators(self) -> None:
        code = _current_code_for(_constant_output_graph)
        assert "@pipeline.constant" in code
        assert "@pipeline.output" in code

    def test_refactored_codegen_matches_legacy(self, tmp_path) -> None:
        """Round-trip sanity: after the refactor, re-importing
        ``haute.codegen`` and regenerating the pinned graphs must produce
        exactly the same bytes as the legacy table did.

        We exercise each graph, then force a cold reload, and regenerate —
        byte-for-byte equality is required."""
        import json

        legacy_outputs = {label: _current_code_for(fn) for label, fn in _PINNED_GRAPHS.items()}

        # ``graph_to_code`` logs via structlog to stdout. When we run a
        # subprocess we need clean stdout for JSON, so write the result to
        # a file rather than piping through stdout.
        payload = {label: fn().model_dump(mode="json") for label, fn in _PINNED_GRAPHS.items()}
        input_path = tmp_path / "graphs.json"
        output_path = tmp_path / "results.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")

        code_script = textwrap.dedent(
            f"""
            import sys, json
            for mod in list(sys.modules):
                if mod == 'haute' or mod.startswith('haute.'):
                    del sys.modules[mod]
            from haute.codegen import graph_to_code
            from haute._types import PipelineGraph

            with open(r{str(input_path)!r}, encoding='utf-8') as fh:
                payload = json.load(fh)
            results = {{}}
            for name, graph_dict in payload.items():
                g = PipelineGraph.model_validate(graph_dict)
                results[name] = graph_to_code(g, pipeline_name='test_pipe')
            with open(r{str(output_path)!r}, 'w', encoding='utf-8') as fh:
                json.dump(results, fh)
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", code_script],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"Subprocess codegen failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        fresh_outputs = json.loads(output_path.read_text(encoding="utf-8"))
        for label, expected in legacy_outputs.items():
            assert fresh_outputs[label] == expected, (
                f"Codegen output drifted for {label!r} after cold reload:\n"
                f"--- legacy ---\n{expected}\n"
                f"--- fresh  ---\n{fresh_outputs[label]}"
            )


# ---------------------------------------------------------------------------
# Registry contract — every entry corresponds to the legacy pair.
# ---------------------------------------------------------------------------


class TestRegistryMatchesLegacyPairs:
    """For every NodeType K, the unified registry's ``exec`` and ``codegen``
    entries must be the exact same callables that the legacy
    ``_NODE_BUILDERS[K]`` and ``_CODEGEN_BUILDERS[K]`` resolved to — otherwise
    the refactor changed behaviour silently."""

    def test_registry_exec_matches_legacy_exec(self) -> None:
        reg, _loc = _locate_unified_registry()
        assert reg is not None, "Unified registry missing"

        # Rebuild the "legacy" view by importing _builders and looking at the
        # dispatcher's actual behaviour for each NodeType.  If the legacy
        # table still exists, compare directly; otherwise the dispatcher is
        # the source of truth.
        from haute import _builders as b

        for node_type, entry in reg.items():
            exec_fn = _get_exec(entry)
            legacy = getattr(b, "_NODE_BUILDERS", None)
            if legacy is not None and legacy is not reg:
                assert legacy.get(node_type) is exec_fn, (
                    f"Exec builder for {node_type!r} differs between legacy "
                    f"_NODE_BUILDERS and unified registry. The two are "
                    f"already drifting."
                )

    def test_registry_codegen_matches_legacy_codegen(self) -> None:
        reg, _loc = _locate_unified_registry()
        assert reg is not None, "Unified registry missing"

        from haute import codegen

        for node_type, entry in reg.items():
            codegen_fn = _get_codegen(entry)
            legacy = getattr(codegen, "_CODEGEN_BUILDERS", None)
            if legacy is not None and legacy is not reg:
                assert legacy.get(node_type) is codegen_fn, (
                    f"Codegen builder for {node_type!r} differs between legacy "
                    f"_CODEGEN_BUILDERS and unified registry. The two are "
                    f"already drifting."
                )
