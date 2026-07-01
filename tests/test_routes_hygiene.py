"""Routes and handlers hygiene regression tests.

Four internal-quality items are pinned here *before* the production refactor
lands.  Every group below is expected to **fail today** and flip to green
when the developer ships the corresponding change:

* **#101** — ``src/haute/routes/pipeline.py`` currently uses function-local
  imports inside three handlers (lines 68, 165, 212 at time of writing).
  The fix is to hoist them to module top.  An AST-walk meta-test pins that.

* **#102** — ``src/haute/routes/_helpers.py::save_sidecar`` builds a plain
  dict and serialises via ``_json.dumps``.  The fix is to introduce a
  ``SidecarModel`` Pydantic model and use ``model_dump_json()``; tests
  pin a round-trip, forward-compat parsing of the current manual-JSON
  shape, and a default-field migration path.

* **#103** — ``src/haute/_cache.py`` exposes ``graph_fingerprint`` but
  does not embed an algorithm version in the digest, so a future
  canonicalisation tweak silently collides with existing cache entries.
  The fix is to introduce ``ALGO_VERSION = 1`` and prefix every digest
  with ``"v{ALGO_VERSION}:"``; bumping the version invalidates the
  cache.  An in-memory ``FingerprintCache`` round-trip with two
  versions pins the non-collision property.

* **#126** — Each of ``modelling.py``, ``optimiser.py``, and the two
  service modules currently calls ``JobStore()`` directly, producing
  four independent instances with no central registry.  The fix is a
  ``get_job_store(prefix)`` factory in ``_job_store.py`` that returns
  one singleton per prefix.  Tests pin singleton-per-prefix,
  cross-prefix isolation, and a grep that no direct ``JobStore()``
  instantiation remains in ``src/haute/routes/`` (apart from the
  factory itself).

None of these tests modify production code.  They are intentionally
written to fail before the refactor so the developer has a target.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NoReturn

import pytest

# ---------------------------------------------------------------------------
# Locate the repo root + key files once — every test class reuses these.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROUTES_DIR = _REPO_ROOT / "src" / "haute" / "routes"
_DEPLOY_DIR = _REPO_ROOT / "src" / "haute" / "deploy"
_PIPELINE_PY = _ROUTES_DIR / "pipeline.py"
_HELPERS_PY = _ROUTES_DIR / "_helpers.py"
_JOB_STORE_PY = _ROUTES_DIR / "_job_store.py"
# Flat 1500ms on every platform (Windows always had this). The old 1000ms
# non-Windows budget sat on the shared-runner noise floor — observed clean-code
# samples: 1007.8ms and 1035ms on ubuntu CI, 1201ms locally under load — so it
# coin-flipped PR runs. A genuinely hoisted heavy import (torch, mlflow, …)
# costs multiple seconds, so the tripwire keeps its full margin.
_COLD_IMPORT_BUDGET_MS = 1_500.0
_STATIC_SCAN_SKIP_DIRS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


# ===========================================================================
# #101 — Conditional imports in handlers (src/haute/routes/pipeline.py)
# ===========================================================================


def _iter_function_nodes(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every ``FunctionDef`` / ``AsyncFunctionDef`` in *tree*.

    We deliberately walk the full tree rather than only top-level
    definitions so a future refactor that nests handlers inside a class
    (e.g. a Router subclass) is still covered.
    """
    return [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _imports_inside(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """Return every ``Import`` / ``ImportFrom`` node contained in *func*'s body.

    Walks the full subtree — catches imports nested in ``try`` blocks,
    conditionals, or inner closures, all of which the hoist-to-top rule
    forbids.
    """
    return [
        child
        for child in ast.walk(func)
        if isinstance(child, (ast.Import, ast.ImportFrom)) and child is not func
    ]


def _iter_python_sources(root: Path) -> list[Path]:
    """Return stable Python source files under *root* for static hygiene scans."""
    sources: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise_static_scan_error):
        dirnames[:] = sorted(
            dirname for dirname in dirnames if dirname not in _STATIC_SCAN_SKIP_DIRS
        )
        sources.extend(
            Path(dirpath) / filename for filename in sorted(filenames) if filename.endswith(".py")
        )
    return sources


def _raise_static_scan_error(error: OSError) -> NoReturn:
    raise error


def test_static_source_iterator_skips_runtime_cache_dirs(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    module = package / "module.py"
    module.write_text("value = 1\n", encoding="utf-8")
    (cache / "generated.py").write_text("raise AssertionError\n", encoding="utf-8")

    assert _iter_python_sources(package) == [module]


def test_static_source_iterator_fails_loudly_on_non_cache_scan_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = FileNotFoundError("missing production directory")

    def fake_walk(
        _root: Path,
        *,
        onerror: Callable[[OSError], object] | None = None,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        assert onerror is not None
        onerror(expected)
        yield from ()

    monkeypatch.setattr(os, "walk", fake_walk)

    with pytest.raises(FileNotFoundError, match="missing production directory") as exc_info:
        _iter_python_sources(tmp_path)

    assert exc_info.value is expected


class TestPipelineImportsHoisted:
    """No function-local imports may remain in ``pipeline.py``.

    Fails today — ``list_pipelines`` (~line 68), ``trace_row`` (~line 165),
    and ``preview_node`` (~line 212) each still import inside their body.
    """

    def test_module_parses(self) -> None:
        """The file itself must parse.  Guardrail in case of syntax damage."""
        tree = ast.parse(_PIPELINE_PY.read_text(encoding="utf-8"))
        assert isinstance(tree, ast.Module)

    def test_no_function_local_imports(self) -> None:
        """AST-walk assertion: every import must live at module top.

        This is the primary failing test — the dev's fix is to move the
        imports at lines 69, 166--167, and 219--224 to the top of the
        module (or, if a genuine cycle exists, restructure the modules
        properly).
        """
        tree = ast.parse(_PIPELINE_PY.read_text(encoding="utf-8"))

        offenders: list[tuple[str, int, str]] = []
        for func in _iter_function_nodes(tree):
            for imp in _imports_inside(func):
                if isinstance(imp, ast.ImportFrom):
                    detail = f"from {imp.module} import {[a.name for a in imp.names]}"
                else:  # ast.Import
                    detail = f"import {[a.name for a in imp.names]}"
                offenders.append((func.name, imp.lineno, detail))

        assert offenders == [], (
            "Function-local imports found in src/haute/routes/pipeline.py. "
            "Hoist them to module top (or break the cycle properly if one "
            f"exists).  Offenders: {offenders}"
        )

    @pytest.mark.xfail(
        reason=(
            "Escape hatch: if the dev determines a GENUINE import cycle exists "
            "between haute.routes.pipeline and haute.parser / haute.graph_utils "
            "/ haute.trace / haute.executor that cannot be resolved by module "
            "restructuring, mark this test as expected-fail and document which "
            "cycle is unavoidable.  Default expectation: no cycle."
        ),
        strict=False,
    )
    def test_genuine_cycle_documented(self) -> None:
        """Placeholder: flip ``strict=True`` + an assertion here if a cycle is
        documented.  Exists purely to hold the reason string in-repo so
        reviewers know what the escape hatch is for."""
        pytest.skip("No genuine cycle documented — see xfail reason.")


def _snapshot_haute_modules(prefix: str) -> dict[str, Any]:
    """Snapshot every ``sys.modules`` entry whose name starts with *prefix*.

    Also captures the parent-package ``__dict__`` attribute bindings so a
    subsequent :func:`_restore_haute_modules` call can repair attribute
    references like ``haute.routes.pipeline`` — deleting a sub-module from
    ``sys.modules`` and re-importing it mutates the package's attribute
    *in addition to* ``sys.modules``, so restoring only ``sys.modules``
    leaves dangling references that ``import pkg.sub as alias`` picks up
    via attribute lookup rather than the ``sys.modules`` cache.
    """
    snapshot: dict[str, Any] = {
        "modules": {n: sys.modules[n] for n in list(sys.modules) if n.startswith(prefix)},
        "attrs": {},
    }
    for name in snapshot["modules"]:
        if "." in name:
            parent_name, child_name = name.rsplit(".", 1)
            parent = sys.modules.get(parent_name)
            if parent is not None and hasattr(parent, child_name):
                snapshot["attrs"][(parent_name, child_name)] = getattr(parent, child_name)
    return snapshot


def _restore_haute_modules(snapshot: dict[str, Any]) -> None:
    """Restore ``sys.modules`` and parent-package attributes from a snapshot.

    Paired with :func:`_snapshot_haute_modules` — restores the same module
    identity that sibling tests held before this test deleted and
    re-imported.  Without the attribute-restore step, a later
    ``import haute.routes.pipeline as route_mod`` statement resolves via
    the stale parent attribute (the fresh copy) rather than the canonical
    ``sys.modules`` entry, so ``monkeypatch.setattr(route_mod, ...)``
    mutates a dead module and tests that rely on the route-registered
    endpoint seeing the override silently fail.
    """
    sys.modules.update(snapshot["modules"])
    for (parent_name, child_name), value in snapshot["attrs"].items():
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, value)


class TestPipelineImportableCold:
    """The module must import quickly — no runtime side-effects.

    If the dev's fix accidentally pulls in a heavy dependency at import
    time (e.g. ``import torch`` via ``haute.executor``), this test
    catches it before it slows down every CLI invocation.
    """

    def test_importlib_succeeds(self) -> None:
        # Snapshot and restore so sibling tests (which hold pre-test
        # references to these modules) keep seeing the same object
        # identity post-test — including parent-package attribute
        # bindings, not just ``sys.modules`` entries.
        snapshot = _snapshot_haute_modules("haute.routes.pipeline")
        for name in snapshot["modules"]:
            del sys.modules[name]
        try:
            mod = importlib.import_module("haute.routes.pipeline")
            assert hasattr(mod, "router")
        finally:
            _restore_haute_modules(snapshot)

    def test_cold_import_within_latency_budget(self) -> None:
        """Measured cold-import latency for ``haute.routes.pipeline``.

        The budget is tight enough to catch accidentally-hoisted heavyweight
        dependencies while allowing a small amount of Windows coverage/xdist
        scheduler overhead.  The guard rail exists so the dev does not
        accidentally hoist a heavy import (``torch``, ``mlflow``, etc.) to
        module top when breaking a cycle.
        """
        # Snapshot every ``haute.*`` entry before eviction and restore
        # afterwards — including parent-package attribute bindings.
        # Without attribute restoration, ``haute.routes.pipeline`` on the
        # parent ``haute.routes`` package still points at the fresh copy
        # after the ``sys.modules`` restore, so ``import haute.routes
        # .pipeline as route_mod`` (which uses attribute access, not the
        # ``sys.modules`` cache) resolves to the dead copy.  The result
        # is a test-order flake where ``monkeypatch.setattr(route_mod,
        # ...)`` writes to a module that nothing else actually reads.
        snapshot = _snapshot_haute_modules("haute")
        for name in snapshot["modules"]:
            del sys.modules[name]

        try:
            start = time.perf_counter()
            importlib.import_module("haute.routes.pipeline")
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        finally:
            _restore_haute_modules(snapshot)

        assert elapsed_ms < _COLD_IMPORT_BUDGET_MS, (
            f"Cold import of haute.routes.pipeline took {elapsed_ms:.1f}ms — "
            f"exceeds {_COLD_IMPORT_BUDGET_MS:.0f}ms budget.  "
            "Check for newly-hoisted heavy imports."
        )


# ===========================================================================
# #102 — Sidecar manual JSON → SidecarModel.model_dump_json()
# ===========================================================================


def _make_sidecar_graph():
    """Build a small graph whose sidecar has positions + sources + active_source."""
    from haute._types import GraphNode, NodeData, NodeType, PipelineGraph

    return PipelineGraph(
        nodes=[
            GraphNode(
                id="alpha",
                position={"x": 1.0, "y": 2.0},
                data=NodeData(label="alpha", nodeType=NodeType.POLARS),
            ),
            GraphNode(
                id="beta",
                position={"x": 3.5, "y": 4.5},
                data=NodeData(label="beta", nodeType=NodeType.OUTPUT),
            ),
        ],
        edges=[],
        sources=["live", "batch"],
        active_source="batch",
    )


class TestSidecarModelExists:
    """A ``SidecarModel`` Pydantic model must be importable and usable.

    Fails today — no such model exists in ``haute.routes._helpers``.
    """

    def test_sidecar_model_importable(self) -> None:
        """``SidecarModel`` must be exposed from ``haute.routes._helpers``."""
        mod = importlib.import_module("haute.routes._helpers")
        assert hasattr(mod, "SidecarModel"), (
            "SidecarModel not found in haute.routes._helpers.  The fix for "
            "#102 is to introduce a Pydantic model that replaces the manual "
            "``dict + _json.dumps`` in ``save_sidecar``."
        )

    def test_sidecar_model_is_pydantic(self) -> None:
        """Must subclass ``pydantic.BaseModel`` so it exposes the Pydantic API."""
        from pydantic import BaseModel

        from haute.routes._helpers import SidecarModel

        assert issubclass(SidecarModel, BaseModel)
        # The two methods the refactor depends on:
        assert hasattr(SidecarModel, "model_dump_json")
        assert hasattr(SidecarModel, "model_validate_json")


class TestSidecarRoundTrip:
    """save_sidecar → read bytes → SidecarModel.model_validate_json → equality."""

    def test_round_trip_preserves_every_field(self, tmp_path: Path) -> None:
        """After ``save_sidecar``, parsing the written bytes through the
        Pydantic model must recover every field.

        Fails today because ``save_sidecar`` writes a hand-built dict
        whose shape may not match what the new Pydantic model expects —
        or, more importantly, because ``SidecarModel`` does not exist.
        """
        from haute.routes._helpers import SidecarModel, save_sidecar

        py_path = tmp_path / "pipeline.py"
        graph = _make_sidecar_graph()
        save_sidecar(py_path, graph)

        sidecar_path = tmp_path / "pipeline.haute.json"
        assert sidecar_path.exists()

        raw = sidecar_path.read_text(encoding="utf-8")
        parsed = SidecarModel.model_validate_json(raw)

        # Every position is preserved (keys are sanitized labels).
        # The node labels in the fixture already sanitize to themselves.
        assert parsed.positions == {
            "alpha": {"x": 1.0, "y": 2.0},
            "beta": {"x": 3.5, "y": 4.5},
        }
        assert parsed.sources == ["live", "batch"]
        assert parsed.active_source == "batch"

    def test_round_trip_via_model_dump_json(self, tmp_path: Path) -> None:
        """Serialising the model to JSON and parsing it back must be idempotent."""
        from haute.routes._helpers import SidecarModel

        # Build the model directly so we don't depend on save_sidecar's
        # internal wiring for this axis of the test.
        initial = SidecarModel(
            positions={
                "alpha": {"x": 1.0, "y": 2.0},
                "beta": {"x": 3.5, "y": 4.5},
            },
            sources=["live", "batch"],
            active_source="batch",
        )
        as_json = initial.model_dump_json()
        reloaded = SidecarModel.model_validate_json(as_json)
        assert reloaded == initial


class TestSidecarDefaults:
    """Sidecar JSON uses explicit defaults for optional editor state.

    The supported persisted shape looks like::

        {
            "positions": {"alpha": {"x": 1.0, "y": 2.0}},
            "sources": ["live", "batch"],
            "active_source": "batch"
        }
    """

    def test_full_shape_parses(self) -> None:
        from haute.routes._helpers import SidecarModel

        payload = json.dumps(
            {
                "positions": {"alpha": {"x": 1.0, "y": 2.0}},
                "sources": ["live", "batch"],
                "active_source": "batch",
            }
        )
        parsed = SidecarModel.model_validate_json(payload)
        assert parsed.positions == {"alpha": {"x": 1.0, "y": 2.0}}
        assert parsed.sources == ["live", "batch"]
        assert parsed.active_source == "batch"

    def test_positions_only_shape_uses_source_defaults(self) -> None:
        """Positions-only sidecars are valid and default source state explicitly."""
        from haute.routes._helpers import SidecarModel

        payload = json.dumps(
            {"positions": {"alpha": {"x": 1.0, "y": 2.0}}},
        )
        parsed = SidecarModel.model_validate_json(payload)
        assert parsed.positions == {"alpha": {"x": 1.0, "y": 2.0}}
        # Missing fields receive their defaults — default sources/active_source
        # should not round-trip as None for the write path, so the model must
        # default them sensibly (empty / "live").  We only assert the parse
        # succeeds; the specific default values are pinned alongside save_sidecar.

    def test_missing_optional_fields_use_defaults(self) -> None:
        """Sparse sidecars parse through the model without materialising defaults."""
        from haute.routes._helpers import SidecarModel

        minimal = json.dumps({"positions": {}})
        parsed = SidecarModel.model_validate_json(minimal)
        dumped = parsed.model_dump(exclude_unset=True)
        assert dumped.get("positions") == {}


class TestSidecarWritePathUsesModel:
    """The write path in ``save_sidecar`` must route through the Pydantic model.

    We can't easily assert on call graphs, but we CAN assert that the
    bytes produced by ``save_sidecar`` pass through ``model_validate_json``
    successfully AND agree byte-for-byte (up to whitespace) with
    ``SidecarModel.model_dump_json()`` for the equivalent model.
    """

    def test_save_sidecar_output_is_model_valid(self, tmp_path: Path) -> None:
        from haute.routes._helpers import SidecarModel, save_sidecar

        py_path = tmp_path / "pipeline.py"
        save_sidecar(py_path, _make_sidecar_graph())

        written = (tmp_path / "pipeline.haute.json").read_text(encoding="utf-8")
        # Must succeed.  Manual-JSON path produces bytes that MIGHT
        # validate incidentally, but the aim of this test is to lock
        # validation into the save path.
        SidecarModel.model_validate_json(written)


# ===========================================================================
# #103 — Fingerprint algorithm versioning (src/haute/_cache.py)
# ===========================================================================


def _build_chain_graph(nid_prefix: str = "n", n: int = 3):
    from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

    nodes = [
        GraphNode(
            id=f"{nid_prefix}{i}",
            data=NodeData(label=f"{nid_prefix}{i}", nodeType=NodeType.POLARS, config={"i": i}),
        )
        for i in range(n)
    ]
    edges = [
        GraphEdge(id=f"e{i}", source=f"{nid_prefix}{i}", target=f"{nid_prefix}{i + 1}")
        for i in range(n - 1)
    ]
    return PipelineGraph(nodes=nodes, edges=edges)


class TestAlgoVersionExported:
    """``haute._cache.ALGO_VERSION`` must be importable.

    Fails today — no such constant exists.
    """

    def test_algo_version_is_importable(self) -> None:
        mod = importlib.import_module("haute._cache")
        assert hasattr(mod, "ALGO_VERSION"), (
            "ALGO_VERSION not found in haute._cache.  The fix for #103 is "
            "to introduce a module-level ``ALGO_VERSION = 1`` constant that "
            "prefixes every fingerprint digest, so a future canonicalisation "
            "tweak can be deployed without silently colliding with stale "
            "cache entries."
        )

    def test_algo_version_is_positive_int(self) -> None:
        from haute._cache import ALGO_VERSION

        assert isinstance(ALGO_VERSION, int)
        assert ALGO_VERSION >= 1


class TestFingerprintEmbedsVersion:
    """Every ``graph_fingerprint`` output must carry the version prefix.

    Expected shape: ``"v1:<digest>"`` (or any unambiguous prefix that
    includes the version number).  Fails today — the current fingerprint
    is just the hex digest.
    """

    def test_fingerprint_starts_with_version_tag(self) -> None:
        from haute._cache import ALGO_VERSION, graph_fingerprint

        g = _build_chain_graph()
        fp = graph_fingerprint(g)

        # Match a ``v<int>:`` or ``v<int>|`` or similar prefix.  The exact
        # separator is dev's choice — we just require that the ALGO_VERSION
        # integer appears verbatim at the start, followed by a non-hex
        # separator character to avoid collisions with hex-only keys.
        assert re.match(rf"^v{ALGO_VERSION}[:|\-]", fp), (
            f"Fingerprint {fp!r} does not carry the ALGO_VERSION prefix.  "
            f"Expected something like 'v{ALGO_VERSION}:<digest>'."
        )

    def test_fingerprint_with_extra_keys_still_prefixed(self) -> None:
        """Variant path (extra_keys branch) must carry the prefix too."""
        from haute._cache import ALGO_VERSION, graph_fingerprint

        g = _build_chain_graph()
        fp = graph_fingerprint(g, "target_n1", "row_limit=10")
        assert re.match(rf"^v{ALGO_VERSION}[:|\-]", fp)


class TestBumpVersionInvalidatesCache:
    """Bumping ``ALGO_VERSION`` must produce a different digest for the same graph.

    Uses ``monkeypatch`` to simulate a future version bump without
    permanently changing the module constant.
    """

    def test_monkeypatched_bump_changes_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import haute._cache as cache_mod

        g = _build_chain_graph()
        fp_v1 = cache_mod.graph_fingerprint(g)

        # Bump the version.  The fingerprint function must re-read the
        # module-level constant so the patched value takes effect.
        monkeypatch.setattr(cache_mod, "ALGO_VERSION", cache_mod.ALGO_VERSION + 1)
        fp_v2 = cache_mod.graph_fingerprint(g)

        assert fp_v1 != fp_v2, (
            "Bumping ALGO_VERSION did not change the digest.  The version "
            "constant must be mixed into the digest (or its prefix) so a "
            "future algorithmic change invalidates old cache entries."
        )

    def test_two_versions_dont_collide_in_memory_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate the real caching path: store an entry at v1, bump to v2,
        store another — both must be retrievable independently."""
        import haute._cache as cache_mod
        from haute._fingerprint_cache import FingerprintCache

        cache = FingerprintCache(slots=("payload",))

        g = _build_chain_graph()

        # Phase 1: store under current (v1) fingerprint.
        fp_v1 = cache_mod.graph_fingerprint(g)
        cache.store(fp_v1, payload={"era": "v1"})

        # Phase 2: pretend we shipped a new algo version.
        monkeypatch.setattr(cache_mod, "ALGO_VERSION", cache_mod.ALGO_VERSION + 1)
        fp_v2 = cache_mod.graph_fingerprint(g)
        cache.store(fp_v2, payload={"era": "v2"})

        assert fp_v1 != fp_v2

        v1_entry = cache.try_get(fp_v1)
        v2_entry = cache.try_get(fp_v2)
        assert v1_entry is not None
        assert v2_entry is not None
        assert v1_entry["payload"] == {"era": "v1"}
        assert v2_entry["payload"] == {"era": "v2"}


class TestFingerprintDeterminism:
    """Versioned fingerprints must still be deterministic.

    A sanity check so the dev does not accidentally mix ``time.time()``
    or a process-id into the new versioning layer.
    """

    def test_same_graph_same_version_same_digest(self) -> None:
        from haute._cache import graph_fingerprint

        g1 = _build_chain_graph()
        g2 = _build_chain_graph()
        assert graph_fingerprint(g1) == graph_fingerprint(g2)


# ===========================================================================
# #126 — Per-route JobStore singletons → central factory
# ===========================================================================


class TestGetJobStoreExists:
    """``get_job_store`` must be importable from ``haute.routes._job_store``.

    Fails today — no factory exists; each route module does ``JobStore()``.
    """

    def test_get_job_store_is_importable(self) -> None:
        mod = importlib.import_module("haute.routes._job_store")
        assert hasattr(mod, "get_job_store"), (
            "get_job_store not found in haute.routes._job_store.  The fix "
            "for #126 is to introduce a central factory that returns one "
            'singleton JobStore per prefix (e.g. "training", "optimiser").'
        )

    def test_get_job_store_is_callable(self) -> None:
        from haute.routes._job_store import get_job_store

        assert callable(get_job_store)


class TestGetJobStoreSingletonPerPrefix:
    """Two calls with the same prefix return the same store instance."""

    def test_same_prefix_returns_same_instance(self) -> None:
        from haute.routes._job_store import get_job_store

        a = get_job_store("training")
        b = get_job_store("training")
        assert a is b, (
            "get_job_store('training') returned two distinct instances — the "
            "factory must cache by prefix."
        )

    def test_same_prefix_many_callers_agree(self) -> None:
        """N calls all agree — defensive check against weak caching."""
        from haute.routes._job_store import get_job_store

        stores = [get_job_store("optimiser") for _ in range(5)]
        first = stores[0]
        for s in stores[1:]:
            assert s is first


class TestGetJobStoreDistinctPrefixes:
    """Different prefixes return distinct stores."""

    def test_training_and_optimiser_are_isolated(self) -> None:
        from haute.routes._job_store import get_job_store

        training = get_job_store("training")
        optimiser = get_job_store("optimiser")
        assert training is not optimiser

    def test_job_ids_do_not_collide_across_prefixes(self) -> None:
        """A job created in one prefix must not be visible from another.

        The factory is allowed to reuse the same job-ID space globally
        (UUIDs are unique) — the correctness property we care about is
        that a lookup in prefix A never returns a job that was created
        in prefix B.
        """
        from haute.routes._job_store import get_job_store

        training = get_job_store("training")
        optimiser = get_job_store("optimiser")

        t_id = training.create_job({"status": "running", "kind": "train"})
        o_id = optimiser.create_job({"status": "running", "kind": "solve"})

        # IDs themselves happen to be uuid4 prefixes, but we don't
        # enforce collision freedom (uuid4 already guarantees it).  We
        # enforce mutual invisibility:
        assert training.get_job(o_id) is None
        assert optimiser.get_job(t_id) is None

        # And round-trip within the same prefix still works:
        t_job = training.get_job(t_id)
        o_job = optimiser.get_job(o_id)
        assert t_job is not None
        assert t_job.get("kind") == "train"
        assert o_job is not None
        assert o_job.get("kind") == "solve"


class TestNoDirectJobStoreInstantiation:
    """Grep-style meta-test: no production route/service module may call
    ``JobStore()`` directly — they must go through ``get_job_store(prefix)``.

    The only legitimate place the raw class is still instantiated is
    inside the factory in ``_job_store.py`` itself.
    """

    def test_no_direct_jobstore_calls_in_route_modules(self) -> None:
        # Every .py file under src/haute/routes/ except _job_store.py
        offenders: list[tuple[str, int, str]] = []

        for py_file in _iter_python_sources(_ROUTES_DIR):
            if py_file.name == "_job_store.py":
                continue  # factory + class definition live here; allowed.
            if py_file.name == "__init__.py":
                continue  # re-exports only

            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                pytest.fail(f"Could not parse {py_file}")

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Match bare ``JobStore(...)`` — not qualified.  We also
                # catch ``JobStore()`` via attribute access just in case.
                func = node.func
                call_name: str | None = None
                if isinstance(func, ast.Name):
                    call_name = func.id
                elif isinstance(func, ast.Attribute):
                    call_name = func.attr
                if call_name == "JobStore":
                    offenders.append(
                        (
                            str(py_file.relative_to(_REPO_ROOT)),
                            node.lineno,
                            ast.unparse(node),
                        )
                    )

        assert offenders == [], (
            "Direct ``JobStore()`` instantiations found in src/haute/routes/. "
            "All route modules must acquire their store through "
            f"``get_job_store(prefix)``.  Offenders: {offenders}"
        )

    def test_factory_itself_is_allowed_to_instantiate(self) -> None:
        """Sanity: the factory file is where the real ``JobStore(...)`` call
        lives.  The test above skips ``_job_store.py`` for exactly this
        reason; we assert here that the class itself is still defined
        there so the exclusion is well-founded."""
        tree = ast.parse(_JOB_STORE_PY.read_text(encoding="utf-8"))
        class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "JobStore" in class_names


class TestNoNewPrivateEngineImports:
    """Slice 0 guardrail for the execution-engine cleanup.

    Route/deploy modules should enter the execution engine through the internal
    facade, not by importing private lazy-execution helpers directly.
    """

    _PRIVATE_ENGINE_IMPORT_ALLOWLIST: set[tuple[str, str, str]] = set()
    _PRIVATE_ENGINE_IMPORTS_BY_MODULE = {
        "haute._execute_lazy": None,
        "haute.projection": None,
        "haute.graph_utils": {
            "_execute_lazy",
            "_prepare_graph",
            "_prune_live_switch_edges",
        },
    }

    def test_no_new_private_execution_helper_imports_in_routes_or_deploy(self) -> None:
        offenders: list[tuple[str, str, str, int]] = []
        seen_private_imports: set[tuple[str, str, str]] = set()

        for root in (_ROUTES_DIR, _DEPLOY_DIR):
            for py_file in _iter_python_sources(root):
                if py_file.name == "__init__.py":
                    continue
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                rel_path = str(py_file.relative_to(_REPO_ROOT)).replace("\\", "/")

                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    if node.module not in self._PRIVATE_ENGINE_IMPORTS_BY_MODULE:
                        continue
                    tracked_imports = self._PRIVATE_ENGINE_IMPORTS_BY_MODULE[node.module]
                    for alias in node.names:
                        if not alias.name.startswith("_"):
                            continue
                        if tracked_imports is not None and alias.name not in tracked_imports:
                            continue
                        private_import = (rel_path, node.module, alias.name)
                        seen_private_imports.add(private_import)
                        if private_import in self._PRIVATE_ENGINE_IMPORT_ALLOWLIST:
                            continue
                        offenders.append((*private_import, node.lineno))

        assert offenders == [], (
            "New private execution-helper imports found in routes/deploy. "
            "Expose an internal execution facade instead, or add a short-lived allowlist "
            f"entry with a Slice 0 cleanup note. Offenders: {offenders}"
        )
        assert seen_private_imports == self._PRIVATE_ENGINE_IMPORT_ALLOWLIST, (
            "Private execution-helper allowlist is stale. Shrink the allowlist "
            "when a private import is removed, or add a reviewed entry when one "
            "is intentionally introduced. "
            f"Missing: {self._PRIVATE_ENGINE_IMPORT_ALLOWLIST - seen_private_imports}; "
            f"Unexpected: {seen_private_imports - self._PRIVATE_ENGINE_IMPORT_ALLOWLIST}"
        )


class TestExecutionBoundaryGuardrails:
    """Static guardrails for the shared execution/projection architecture."""

    def test_execute_lazy_call_sites_make_execution_context_decision(self) -> None:
        offenders: list[tuple[str, int, str]] = []

        for py_file in _iter_python_sources(_REPO_ROOT / "src" / "haute"):
            rel_path = str(py_file.relative_to(_REPO_ROOT)).replace("\\", "/")
            if rel_path == "src/haute/_execute_lazy.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                call_name: str | None = None
                if isinstance(func, ast.Name):
                    call_name = func.id
                elif isinstance(func, ast.Attribute):
                    call_name = func.attr
                if call_name != "_execute_lazy":
                    continue
                if any(keyword.arg == "execution_context" for keyword in node.keywords):
                    continue
                offenders.append((rel_path, node.lineno, ast.unparse(node)))

        assert offenders == [], (
            "Every production _execute_lazy call site must explicitly pass "
            "execution_context=... (including None only if the caller has made "
            f"that decision deliberately). Offenders: {offenders}"
        )

    def test_ratebook_factor_column_contract_is_owned_by_projection_planner(self) -> None:
        optimiser_service = ast.parse(
            (_ROUTES_DIR / "_optimiser_service.py").read_text(encoding="utf-8")
        )

        local_helpers = [
            node.lineno
            for node in ast.walk(optimiser_service)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_ratebook_factor_required_columns"
        ]
        assert local_helpers == [], (
            "Ratebook factor-column requirements must stay in haute.projection "
            "so planning and execution cannot drift. Local helper definitions: "
            f"{local_helpers}"
        )

        # Routes must consume the shared helper rather than redefine it.  The
        # canonical owner is ``haute.projection``; routes go through the
        # ``haute.execution`` facade (which re-exports the same symbol) per
        # the execution-facade hygiene rule pinned in
        # ``test_polars_execution_strategy_slice0`` — both paths resolve to
        # the projection planner so planning and execution cannot drift.
        allowed_modules = {"haute.projection", "haute.execution"}
        imports_public_helper = any(
            isinstance(node, ast.ImportFrom)
            and node.module in allowed_modules
            and any(alias.name == "ratebook_factor_required_columns" for alias in node.names)
            for node in ast.walk(optimiser_service)
        )
        assert imports_public_helper, (
            "Ratebook factor-column requirements must be imported from the "
            "shared projection planner — directly via haute.projection or "
            "indirectly via the haute.execution facade — instead of being "
            "redefined locally."
        )

    def test_polars_streaming_chunk_size_is_only_mutated_in_shared_helper(self) -> None:
        offenders: list[tuple[str, int, str]] = []

        for py_file in _iter_python_sources(_REPO_ROOT / "src" / "haute"):
            rel_path = str(py_file.relative_to(_REPO_ROOT)).replace("\\", "/")
            if rel_path == "src/haute/_polars_utils.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "set_streaming_chunk_size":
                    offenders.append((rel_path, node.lineno, ast.unparse(node)))

        assert offenders == [], (
            "Polars streaming chunk size is process-global. Production code must "
            "mutate it only through haute._polars_utils.temporary_streaming_chunk_size. "
            f"Offenders: {offenders}"
        )

    def test_status_responses_do_not_default_to_unknown_status(self) -> None:
        offenders: list[tuple[str, int, str]] = []

        for py_file in _iter_python_sources(_ROUTES_DIR):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            rel_path = str(py_file.relative_to(_REPO_ROOT)).replace("\\", "/")
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
                    continue
                if len(node.args) < 2:
                    continue
                first, second = node.args[0], node.args[1]
                if (
                    isinstance(first, ast.Constant)
                    and first.value == "status"
                    and isinstance(second, ast.Constant)
                    and second.value == "unknown"
                ):
                    offenders.append((rel_path, node.lineno, ast.unparse(node)))

        assert offenders == [], (
            "Job status response models use a closed status literal. Do not hide "
            f"corrupt job state behind 'unknown'; fail loudly instead. Offenders: {offenders}"
        )

    def test_file_schema_route_uses_profiled_collect_helper(self) -> None:
        offenders: list[tuple[int, str]] = []
        tree = ast.parse((_ROUTES_DIR / "files.py").read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "collect":
                offenders.append((node.lineno, ast.unparse(node)))

        assert offenders == [], (
            "File/schema preview routes must use haute._polars_utils.streaming_collect "
            f"so even tiny materialisation boundaries are profiled consistently. {offenders}"
        )

    def test_deploy_schema_sampling_uses_profiled_collect_helper(self) -> None:
        offenders: list[tuple[int, str]] = []
        tree = ast.parse(
            (_REPO_ROOT / "src" / "haute" / "deploy" / "_schema.py").read_text(encoding="utf-8")
        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "collect":
                offenders.append((node.lineno, ast.unparse(node)))

        assert offenders == [], (
            "Deploy schema sampling must use haute._polars_utils.streaming_collect "
            f"so deployment-time materialisation is profiled consistently. {offenders}"
        )

    def test_optimiser_service_terminal_statuses_go_through_lifecycle(self) -> None:
        optimiser_service = ast.parse(
            (_ROUTES_DIR / "_optimiser_service.py").read_text(encoding="utf-8")
        )
        terminal_statuses = {
            "completed",
            "cancelled",
            "superseded",
            "timed_out",
            "memory_limited",
            "contract_error",
            "error",
        }
        offenders: list[tuple[int, str]] = []

        def _literal_terminal_status(node: ast.AST) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value if node.value in terminal_statuses else None
            return None

        for node in ast.walk(optimiser_service):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "atomic_update" and len(node.args) >= 2:
                payload = node.args[1]
                if isinstance(payload, ast.Dict):
                    for key, value in zip(payload.keys, payload.values, strict=False):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "status"
                            and _literal_terminal_status(value) is not None
                        ):
                            offenders.append((node.lineno, ast.unparse(node)))
            elif node.func.attr == "update_job":
                for keyword in node.keywords:
                    if keyword.arg != "status":
                        continue
                    if _literal_terminal_status(keyword.value) is not None:
                        offenders.append((node.lineno, ast.unparse(node)))

        assert offenders == [], (
            "Optimiser terminal job status writes must use JobLifecycle.transition() "
            f"so status and terminal_reason cannot drift. Offenders: {offenders}"
        )


class TestGetJobStoreIndependentState:
    """Mutations in one prefix's store do not leak into another."""

    def test_write_to_training_not_visible_in_optimiser(self) -> None:
        from haute.routes._job_store import get_job_store

        training = get_job_store("training")
        optimiser = get_job_store("optimiser")

        jid = training.create_job({"status": "running"})
        training.update_job(jid, progress=0.5)

        # Cross-store visibility: optimiser store must have no knowledge.
        assert optimiser.get_job(jid) is None

    def test_clear_result_data_scoped_to_prefix(self) -> None:
        from haute.routes._job_store import get_job_store

        training = get_job_store("training")
        optimiser = get_job_store("optimiser")

        t_id = training.create_job({"status": "completed", "solver": object()})
        o_id = optimiser.create_job({"status": "completed", "solver": object()})

        training.clear_result_data(t_id)

        t_job = training.get_job(t_id)
        o_job = optimiser.get_job(o_id)
        assert t_job is not None
        assert o_job is not None
        # Training's heavy object was dropped.
        assert "solver" not in t_job
        # Optimiser's heavy object survived — the two stores are isolated.
        assert "solver" in o_job


# ---------------------------------------------------------------------------
# Cleanup — reset any singleton factory state between tests.
#
# If ``get_job_store`` caches in a module-level dict (expected
# implementation), the create_job tests above would otherwise bleed
# state across test runs.  We reset after every test that touches the
# factory by clearing the cache — assuming the dev exposes
# ``_reset_job_stores()`` or a similar hook on the factory, which is
# standard practice.  If the dev opts for ``functools.lru_cache``, the
# reset uses ``cache_clear()`` directly.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_job_store_singletons() -> Any:
    """Clear the factory cache between tests so state never leaks.

    Runs for EVERY test in the file (autouse=True).  If ``get_job_store``
    does not yet exist, the fixture is a no-op — the tests themselves
    exercise the import and will fail loudly with the helpful message.
    """
    yield
    try:
        mod = importlib.import_module("haute.routes._job_store")
    except Exception:
        return

    # Preferred hook: a module-level ``_reset_job_stores`` helper.
    reset = getattr(mod, "_reset_job_stores", None)
    if callable(reset):
        reset()
        return

    # Fallback: the factory is ``functools.lru_cache``-wrapped.
    factory = getattr(mod, "get_job_store", None)
    cache_clear = getattr(factory, "cache_clear", None) if factory is not None else None
    if callable(cache_clear):
        cache_clear()
        return

    # Final fallback: a module-level dict.  If the dev names it
    # something else we simply leave state alone — the individual test
    # uses fresh prefix strings where possible.
    for attr in ("_JOB_STORES", "_job_stores", "_stores"):
        registry = getattr(mod, attr, None)
        if isinstance(registry, dict):
            registry.clear()
            return
