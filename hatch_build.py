"""Hatchling custom build hook — builds the frontend before packaging.

This runs automatically during ``hatch build``, ``uv build``, or
``pip install .`` so that ``src/haute/static/`` always contains the
latest compiled frontend assets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "frontend-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        frontend_dir = Path(self.root) / "frontend"
        if not frontend_dir.exists():
            # Source dist or CI without frontend — skip
            return

        static_dir = Path(self.root) / "src" / "haute" / "static"
        index_html = static_dir / "index.html"

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
