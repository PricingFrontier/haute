"""Deterministic revisions for persisted pipeline documents."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from haute._cache import canonical_json
from haute._hashing import content_hash, content_hash_bytes
from haute._submodel_paths import resolve_submodel_reference
from haute._types import PipelineGraph

_REVISION_FORMAT = 1
_RECOVERY_REVISION_FORMAT = 1


def _project_relative(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _normalise_graph_value(value: Any, project_root: Path) -> Any:
    """Remove self-referential metadata and canonicalise source paths."""
    if isinstance(value, dict):
        normalised: dict[str, Any] = {}
        for key, item in value.items():
            if key == "source_revision":
                continue
            if key == "source_file" and isinstance(item, str) and item:
                path = Path(item)
                if not path.is_absolute():
                    path = project_root / path
                normalised[key] = _project_relative(path, project_root)
            else:
                normalised[key] = _normalise_graph_value(item, project_root)
        return normalised
    if isinstance(value, list):
        return [_normalise_graph_value(item, project_root) for item in value]
    return value


def _file_manifest_entry(
    *,
    role: str,
    path: Path,
    project_root: Path,
) -> dict[str, str]:
    entry = {
        "role": role,
        "path": _project_relative(path, project_root),
    }
    if path.is_file():
        entry["state"] = "present"
        entry["content_hash"] = content_hash(path)
    else:
        entry["state"] = "missing"
    return entry


def pipeline_document_revision(
    graph: PipelineGraph,
    *,
    pipeline_path: Path,
    project_root: Path,
) -> str:
    """Hash one canonical graph and every source/sidecar it owns or references."""
    root = project_root.resolve()
    parent = pipeline_path.resolve()
    manifest = [
        _file_manifest_entry(role="parent_source", path=parent, project_root=root),
        _file_manifest_entry(
            role="parent_sidecar",
            path=parent.with_suffix(".haute.json"),
            project_root=root,
        ),
    ]

    seen_children: set[str] = set()
    child_entries: list[dict[str, str]] = []
    for name, metadata in sorted((graph.submodels or {}).items()):
        recorded_path = metadata.file
        if not recorded_path:
            raise ValueError(f"Submodel {name!r} has no valid source file path.")
        child_path, _config_base = resolve_submodel_reference(
            recorded_path,
            pipeline_dir=parent.parent,
            project_root=root,
        )
        child_key = str(child_path.resolve()).casefold()
        if child_key in seen_children:
            continue
        seen_children.add(child_key)
        child_entries.extend(
            [
                _file_manifest_entry(
                    role="child_source",
                    path=child_path,
                    project_root=root,
                ),
                _file_manifest_entry(
                    role="child_sidecar",
                    path=child_path.with_suffix(".haute.json"),
                    project_root=root,
                ),
            ]
        )

    manifest.extend(sorted(child_entries, key=lambda entry: (entry["path"], entry["role"])))
    payload = {
        "format": _REVISION_FORMAT,
        "graph": _normalise_graph_value(graph.model_dump(mode="json"), root),
        "files": manifest,
    }
    return content_hash_bytes(canonical_json(payload).encode("utf-8"))


def pipeline_recovery_revision(
    *,
    project_root: Path,
    artifacts: Iterable[tuple[str, Path]],
    known_bytes: Mapping[Path, bytes] | None = None,
) -> str:
    """Hash raw, contained artifacts without requiring a canonical graph.

    Repeated references to the same resolved path in the same role collapse to
    one manifest entry. Missing and unreadable artifacts remain explicit states
    so either transition changes the revision. *known_bytes* supplies the exact
    bytes a caller already read for an artifact; hashing those instead of
    re-reading keeps the revision authenticating the same content the caller
    presents even when the file changes concurrently.
    """
    root = project_root.resolve()
    captured = {
        str(path.resolve()).casefold(): raw for path, raw in (known_bytes or {}).items()
    }
    unique: dict[tuple[str, str], tuple[str, Path]] = {}
    for role, raw_path in artifacts:
        if not role or not role.strip():
            raise ValueError("Recovery artifact roles must be non-empty.")
        path = raw_path.resolve()
        if not path.is_relative_to(root):
            raise ValueError(
                f"Recovery artifact escapes the project root: {_project_relative(path, root)}"
            )
        key = (role, str(path).casefold())
        unique[key] = (role, path)

    manifest: list[dict[str, str]] = []
    ordered = sorted(
        unique.values(),
        key=lambda item: (_project_relative(item[1], root), item[0]),
    )
    for role, path in ordered:
        entry = {"role": role, "path": _project_relative(path, root)}
        try:
            captured_raw = captured.get(str(path).casefold())
            raw = captured_raw if captured_raw is not None else path.read_bytes()
        except FileNotFoundError:
            entry["state"] = "missing"
        except OSError as exc:
            entry["state"] = "unreadable"
            entry["error_type"] = type(exc).__name__
        else:
            entry["state"] = "present"
            entry["content_hash"] = content_hash_bytes(raw)
        manifest.append(entry)

    payload = {
        "format": _RECOVERY_REVISION_FORMAT,
        "files": manifest,
    }
    return content_hash_bytes(canonical_json(payload).encode("utf-8"))
