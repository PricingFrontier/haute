"""Hatchling custom build hook for packaged frontend assets.

By default the hook packages already-built files in ``src/haute/static/``
and verifies they are present and current. Release or preflight builds
that need to refresh those assets can opt in with ``HAUTE_BUILD_FRONTEND=1``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_BUILD_FRONTEND_ENV = "HAUTE_BUILD_FRONTEND"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "frontend-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        # Editable installs happen during dependency sync; wheel builds still
        # validate packaged frontend assets below.
        if version == "editable":
            return

        frontend_dir = Path(self.root) / "frontend"
        if not frontend_dir.exists():
            # Source dist or CI without frontend — skip
            return

        static_dir = Path(self.root) / "src" / "haute" / "static"
        index_html = static_dir / "index.html"

        if not self._should_build_frontend():
            self._validate_static_assets(frontend_dir, index_html)
            return

        # Install deps if node_modules is missing
        node_modules = frontend_dir / "node_modules"
        if not node_modules.exists():
            self._run([self._npm(), "ci", "--prefer-offline"], cwd=frontend_dir)

        # Skip rebuild if static assets are newer than all frontend sources
        if index_html.exists() and not self._is_stale(frontend_dir, index_html):
            return

        # Build frontend → src/haute/static/
        self._run([self._npm(), "run", "build"], cwd=frontend_dir)

        # Sanity check
        if not index_html.exists():
            msg = f"Frontend build did not produce {index_html}"
            raise RuntimeError(msg)

    @staticmethod
    def _should_build_frontend() -> bool:
        """Return True when the caller explicitly opts into a frontend build."""
        raw = os.environ.get(_BUILD_FRONTEND_ENV, "").strip().lower()
        if raw in _TRUE_VALUES:
            return True
        if raw in _FALSE_VALUES:
            return False
        msg = (
            f"{_BUILD_FRONTEND_ENV} must be one of "
            f"{sorted(_TRUE_VALUES | _FALSE_VALUES)!r}; got {raw!r}"
        )
        raise RuntimeError(msg)

    @classmethod
    def _validate_static_assets(cls, frontend_dir: Path, index_html: Path) -> None:
        """Fail clearly when a wheel build would package missing or stale frontend."""
        if not index_html.exists():
            msg = (
                f"Built frontend assets are missing at {index_html}. "
                f"Run 'cd frontend && npm ci && npm run build', or set "
                f"{_BUILD_FRONTEND_ENV}=1 for an explicit release build."
            )
            raise RuntimeError(msg)
        if cls._is_stale(frontend_dir, index_html):
            msg = (
                f"Built frontend assets at {index_html.parent} are older than "
                f"the frontend source. Run 'cd frontend && npm run build', or "
                f"set {_BUILD_FRONTEND_ENV}=1 so the package build refreshes them."
            )
            raise RuntimeError(msg)

    @staticmethod
    def _is_stale(frontend_dir: Path, index_html: Path) -> bool:
        """Return True if any frontend source file is newer than index.html."""
        build_mtime = index_html.stat().st_mtime
        src_dir = frontend_dir / "src"
        for ext in ("*.ts", "*.tsx", "*.css", "*.html"):
            for f in src_dir.rglob(ext):
                if f.stat().st_mtime > build_mtime:
                    return True
        # Also check vite/ts config changes
        for cfg in ("vite.config.ts", "tsconfig.json", "tsconfig.app.json", "package.json"):
            cfg_path = frontend_dir / cfg
            if cfg_path.exists() and cfg_path.stat().st_mtime > build_mtime:
                return True
        return False

    @staticmethod
    def _npm() -> str:
        """Return the npm executable, resolving common Windows install paths.

        Duplicates logic from ``haute.cli._helpers._npm`` because this build
        hook runs outside the installed package (hatchling context).
        """
        found = shutil.which("npm")
        if found:
            return found
        if sys.platform == "win32":
            candidate = Path(r"C:\Program Files\nodejs\npm.cmd")
            if candidate.exists():
                return str(candidate)
        msg = "npm not found on PATH. Install Node.js from https://nodejs.org"
        raise RuntimeError(msg)

    @staticmethod
    def _node_env() -> dict[str, str] | None:
        """Return env with Node.js on PATH, or *None* if already available."""
        if shutil.which("node"):
            return None
        if sys.platform == "win32":
            nodejs_dir = Path(r"C:\Program Files\nodejs")
            if (nodejs_dir / "node.exe").exists():
                env = os.environ.copy()
                env["PATH"] = f"{nodejs_dir};{env.get('PATH', '')}"
                return env
        return None

    def _run(self, cmd: list[str], cwd: Path) -> None:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=self._node_env(),
        )
        if result.returncode != 0:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            msg = f"Command failed: {' '.join(cmd)}"
            raise RuntimeError(msg)
