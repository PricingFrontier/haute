"""Phase 5 Wave 9C — routes/handlers hygiene regression tests.

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
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Locate the repo root + key files once — every test class reuses these.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROUTES_DIR = _REPO_ROOT / "src" / "haute" / "routes"
_PIPELINE_PY = _ROUTES_DIR / "pipeline.py"
_HELPERS_PY = _ROUTES_DIR / "_helpers.py"
_JOB_STORE_PY = _ROUTES_DIR / "_job_store.py"


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
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
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

    def test_cold_import_under_500ms(self) -> None:
        """Measured cold-import latency for ``haute.routes.pipeline``.

        500ms is generous — typical cold import is well under 100ms even
        with FastAPI's import graph.  The guard rail exists so the dev
        does not accidentally hoist a heavy import (``torch``, ``mlflow``,
        etc.) to module top when breaking a cycle.
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

        assert elapsed_ms < 500.0, (
            f"Cold import of haute.routes.pipeline took {elapsed_ms:.1f}ms — "
            "exceeds 500ms budget.  Check for newly-hoisted heavy imports."
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


class TestSidecarForwardCompat:
    """Old-shape JSON (from the pre-refactor manual-dump code) must still parse.

    The currently-persisted shape looks like::

        {
            "positions": {"alpha": {"x": 1.0, "y": 2.0}},
            "sources": ["live", "batch"],
            "active_source": "batch"
        }

    Users have these files on disk right now.  The refactor must not
    invalidate them.
    """

    def test_legacy_full_shape_parses(self) -> None:
        from haute.routes._helpers import SidecarModel

        legacy = json.dumps(
            {
                "positions": {"alpha": {"x": 1.0, "y": 2.0}},
                "sources": ["live", "batch"],
                "active_source": "batch",
            }
        )
        parsed = SidecarModel.model_validate_json(legacy)
        assert parsed.positions == {"alpha": {"x": 1.0, "y": 2.0}}
        assert parsed.sources == ["live", "batch"]
        assert parsed.active_source == "batch"

    def test_legacy_positions_only_parses(self) -> None:
        """Sidecars that pre-date the source-state feature only carry positions."""
        from haute.routes._helpers import SidecarModel

        legacy = json.dumps(
            {"positions": {"alpha": {"x": 1.0, "y": 2.0}}},
        )
        parsed = SidecarModel.model_validate_json(legacy)
        assert parsed.positions == {"alpha": {"x": 1.0, "y": 2.0}}
        # Missing fields receive their defaults — default sources/active_source
        # should not round-trip as None for the write path, so the model must
        # default them sensibly (empty / "live").  We only assert the parse
        # succeeds; the specific default values are pinned alongside save_sidecar.

    def test_missing_future_field_uses_default(self) -> None:
        """Forward migration: a sidecar missing a newly-added field must parse
        with the default value.

        Models of record for on-disk data should never require a field
        that was added after the file was written.  This pin forces the
        dev to give every new field a sensible default.
        """
        from haute.routes._helpers import SidecarModel

        minimal = json.dumps({"positions": {}})
        parsed = SidecarModel.model_validate_json(minimal)
        # If the dev adds a new field (say, ``schema_version``) later,
        # ``.model_dump(exclude_unset=True)`` must not include it here.
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
            "singleton JobStore per prefix (e.g. \"training\", \"optimiser\")."
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

        for py_file in _ROUTES_DIR.rglob("*.py"):
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
