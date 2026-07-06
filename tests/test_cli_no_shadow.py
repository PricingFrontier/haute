"""Guard: the ``haute.cli`` package must never be shadowed by a ``cli.py`` module.

``src/haute/cli.py`` was the original monolithic CLI. It was split into the
``src/haute/cli/`` package and removed from git in ``1c02d2cc``. Because Python's
import system selects a package over a same-named module, a ``cli.py`` re-added
beside the package would still be *unreachable* — ``import haute.cli`` keeps
resolving to ``cli/__init__.py``. That makes the shadow module invisible at
runtime yet a live trap for grep-based audits and tooling, and a place for stale
logic to rot (for example the pre-``shutil.which`` ``subprocess.Popen(["npm",
...])`` call that ``haute.cli._helpers._npm`` replaced for Windows correctness).

These tests fail loudly if the shadow file reappears, and pin ``haute.cli`` as a
package so the canonical CLI location cannot silently drift.
"""

from __future__ import annotations

from pathlib import Path

# src/haute/ — parent of both the cli/ package and any stray cli.py shadow.
_HAUTE_DIR = Path(__file__).resolve().parent.parent / "src" / "haute"


def test_cli_package_not_shadowed_by_module() -> None:
    """No ``src/haute/cli.py`` may sit beside the ``src/haute/cli/`` package."""
    package_init = _HAUTE_DIR / "cli" / "__init__.py"
    shadow = _HAUTE_DIR / "cli.py"

    assert package_init.is_file(), f"CLI package missing: expected {package_init}"
    assert not shadow.exists(), (
        f"{shadow} shadows the haute.cli package and is unreachable dead code. "
        "The CLI lives in the cli/ package — delete the stray module."
    )


def test_haute_cli_imports_as_package() -> None:
    """``import haute.cli`` must resolve to the package, not a module."""
    import haute.cli

    # Packages expose ``__path__``; a plain cli.py module would not.
    assert hasattr(haute.cli, "__path__"), (
        "haute.cli must be a package (cli/__init__.py), not a cli.py module"
    )
    assert haute.cli.__file__ is not None
    assert Path(haute.cli.__file__).name == "__init__.py", (
        f"haute.cli resolved to {haute.cli.__file__!r}, expected a package __init__.py"
    )
