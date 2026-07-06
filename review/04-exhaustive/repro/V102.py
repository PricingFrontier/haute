"""Reproduction for V102.

Claim: ``hatch_build.py:_is_stale`` only scans ``frontend/src/`` (rglob of
*.ts/*.tsx/*.css/*.html) plus a fixed config list. It ignores Vite's REAL
entry HTML at the frontend ROOT (``frontend/index.html``) and everything under
``frontend/public/``. Both are first-class Vite build inputs (the root
index.html is transformed into ``static/index.html``; public/ is copied
verbatim into outDir). Therefore, when a developer edits the page title / meta
/ favicon / script entry in ``frontend/index.html`` or a ``public/`` asset and
then builds a wheel, ``_is_stale`` wrongly returns False -> the stale build is
shipped (validation passes) and (with HAUTE_BUILD_FRONTEND=1) the rebuild is
skipped.

This reproduction is fully isolated: it loads ``hatch_build.py`` via importlib
with hatchling stubbed (same technique as tests/test_hatch_build.py), and
builds a synthetic frontend tree entirely inside a Python tempdir. It never
reads or writes rating/, src/, tests/, or any real project file.

We assert on the SPECIFIC wrong boolean VALUE returned by ``_is_stale`` for two
genuinely-changed Vite inputs, and include a control proving the function DOES
detect a changed ``src/*.tsx`` (so the gap is specific, not a broken harness).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path


def _load_hatch_build():
    """Load the real hatch_build.py with hatchling stubbed out."""
    for name in (
        "hatchling",
        "hatchling.builders",
        "hatchling.builders.hooks",
        "hatchling.builders.hooks.plugin",
    ):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package
    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = object
    sys.modules[interface.__name__] = interface

    # Real project file — read-only load of the module under test (not modified).
    path = Path(__file__).resolve().parents[3] / "hatch_build.py"
    spec = importlib.util.spec_from_file_location("_hatch_build_v102", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch(p: Path, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def main() -> None:
    module = _load_hatch_build()
    is_stale = module.FrontendBuildHook._is_stale

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        frontend = root / "frontend"
        src = frontend / "src"
        public = frontend / "public"
        static = root / "src" / "haute" / "static"

        # Baseline mtimes: everything built at t=1000, build output at t=2000
        # so a fresh tree is NOT stale.
        old = 1000.0
        build_time = 2000.0
        newer = 3000.0  # an edit that happens AFTER the build

        # --- Pre-existing synthetic frontend sources (all older than build) ---
        _touch(frontend / "index.html", old)          # Vite ENTRY (root)
        _touch(frontend / "vite.config.ts", old)
        _touch(frontend / "tsconfig.json", old)
        _touch(frontend / "tsconfig.app.json", old)
        _touch(frontend / "package.json", old)
        _touch(src / "main.tsx", old)
        _touch(src / "App.tsx", old)
        _touch(src / "index.css", old)
        _touch(public / "favicon.svg", old)
        _touch(public / "vite.svg", old)

        # --- Build output (what gets packaged) ---
        _touch(static / "index.html", build_time)
        index_html = static / "index.html"

        # Sanity: a fresh, untouched tree must NOT be stale.
        assert is_stale(frontend, index_html) is False, (
            "Harness baseline wrong: fresh tree reported stale"
        )

        # ============================================================
        # CONTROL: editing a tracked src/*.tsx AFTER the build -> stale.
        # ============================================================
        os.utime(src / "App.tsx", (newer, newer))
        control = is_stale(frontend, index_html)
        assert control is True, (
            f"Control failed: changed src/App.tsx not detected (got {control!r}); "
            "harness is broken, not demonstrating the bug."
        )
        # Reset App.tsx so it no longer dominates the next checks.
        os.utime(src / "App.tsx", (old, old))
        assert is_stale(frontend, index_html) is False

        # ============================================================
        # BUG 1: edit the REAL Vite entry frontend/index.html AFTER build.
        # The packaged static/index.html is now genuinely stale, yet
        # _is_stale ignores the root index.html -> returns False.
        # ============================================================
        os.utime(frontend / "index.html", (newer, newer))
        result_entry = is_stale(frontend, index_html)
        # Reset for the next independent check.
        os.utime(frontend / "index.html", (old, old))

        # ============================================================
        # BUG 2: edit a public/ asset (copied verbatim into outDir) AFTER
        # build. Output is stale, yet _is_stale never walks public/.
        # ============================================================
        os.utime(public / "favicon.svg", (newer, newer))
        result_public = is_stale(frontend, index_html)
        os.utime(public / "favicon.svg", (old, old))

        print(f"control (src/App.tsx edited)        -> is_stale = {control}")
        print(f"BUG1   (frontend/index.html edited) -> is_stale = {result_entry}")
        print(f"BUG2   (public/favicon.svg edited)  -> is_stale = {result_public}")

        # The bug: both SHOULD be True (output is stale) but are False.
        assert result_entry is True, (
            f"REPRODUCED: frontend/index.html (Vite entry transformed into "
            f"static/index.html) was edited after the build, so the packaged "
            f"static/index.html IS stale; _is_stale should return True but "
            f"returned {result_entry!r}. A wheel build silently ships the old "
            f"index.html (wrong <title>/meta/favicon/script entry)."
        )
        assert result_public is True, (
            f"REPRODUCED: frontend/public/favicon.svg (copied verbatim into the "
            f"output) was edited after the build; _is_stale should return True "
            f"but returned {result_public!r}. Stale public asset ships."
        )
        print("UNREACHED: if this prints, _is_stale already handles entry/public")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        # An AssertionError that fires on the result_entry/result_public checks
        # is the demonstrated bug (a wrong boolean). Print and exit non-zero.
        print("BUG DEMONSTRATED:")
        print(str(exc))
        sys.exit(1)
