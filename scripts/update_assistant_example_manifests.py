"""Check or refresh content digests in assistant example-bundle manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = PROJECT_ROOT / "src" / "haute" / "assistant" / "assets" / "examples"


def _safe_resource_path(raw: object) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"Invalid manifest resource path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe manifest resource path: {raw!r}")
    return path


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("resources"), list):
        raise ValueError(f"Invalid assistant bundle manifest: {path}")
    return manifest


def _actual_bundle_paths(bundle: Path) -> set[str]:
    return {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }


def _refresh_manifest(path: Path, *, write: bool) -> bool:
    bundle = path.parent
    manifest = _load_manifest(path)
    declared: set[str] = set()
    changed = False
    for item in manifest["resources"]:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid resource entry in {path}")
        relative = _safe_resource_path(item.get("path"))
        relative_text = relative.as_posix()
        if relative_text in declared:
            raise ValueError(f"Duplicate resource path {relative_text!r} in {path}")
        declared.add(relative_text)
        resource = bundle.joinpath(*relative.parts)
        if not resource.is_file():
            raise ValueError(f"Missing declared resource {resource}")
        digest = hashlib.sha256(resource.read_bytes()).hexdigest()
        if item.get("sha256") != digest:
            changed = True
            item["sha256"] = digest
    undeclared = sorted(_actual_bundle_paths(bundle) - declared)
    if undeclared:
        raise ValueError(f"Undeclared resources in {bundle.name}: {', '.join(undeclared)}")
    if changed and write:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite mismatched SHA-256 fields; the default only checks",
    )
    args = parser.parse_args()
    manifests = sorted(EXAMPLES_ROOT.glob("*/manifest.json"))
    if not manifests:
        raise SystemExit(f"No assistant bundle manifests found under {EXAMPLES_ROOT}")
    changed = [path for path in manifests if _refresh_manifest(path, write=args.write)]
    if changed and not args.write:
        for path in changed:
            print(f"stale: {path.relative_to(PROJECT_ROOT)}")
        return 1
    for path in changed:
        print(f"updated: {path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
