"""Hatchling custom build hook for packaged frontend assets.

By default the hook packages already-built files in ``src/haute/static/``
and verifies that their output graph and recorded production inputs match the
checkout. Release or preflight builds that need to refresh those assets can
opt in with ``HAUTE_BUILD_FRONTEND=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_BUILD_FRONTEND_ENV = "HAUTE_BUILD_FRONTEND"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
_COMMAND_TIMEOUT_SECONDS = 900
_INPUT_MANIFEST_NAME = "haute-build-inputs.json"
_OUTPUT_MANIFEST_NAME = "manifest.json"
_INPUT_MANIFEST_VERSION = 1
_SOURCE_TEST_ONLY_DIRS = frozenset({"__tests__", "test-utils", "testSupport"})
_SOURCE_TEST_FILE = re.compile(r"[.](?:test|spec)[.][^.]+$")


class _LocalAssetParser(HTMLParser):
    """Collect file-bearing attributes from the generated entry page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        file_attribute = {"script": "src", "link": "href"}.get(tag.casefold())
        if file_attribute is None:
            return
        for name, value in attrs:
            if name.casefold() == file_attribute and value:
                self.references.append(value)


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "frontend-build"

    def initialize(self, version: str, build_data: dict) -> None:  # noqa: ARG002
        build_frontend = self._should_build_frontend()
        # Editable installs happen during dependency sync; wheel builds still
        # validate packaged frontend assets below.
        if version == "editable":
            return

        frontend_dir = Path(self.root) / "frontend"
        if not frontend_dir.exists():
            # Source distributions can omit frontend source.
            return

        static_dir = Path(self.root) / "src" / "haute" / "static"
        index_html = static_dir / "index.html"

        if not build_frontend:
            self._validate_static_assets(frontend_dir, index_html)
            return

        # An explicit build uses the lockfile exactly even when node_modules
        # happens to be present.
        self._run([self._npm(), "ci", "--prefer-offline"], cwd=frontend_dir)

        # Vite may be skipped only when both proofs are complete and current.
        if self._static_assets_ready(index_html) and not self._is_stale(frontend_dir, index_html):
            return

        inputs_before_build = self._current_input_manifest(frontend_dir)
        self._run([self._npm(), "run", "build"], cwd=frontend_dir)
        if self._current_input_manifest(frontend_dir) != inputs_before_build:
            raise RuntimeError(
                "Frontend production inputs changed while the Vite build was "
                "running; discard the output and rerun the package build."
            )

        # Vite replaces the output directory, so record inputs afterwards.
        self._write_input_manifest(
            frontend_dir,
            index_html,
            manifest=inputs_before_build,
        )
        self._require_static_readiness(index_html)
        self._require_current_input_manifest(frontend_dir, index_html)

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
        """Fail clearly rather than packaging an incomplete or stale client."""
        cls._require_static_readiness(index_html)
        cls._require_current_input_manifest(frontend_dir, index_html)

    @classmethod
    def _static_assets_ready(cls, index_html: Path) -> bool:
        """Return whether the generated output dependency graph is coherent."""
        try:
            cls._require_static_readiness(index_html)
        except RuntimeError:
            return False
        return True

    @classmethod
    def _require_static_readiness(cls, index_html: Path) -> None:
        """Require the entry page and every local Vite dependency to exist."""
        static_dir = index_html.parent
        if not index_html.is_file():
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    "index.html is missing or is not a regular file",
                )
            )

        output_manifest = static_dir / _OUTPUT_MANIFEST_NAME
        manifest = cls._read_json_object(output_manifest, "Vite output manifest")
        if not manifest:
            raise RuntimeError(cls._readiness_message(static_dir, "Vite manifest is empty"))

        declared_assets: set[str] = set()
        entry_assets: set[str] = set()
        for key, raw_entry in manifest.items():
            if not isinstance(key, str) or not key or not isinstance(raw_entry, dict):
                raise RuntimeError(
                    cls._readiness_message(
                        static_dir,
                        "Vite manifest keys must map to objects",
                    )
                )
            file_name = cls._manifest_string(raw_entry, "file", key, static_dir)
            normalised_file = cls._require_local_asset(
                static_dir,
                file_name,
                f"manifest entry {key!r}",
            )
            declared_assets.add(normalised_file)
            if raw_entry.get("isEntry") is True:
                entry_assets.add(normalised_file)
            elif "isEntry" in raw_entry and raw_entry["isEntry"] is not False:
                raise RuntimeError(
                    cls._readiness_message(
                        static_dir,
                        f"manifest entry {key!r} has non-boolean isEntry",
                    )
                )

            for field in ("css", "assets"):
                for asset in cls._manifest_string_list(raw_entry, field, key, static_dir):
                    declared_assets.add(
                        cls._require_local_asset(
                            static_dir,
                            asset,
                            f"manifest entry {key!r} field {field!r}",
                        )
                    )
            for field in ("imports", "dynamicImports"):
                for imported_key in cls._manifest_string_list(raw_entry, field, key, static_dir):
                    if imported_key not in manifest:
                        raise RuntimeError(
                            cls._readiness_message(
                                static_dir,
                                f"manifest entry {key!r} names missing {field} key "
                                f"{imported_key!r}",
                            )
                        )

        if not entry_assets:
            raise RuntimeError(
                cls._readiness_message(static_dir, "Vite manifest has no entry chunk")
            )

        try:
            index_text = index_html.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(
                cls._readiness_message(static_dir, f"index.html is unreadable: {exc}")
            ) from exc

        parser = _LocalAssetParser()
        try:
            parser.feed(index_text)
            parser.close()
        except Exception as exc:
            raise RuntimeError(
                cls._readiness_message(static_dir, f"index.html is malformed: {exc}")
            ) from exc

        direct_assets: set[str] = set()
        for reference in parser.references:
            local = cls._local_reference_path(reference)
            if local is None:
                continue
            resolved = cls._require_local_asset(
                static_dir,
                local,
                "index.html reference",
            )
            direct_assets.add(resolved)
            if (
                Path(resolved).suffix.casefold() in {".js", ".css"}
                and resolved not in declared_assets
            ):
                raise RuntimeError(
                    cls._readiness_message(
                        static_dir,
                        f"index.html reference {reference!r} is absent from the Vite manifest",
                    )
                )

        if not entry_assets.intersection(direct_assets):
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    "index.html does not reference the Vite manifest entry chunk",
                )
            )

    @classmethod
    def _is_stale(cls, frontend_dir: Path, index_html: Path) -> bool:
        """Return whether the deterministic input proof is absent or differs."""
        try:
            cls._require_current_input_manifest(frontend_dir, index_html)
        except RuntimeError:
            return True
        return False

    @classmethod
    def _write_input_manifest(
        cls,
        frontend_dir: Path,
        index_html: Path,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        """Atomically record the canonical current production-input inventory."""
        manifest = cls._current_input_manifest(frontend_dir) if manifest is None else manifest
        destination = index_html.parent / _INPUT_MANIFEST_NAME
        temporary = destination.with_name(f"{destination.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(
                f"Unable to write frontend input manifest at {destination}: {exc}"
            ) from exc

    @classmethod
    def _require_current_input_manifest(
        cls,
        frontend_dir: Path,
        index_html: Path,
    ) -> None:
        """Require the recorded production-input proof to equal the checkout."""
        destination = index_html.parent / _INPUT_MANIFEST_NAME
        recorded = cls._read_json_object(destination, "frontend input manifest")
        cls._validate_input_manifest_shape(recorded, destination)
        current = cls._current_input_manifest(frontend_dir)
        if recorded != current:
            raise RuntimeError(
                "Built frontend input fingerprint is stale or mismatched at "
                f"{destination}. Set {_BUILD_FRONTEND_ENV}=1 so the package build "
                "rebuilds and records the current production inputs."
            )

    @classmethod
    def _current_input_manifest(cls, frontend_dir: Path) -> dict[str, Any]:
        root = frontend_dir.parent.resolve()
        inputs: list[dict[str, str | int]] = []
        for path in cls._production_inputs(frontend_dir):
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise RuntimeError(
                    f"Unable to read frontend production input {path}: {exc}"
                ) from exc
            inputs.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        encoded = json.dumps(
            inputs,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return {
            "version": _INPUT_MANIFEST_VERSION,
            "algorithm": "sha256",
            "digest": hashlib.sha256(encoded).hexdigest(),
            "inputs": inputs,
        }

    @classmethod
    def _production_inputs(cls, frontend_dir: Path) -> tuple[Path, ...]:
        """Return the authoritative, closed production-input inventory."""
        frontend = frontend_dir.resolve()
        root = frontend.parent
        required = {
            root / "pyproject.toml",
            frontend / ".npmrc",
            frontend / "index.html",
            frontend / "package.json",
            frontend / "package-lock.json",
            frontend / "vite.config.ts",
        }
        required.update(cls._referenced_tsconfigs(frontend))

        public_dir = frontend / "public"
        source_dir = frontend / "src"
        for directory in (public_dir, source_dir):
            if not directory.is_dir():
                raise RuntimeError(f"Frontend production input directory is missing: {directory}")

        required.update(path for path in public_dir.rglob("*") if path.is_file())
        required.update(
            path
            for path in source_dir.rglob("*")
            if path.is_file() and cls._is_production_source(path, source_dir)
        )

        resolved: set[Path] = set()
        for path in required:
            if not path.is_file():
                raise RuntimeError(f"Frontend production input is missing or not a file: {path}")
            candidate = path.resolve()
            if not candidate.is_relative_to(root):
                raise RuntimeError(f"Frontend production input escapes the project root: {path}")
            resolved.add(candidate)
        return tuple(
            sorted(
                resolved,
                key=lambda item: item.relative_to(root).as_posix(),
            )
        )

    @staticmethod
    def _is_production_source(path: Path, source_root: Path) -> bool:
        relative = path.relative_to(source_root)
        if any(part in _SOURCE_TEST_ONLY_DIRS for part in relative.parts):
            return False
        if path.name == "setupTests.ts":
            return False
        return _SOURCE_TEST_FILE.search(path.name) is None

    @classmethod
    def _referenced_tsconfigs(cls, frontend_dir: Path) -> set[Path]:
        pending = [frontend_dir / "tsconfig.json"]
        discovered: set[Path] = set()
        while pending:
            config = pending.pop().resolve()
            if config in discovered:
                continue
            if not config.is_relative_to(frontend_dir):
                raise RuntimeError(f"Referenced TypeScript config escapes frontend/: {config}")
            if not config.is_file():
                raise RuntimeError(f"Referenced TypeScript config is missing: {config}")
            discovered.add(config)
            try:
                raw: Any = json.loads(cls._strip_json_comments(config.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Referenced TypeScript config is unreadable: {config}: {exc}"
                ) from exc
            if not isinstance(raw, dict):
                raise RuntimeError(f"Referenced TypeScript config must contain an object: {config}")
            references = raw.get("references", [])
            if not isinstance(references, list):
                raise RuntimeError(f"TypeScript config references must be an array: {config}")
            for reference in references:
                if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
                    raise RuntimeError(f"Malformed TypeScript project reference in {config}")
                candidate = (config.parent / reference["path"]).resolve()
                if candidate.is_dir():
                    candidate /= "tsconfig.json"
                elif candidate.suffix.casefold() != ".json":
                    candidate = candidate.with_suffix(".json")
                pending.append(candidate)
        return discovered

    @staticmethod
    def _strip_json_comments(text: str) -> str:
        """Remove JSONC comments without changing string literals."""
        output: list[str] = []
        index = 0
        in_string = False
        escaped = False
        while index < len(text):
            char = text[index]
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                output.append(char)
                index += 1
                continue
            if char == "/" and next_char == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if char == "/" and next_char == "*":
                index += 2
                while index + 1 < len(text) and text[index : index + 2] != "*/":
                    if text[index] in "\r\n":
                        output.append(text[index])
                    index += 1
                if index + 1 >= len(text):
                    raise RuntimeError("Unterminated block comment in TypeScript config")
                index += 2
                continue
            output.append(char)
            index += 1
        return "".join(output)

    @staticmethod
    def _read_json_object(path: Path, label: str) -> dict[str, Any]:
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError(f"{label} is missing at {path}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{label} is malformed or unreadable at {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"{label} must contain a JSON object at {path}")
        return raw

    @staticmethod
    def _validate_input_manifest_shape(
        manifest: dict[str, Any],
        path: Path,
    ) -> None:
        if set(manifest) != {"version", "algorithm", "digest", "inputs"}:
            raise RuntimeError(f"Frontend input manifest has an invalid schema at {path}")
        if (
            type(manifest["version"]) is not int
            or manifest["version"] != _INPUT_MANIFEST_VERSION
            or manifest["algorithm"] != "sha256"
            or not isinstance(manifest["digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest["digest"]) is None
            or not isinstance(manifest["inputs"], list)
        ):
            raise RuntimeError(f"Frontend input manifest has invalid fields at {path}")
        for item in manifest["inputs"]:
            if not isinstance(item, dict) or set(item) != {
                "path",
                "size",
                "sha256",
            }:
                raise RuntimeError(f"Frontend input manifest has an invalid input row at {path}")
            if (
                not isinstance(item["path"], str)
                or not item["path"]
                or type(item["size"]) is not int
                or item["size"] < 0
                or not isinstance(item["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
            ):
                raise RuntimeError(f"Frontend input manifest has invalid input values at {path}")

    @classmethod
    def _manifest_string(
        cls,
        entry: dict[str, Any],
        field: str,
        key: str,
        static_dir: Path,
    ) -> str:
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    f"manifest entry {key!r} has invalid {field!r}",
                )
            )
        return value

    @classmethod
    def _manifest_string_list(
        cls,
        entry: dict[str, Any],
        field: str,
        key: str,
        static_dir: Path,
    ) -> list[str]:
        value = entry.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    f"manifest entry {key!r} has invalid {field!r}",
                )
            )
        return value

    @staticmethod
    def _local_reference_path(reference: str) -> str | None:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        return unquote(parsed.path).replace("\\", "/").lstrip("/")

    @classmethod
    def _require_local_asset(
        cls,
        static_dir: Path,
        reference: str,
        context: str,
    ) -> str:
        normalised = unquote(reference).replace("\\", "/").lstrip("/")
        if not normalised or re.match(r"^[A-Za-z]:", normalised):
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    f"{context} has invalid path {reference!r}",
                )
            )
        root = static_dir.resolve()
        candidate = (root / normalised).resolve()
        if not candidate.is_relative_to(root):
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    f"{context} escapes the static root: {reference!r}",
                )
            )
        if not candidate.is_file():
            raise RuntimeError(
                cls._readiness_message(
                    static_dir,
                    f"{context} names a missing or non-file asset: {reference!r}",
                )
            )
        return candidate.relative_to(root).as_posix()

    @staticmethod
    def _readiness_message(static_dir: Path, detail: str) -> str:
        return (
            f"Built frontend assets are missing or incomplete at {static_dir}: "
            f"{detail}. Set {_BUILD_FRONTEND_ENV}=1 for an explicit release build."
        )

    @staticmethod
    def _npm() -> str:
        """Return npm, resolving the common Windows installation path."""
        found = shutil.which("npm")
        if found:
            return found
        if sys.platform == "win32":
            candidate = Path(r"C:\Program Files\nodejs\npm.cmd")
            if candidate.exists():
                return str(candidate)
        raise RuntimeError("npm not found on PATH. Install Node.js from https://nodejs.org")

    @staticmethod
    def _node_env() -> dict[str, str] | None:
        """Return an environment with Node.js on PATH when necessary."""
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
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_COMMAND_TIMEOUT_SECONDS,
                env=self._node_env(),
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"Command timed out after {_COMMAND_TIMEOUT_SECONDS} seconds: {' '.join(cmd)}"
            raise RuntimeError(msg) from exc
        if result.returncode != 0:
            print(result.stdout, file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"Command failed: {' '.join(cmd)}")
