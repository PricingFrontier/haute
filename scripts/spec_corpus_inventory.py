"""Create a reproducible inventory of the repository's specification corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ALLOWED_STATES = frozenset({"full", "partial", "mechanical", "unread"})
MARKDOWN_SUFFIX = ".md"
CORPUS_MANIFEST = "corpus.toml"
SUPPORTED_SPEC_SUFFIXES = frozenset({MARKDOWN_SUFFIX, ".toml"})
SUPPORTED_SUPPLEMENTAL_KINDS = frozenset({"decision"})


class SpecCorpusError(ValueError):
    """Raised when the specification corpus or its coverage declaration is invalid."""


@dataclass(frozen=True)
class FileRecord:
    path: str
    category: str
    sha256: str
    markdown_lines: int | None
    line_count: int
    coverage: CoverageRecord | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "sha256": self.sha256,
            "markdown_lines": self.markdown_lines,
            "coverage": self.coverage.public_dict() if self.coverage else None,
        }


@dataclass(frozen=True)
class CoverageRecord:
    state: str
    ranges: tuple[tuple[int, int], ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {"state": self.state, "ranges": [f"{start}-{end}" for start, end in self.ranges]}


@dataclass(frozen=True)
class SupplementalDocument:
    path: str
    kind: str
    required_headings: tuple[str, ...]


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _line_count(contents: bytes) -> int:
    return len(contents.splitlines())


def load_corpus_manifest(root: Path) -> dict[str, SupplementalDocument]:
    """Load the closed declaration for non-conventional component documents."""
    root = root.resolve()
    specs = root / "specs"
    if not specs.is_dir():
        raise SpecCorpusError(f"specification directory does not exist: {specs}")
    manifest = specs / CORPUS_MANIFEST
    try:
        with manifest.open("rb") as manifest_file:
            document = tomllib.load(manifest_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SpecCorpusError(f"cannot read corpus manifest {manifest}: {error}") from error

    if type(document.get("version")) is not int or document["version"] != 1:
        raise SpecCorpusError("corpus manifest must declare version = 1")
    unexpected_document_keys = set(document) - {"version", "supplemental_document"}
    if unexpected_document_keys:
        raise SpecCorpusError(
            "unexpected corpus manifest keys: " + ", ".join(sorted(unexpected_document_keys))
        )
    entries = document.get("supplemental_document", [])
    if not isinstance(entries, list):
        raise SpecCorpusError("corpus manifest supplemental_document must be an array of tables")

    declarations: dict[str, SupplementalDocument] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SpecCorpusError("each supplemental document declaration must be a table")
        unexpected_entry_keys = set(entry) - {"path", "kind", "required_headings"}
        if unexpected_entry_keys:
            raise SpecCorpusError(
                "unexpected supplemental document keys: " + ", ".join(sorted(unexpected_entry_keys))
            )
        raw_path = entry.get("path")
        kind = entry.get("kind")
        headings = entry.get("required_headings")
        if not isinstance(raw_path, str) or not raw_path:
            raise SpecCorpusError("supplemental document path must be a non-empty string")
        posix_path = PurePosixPath(raw_path)
        if (
            "\\" in raw_path
            or posix_path.is_absolute()
            or posix_path.as_posix() != raw_path
            or any(part in {"", ".", ".."} for part in posix_path.parts)
            or len(posix_path.parts) != 3
            or posix_path.parts[0] != "specs"
            or posix_path.parts[1] == "roadmap"
            or posix_path.suffix != MARKDOWN_SUFFIX
            or posix_path.name in {"high-level.md", "low-level.md"}
        ):
            raise SpecCorpusError(
                "supplemental document path must be a canonical repository-relative "
                f"component Markdown path: {raw_path!r}"
            )
        if not isinstance(kind, str) or kind not in SUPPORTED_SUPPLEMENTAL_KINDS:
            raise SpecCorpusError(
                f"unsupported supplemental document kind for {raw_path}: {kind!r}"
            )
        if (
            not isinstance(headings, list)
            or not headings
            or not all(
                isinstance(heading, str)
                and heading
                and heading == heading.strip()
                and not heading.startswith("#")
                and "\n" not in heading
                and "\r" not in heading
                for heading in headings
            )
            or len(headings) != len(set(headings))
        ):
            raise SpecCorpusError(
                f"required_headings must contain unique, non-empty heading names for {raw_path}"
            )
        if raw_path in declarations:
            raise SpecCorpusError(f"duplicate supplemental document declaration: {raw_path}")
        target = (root / Path(*posix_path.parts)).resolve()
        if not target.is_relative_to(specs.resolve()) or not target.is_file():
            raise SpecCorpusError(f"supplemental document does not exist: {raw_path}")
        declarations[raw_path] = SupplementalDocument(
            path=raw_path,
            kind=kind,
            required_headings=tuple(headings),
        )
    return declarations


def discover_spec_files(root: Path) -> list[tuple[Path, str]]:
    """Return every supported spec file or fail on an undeclared nested document."""
    root = root.resolve()
    specs = root / "specs"
    if not specs.is_dir():
        raise SpecCorpusError(f"specification directory does not exist: {specs}")
    supplemental = load_corpus_manifest(root)

    discovered: list[tuple[Path, str]] = []
    for child in sorted(specs.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name == "roadmap":
            continue
        high = child / "high-level.md"
        low = child / "low-level.md"
        if not high.is_file() or not low.is_file():
            raise SpecCorpusError(
                f"component {child.name!r} must contain both high-level.md and low-level.md"
            )
        discovered.extend(((high, "component_high"), (low, "component_low")))

    for path in sorted(specs.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.suffix in SUPPORTED_SPEC_SUFFIXES:
            discovered.append((path, "governance"))

    roadmap = specs / "roadmap"
    if roadmap.exists() and not roadmap.is_dir():
        raise SpecCorpusError(f"roadmap must be a directory: {roadmap}")
    if roadmap.is_dir():
        for path in sorted(
            roadmap.rglob("*"), key=lambda item: item.relative_to(roadmap).as_posix()
        ):
            if path.is_file() and path.suffix == MARKDOWN_SUFFIX:
                discovered.append((path, "roadmap"))

    discovered.extend(
        (root / Path(*PurePosixPath(path).parts), "component_supplemental")
        for path in sorted(supplemental)
    )
    discovered_paths = {path.absolute() for path, _ in discovered}
    literal_paths: set[Path] = set()
    for path in specs.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in SUPPORTED_SPEC_SUFFIXES:
            continue
        if not path.resolve().is_relative_to(specs):
            raise SpecCorpusError(
                f"specification file resolves outside the corpus: {_relative(root, path)}"
            )
        literal_paths.add(path.absolute())
    undeclared = sorted(_relative(root, path) for path in literal_paths - discovered_paths)
    if undeclared:
        raise SpecCorpusError("undeclared specification file(s): " + ", ".join(undeclared))
    return sorted(discovered, key=lambda item: _relative(root, item[0]))


def _parse_ranges(value: object, path: str, line_count: int) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list) or not value:
        raise SpecCorpusError(f"partial coverage for {path} requires a non-empty ranges list")
    ranges: list[tuple[int, int]] = []
    for item in value:
        if not isinstance(item, str) or item.count("-") != 1:
            raise SpecCorpusError(f"invalid coverage range for {path}: {item!r}")
        start_text, end_text = item.split("-")
        if not start_text.isdigit() or not end_text.isdigit():
            raise SpecCorpusError(f"invalid coverage range for {path}: {item!r}")
        start, end = int(start_text), int(end_text)
        if start < 1 or end < start or end > line_count:
            raise SpecCorpusError(f"coverage range out of bounds for {path}: {item!r}")
        ranges.append((start, end))
    overlapping = any(end >= next_start for (_, end), (next_start, _) in zip(ranges, ranges[1:]))
    if ranges != sorted(ranges) or overlapping:
        raise SpecCorpusError(f"coverage ranges for {path} must be sorted and non-overlapping")
    if sum(end - start + 1 for start, end in ranges) == line_count:
        raise SpecCorpusError(f"partial coverage for {path} cannot cover the whole file")
    return tuple(ranges)


def load_coverage(
    path: Path,
    records: list[FileRecord],
    *,
    contents: bytes | None = None,
) -> dict[str, CoverageRecord]:
    """Load and validate a version-1 coverage TOML against exactly these records."""
    try:
        text = path.read_text(encoding="utf-8") if contents is None else contents.decode("utf-8")
        document = tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SpecCorpusError(f"cannot read coverage TOML {path}: {error}") from error
    if type(document.get("version")) is not int or document["version"] != 1:
        raise SpecCorpusError("coverage TOML must declare version = 1")
    unexpected_document_keys = set(document) - {"version", "file"}
    if unexpected_document_keys:
        raise SpecCorpusError(
            "unexpected coverage TOML keys: " + ", ".join(sorted(unexpected_document_keys))
        )
    entries = document.get("file")
    if not isinstance(entries, list):
        raise SpecCorpusError("coverage TOML must contain [[file]] records")
    known = {record.path: record for record in records}
    coverage: dict[str, CoverageRecord] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SpecCorpusError("each coverage file record must be a table")
        unexpected_entry_keys = set(entry) - {"path", "state", "ranges"}
        if unexpected_entry_keys:
            raise SpecCorpusError(
                "unexpected coverage record keys: " + ", ".join(sorted(unexpected_entry_keys))
            )
        record_path, state = entry.get("path"), entry.get("state")
        if not isinstance(record_path, str) or not isinstance(state, str):
            raise SpecCorpusError("coverage file records require string path and state")
        if record_path in coverage:
            raise SpecCorpusError(f"duplicate coverage record: {record_path}")
        if record_path not in known:
            raise SpecCorpusError(f"coverage record is outside the inventory: {record_path}")
        if state not in ALLOWED_STATES:
            raise SpecCorpusError(f"invalid coverage state for {record_path}: {state!r}")
        ranges_value = entry.get("ranges")
        if state == "partial":
            ranges = _parse_ranges(ranges_value, record_path, known[record_path].line_count)
        elif ranges_value is not None:
            raise SpecCorpusError(
                f"coverage ranges are only allowed for partial state: {record_path}"
            )
        else:
            ranges = ()
        coverage[record_path] = CoverageRecord(state, ranges)
    missing = sorted(set(known) - set(coverage))
    if missing:
        raise SpecCorpusError(f"coverage is missing inventory files: {', '.join(missing)}")
    return coverage


def _head_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def build_inventory(root: Path, coverage_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    paths = discover_spec_files(root)
    snapshot = hashlib.sha256()
    captured: dict[Path, bytes] = {}
    records: list[FileRecord] = []
    for path, category in paths:
        try:
            contents = path.read_bytes()
        except OSError as error:
            raise SpecCorpusError(f"cannot read inventory file {path}: {error}") from error
        resolved = path.resolve()
        captured[resolved] = contents
        relative = _relative(root, path)
        snapshot.update(relative.encode("utf-8"))
        snapshot.update(b"\0")
        snapshot.update(contents)
        snapshot.update(b"\0")
        line_count = _line_count(contents)
        records.append(
            FileRecord(
                relative,
                category,
                hashlib.sha256(contents).hexdigest(),
                line_count if path.suffix == MARKDOWN_SUFFIX else None,
                line_count,
            )
        )
    coverage: dict[str, CoverageRecord] = {}
    if coverage_path is not None:
        resolved_coverage = (
            coverage_path if coverage_path.is_absolute() else root / coverage_path
        ).resolve()
        coverage = load_coverage(
            resolved_coverage,
            records,
            contents=captured.get(resolved_coverage),
        )
    records = [
        FileRecord(**{**asdict(record), "coverage": coverage.get(record.path)})
        for record in records
    ]
    categories = (
        "component_high",
        "component_low",
        "component_supplemental",
        "governance",
        "roadmap",
    )
    markdown = {
        category: {
            "files": sum(
                record.category == category and record.markdown_lines is not None
                for record in records
            ),
            "lines": sum(
                record.markdown_lines or 0 for record in records if record.category == category
            ),
        }
        for category in categories
    }
    markdown["total"] = {
        "files": sum(record.markdown_lines is not None for record in records),
        "lines": sum(record.markdown_lines or 0 for record in records),
    }
    states = {
        state: sum(
            record.coverage is not None and record.coverage.state == state for record in records
        )
        for state in sorted(ALLOWED_STATES)
    }
    reviewed = {}
    for state in sorted(ALLOWED_STATES):
        reviewed[state] = sum(
            r.line_count
            if state == "full"
            else sum(end - start + 1 for start, end in r.coverage.ranges)
            for r in records
            if r.coverage is not None and r.coverage.state == state
        )
    return {
        "snapshot": {
            "policy": (
                "working-tree on-disk bytes; staged and unstaged content present "
                "there plus untracked in-scope files are included"
            ),
            "digest": snapshot.hexdigest(),
            "head": _head_commit(root),
        },
        "files": [record.public_dict() for record in records],
        "summary": {
            "components": {
                "pairs": sum(r.category == "component_high" for r in records),
                "high": sum(r.category == "component_high" for r in records),
                "low": sum(r.category == "component_low" for r in records),
            },
            "governance_files": sum(r.category == "governance" for r in records),
            "roadmap_files": sum(r.category == "roadmap" for r in records),
            "markdown": markdown,
            "coverage": {
                "files_by_state": states,
                "reviewed_lines_by_state": reviewed,
                "fully_read_files": states["full"],
            },
        },
    }


def render_text(inventory: dict[str, Any]) -> str:
    snapshot, summary = inventory["snapshot"], inventory["summary"]
    return "\n".join(
        (
            f"Snapshot policy: {snapshot['policy']}",
            f"Snapshot digest: {snapshot['digest']}",
            f"HEAD: {snapshot['head'] or 'unavailable'}",
            "Counts: "
            + ", ".join(f"{key}={value}" for key, value in summary["components"].items()),
            f"governance={summary['governance_files']}, roadmap={summary['roadmap_files']}",
            "Markdown: "
            + ", ".join(
                f"{category}={counts['files']} files/{counts['lines']} lines"
                for category, counts in summary["markdown"].items()
            ),
            "Coverage: "
            + ", ".join(
                f"{state}={count}" for state, count in summary["coverage"]["files_by_state"].items()
            ),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        inventory = build_inventory(args.root, args.coverage)
    except SpecCorpusError as error:
        parser.error(str(error))
    output = (
        json.dumps(inventory, sort_keys=True, separators=(",", ":"))
        if args.format == "json"
        else render_text(inventory)
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
