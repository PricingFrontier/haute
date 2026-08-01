"""Source-linked project knowledge with a disposable metadata-only index."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from haute.assistant._config import EgressPolicy, Sensitivity
from haute.errors import HauteError
from haute.routes._helpers import parse_pipeline_to_graph

EvidenceClass = Literal["project_fact", "untrusted_document"]

EXTRACTION_VERSION = "1"
_DOC_EXTENSIONS = frozenset({".md", ".txt"})
_MAX_DOCUMENT_BYTES = 64 * 1024
_MAX_DOCUMENTS = 50
_MAX_QUERY_ITEMS = 10
_MAX_ITEM_CONTENT_CHARS = 12_000
_MAX_QUERY_CONTENT_CHARS = 48_000
_SENSITIVITY_ORDER: dict[Sensitivity, int] = {
    "public": 0,
    "internal": 1,
    "restricted": 2,
}
_SENSITIVITY_LINE = re.compile(
    r"^\s*sensitivity\s*:\s*(public|internal|restricted)\s*$",
    re.IGNORECASE,
)


class ProjectKnowledgeError(HauteError):
    """Project knowledge could not be extracted without losing source evidence."""


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    id: str
    source: str
    source_digest: str
    extraction_version: str
    sensitivity: Sensitivity
    evidence_class: EvidenceClass
    content: str


@dataclass(frozen=True, slots=True)
class ProjectKnowledgeView:
    items: tuple[KnowledgeItem, ...]
    source_manifest: MappingProxyType[str, str]
    excluded_by_policy: tuple[str, ...]
    cache_hit: bool


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _source_item(
    *,
    source: str,
    data: bytes,
    sensitivity: Sensitivity,
    evidence_class: EvidenceClass,
    content: str,
) -> KnowledgeItem:
    digest = _digest(data)
    return KnowledgeItem(
        id=_digest(f"{EXTRACTION_VERSION}:{source}:{digest}".encode()),
        source=source,
        source_digest=digest,
        extraction_version=EXTRACTION_VERSION,
        sensitivity=sensitivity,
        evidence_class=evidence_class,
        content=content,
    )


def _document_sensitivity(text: str) -> Sensitivity:
    for line in text.splitlines()[:10]:
        match = _SENSITIVITY_LINE.fullmatch(line)
        if match is not None:
            return match.group(1).casefold()  # type: ignore[return-value]
    return "restricted"


def _within_policy(item: KnowledgeItem, policy: EgressPolicy) -> bool:
    if not policy.allow_project_knowledge:
        return False
    return _SENSITIVITY_ORDER[item.sensitivity] <= _SENSITIVITY_ORDER[policy.max_sensitivity]


def _graph_fact(project_root: Path, source_file: str) -> KnowledgeItem:
    source = (project_root / source_file).resolve()
    if not source.is_relative_to(project_root):
        raise ValueError("pipeline source must resolve inside the project root")
    data = source.read_bytes()
    graph = parse_pipeline_to_graph(source)
    node_types: dict[str, int] = {}
    for node in graph.nodes:
        node_type = getattr(node.data.nodeType, "value", str(node.data.nodeType))
        node_types[node_type] = node_types.get(node_type, 0) + 1
    content = json.dumps(
        {
            "pipeline": graph.pipeline_name,
            "description": graph.pipeline_description,
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "node_types": node_types,
            "config_keys": {node.id: sorted(node.data.config) for node in graph.nodes},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _source_item(
        source=source.relative_to(project_root).as_posix(),
        data=data,
        sensitivity="internal",
        evidence_class="project_fact",
        content=content,
    )


def _toml_fact(project_root: Path) -> KnowledgeItem | None:
    path = project_root / "haute.toml"
    if not path.is_file():
        return None
    if path.is_symlink() or not path.resolve().is_relative_to(project_root):
        raise ValueError("haute.toml must be a regular file inside the project root")
    data = path.read_bytes()
    # The exact TOML is not provider material. This deterministic fact names
    # only the canonical project artifact and its digest.
    return _source_item(
        source="haute.toml",
        data=data,
        sensitivity="internal",
        evidence_class="project_fact",
        content=json.dumps(
            {"artifact": "haute.toml", "digest": _digest(data)},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _document_items(project_root: Path) -> list[KnowledgeItem]:
    docs = project_root / "docs"
    if not docs.is_dir():
        return []
    if docs.is_symlink() or not docs.resolve().is_relative_to(project_root):
        raise ValueError("project documentation must stay inside the project root")
    items: list[KnowledgeItem] = []
    candidates = sorted(
        (
            path
            for path in docs.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(project_root)
            and path.suffix.casefold() in _DOC_EXTENSIONS
            and not any(part.startswith(".") for part in path.relative_to(project_root).parts)
        ),
        key=lambda path: path.relative_to(project_root).as_posix(),
    )
    for path in candidates[:_MAX_DOCUMENTS]:
        data = path.read_bytes()
        if len(data) > _MAX_DOCUMENT_BYTES:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            source = path.relative_to(project_root).as_posix()
            raise ProjectKnowledgeError(
                f"Project documentation must be valid UTF-8: {source}."
            ) from exc
        items.append(
            _source_item(
                source=path.relative_to(project_root).as_posix(),
                data=data,
                sensitivity=_document_sensitivity(text),
                evidence_class="untrusted_document",
                content=text,
            )
        )
    return items


def _cache_payload(items: list[KnowledgeItem]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "extraction_version": EXTRACTION_VERSION,
        "sources": [
            {
                "source": item.source,
                "source_digest": item.source_digest,
                "sensitivity": item.sensitivity,
                "evidence_class": item.evidence_class,
                "item_id": item.id,
            }
            for item in items
        ],
    }


def _write_cache(project_root: Path, payload: dict[str, object]) -> bool:
    path = project_root / ".haute" / "assistant" / "knowledge" / "index-v1.json"
    if not path.resolve().is_relative_to(project_root):
        raise ValueError("assistant knowledge cache must stay inside the project root")
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    try:
        previous = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        previous = None
    hit = previous == rendered
    if not hit:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, path)
    return hit


def build_project_knowledge(
    project_root: Path,
    source_file: str,
    *,
    policy: EgressPolicy,
) -> ProjectKnowledgeView:
    """Build and policy-filter a bounded source-derived knowledge view."""

    root = project_root.resolve()
    all_items = [_graph_fact(root, source_file)]
    if (toml_item := _toml_fact(root)) is not None:
        all_items.append(toml_item)
    all_items.extend(_document_items(root))
    all_items.sort(key=lambda item: item.source)
    cache_hit = _write_cache(root, _cache_payload(all_items))
    included = tuple(item for item in all_items if _within_policy(item, policy))
    excluded = tuple(item.source for item in all_items if not _within_policy(item, policy))
    manifest = MappingProxyType({item.source: item.source_digest for item in included})
    return ProjectKnowledgeView(
        items=included,
        source_manifest=manifest,
        excluded_by_policy=excluded,
        cache_hit=cache_hit,
    )


def query_project_knowledge(
    view: ProjectKnowledgeView,
    query: str,
    *,
    limit: int = 5,
) -> tuple[dict[str, object], ...]:
    """Return a deterministic, bounded selection from an eligible view."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("project knowledge query must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_QUERY_ITEMS:
        raise ValueError(f"project knowledge limit must be between 1 and {_MAX_QUERY_ITEMS}")
    terms = tuple(
        dict.fromkeys(
            term for term in re.findall(r"[a-z0-9_]+", query.casefold()) if len(term) >= 2
        )
    )
    if not terms:
        raise ValueError("project knowledge query must contain a searchable term")

    ranked: list[tuple[int, KnowledgeItem]] = []
    for item in view.items:
        searchable = f"{item.source}\n{item.content}".casefold()
        score = sum(searchable.count(term) for term in terms)
        if score:
            ranked.append((score, item))
    ranked.sort(key=lambda ranked_item: (-ranked_item[0], ranked_item[1].source))

    remaining = _MAX_QUERY_CONTENT_CHARS
    selected: list[dict[str, object]] = []
    for score, item in ranked[:limit]:
        content_limit = min(_MAX_ITEM_CONTENT_CHARS, remaining)
        if content_limit <= 0:
            break
        content = item.content[:content_limit]
        remaining -= len(content)
        selected.append(
            {
                "id": item.id,
                "source": item.source,
                "source_digest": item.source_digest,
                "extraction_version": item.extraction_version,
                "sensitivity": item.sensitivity,
                "evidence_class": item.evidence_class,
                "content": content,
                "content_truncated": len(content) < len(item.content),
                "relevance_score": score,
            }
        )
    return tuple(selected)


__all__ = [
    "EXTRACTION_VERSION",
    "KnowledgeItem",
    "ProjectKnowledgeError",
    "ProjectKnowledgeView",
    "build_project_knowledge",
    "query_project_knowledge",
]
