"""Validates the workflow coverage ledger."""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from haute._types import NodeType
from tests._test_debt_scanner import (
    REPO_ROOT,
    _DebtSite,
    _DebtVisitor,
    _FrontendDebtSite,
    _is_frontend_test_file,
    _mask_frontend_comments_and_strings,
    _scan_frontend_source,
    _skip_frontend_balanced_call,
)

pytestmark = pytest.mark.meta

_ENTRY_POINTS = frozenset({"file", "cli", "http", "browser", "hosted", "scoring", "library"})
_SCENARIO_STATES = frozenset({"covered", "gap", "decision", "not-applicable"})
_SCENARIO_TIERS = frozenset({"unit", "route", "workflow", "browser", "property", "process"})
_SCENARIO_LANES = frozenset(
    {"backend", "frontend", "browser", "platform", "package", "perf", "mutation"}
)

_WORKFLOW_ID_PATTERN = re.compile(r"^W\d{2}$")
_SCENARIO_ID_PATTERN = re.compile(r"^W\d{2}-S\d{2}$")
_FINDING_PATTERN = re.compile(r"^F\d+$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
# A module-level ``pytestmark`` naming the perf marker (or a file under
# tests/performance/) is deselected by the ordinary lane's ``-m 'not perf'``
# addopts, so it can never witness a covered scenario (ENG-T12 step 1).
_PERF_MARK_PATTERN = re.compile(r"\bperf\b")
_PROPERTY_MANIFEST = Path("scripts") / "property_test_files.txt"
_HYPOTHESIS_IMPORT = re.compile(r"^(?:from|import) hypothesis\b", re.MULTILINE)
_ROADMAP_HEADING_PATTERN = re.compile(r"^### ([A-Z0-9]+(?:-[A-Z0-9]+)+)\s+", re.MULTILINE)
_MARKDOWN_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_TEST_CALL_START = re.compile(r"^[ \t]*(?:it|test)\b", re.MULTILINE)
_MEMBER_PATTERN = re.compile(r"\.([A-Za-z_$][\w$]*)")
_PROPERTY_MODIFIERS = frozenset(
    {"only", "skip", "fixme", "fail", "fails", "todo", "concurrent", "sequential", "serial"}
)
_CALLED_FACTORIES = frozenset({"each", "for", "skipIf", "runIf", "extend"})


@dataclass(frozen=True)
class ParsedTestReference:
    file_part: str
    remainder: str


def _parse_reference(test_ref: str) -> ParsedTestReference | None:
    if "::" not in test_ref:
        return None
    file_part, remainder = test_ref.split("::", 1)
    return ParsedTestReference(file_part=file_part, remainder=remainder)


def load_ledger(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def roadmap_package_ids(root: Path) -> set[str]:
    roadmap_dir = root / "specs" / "roadmap"
    if not roadmap_dir.is_dir():
        return set()
    package_ids: set[str] = set()
    for path in roadmap_dir.glob("*.md"):
        if path.name == "README.md" or "findings" in path.name:
            continue
        content = path.read_text(encoding="utf-8")
        for match in _ROADMAP_HEADING_PATTERN.finditer(content):
            package_ids.add(match.group(1))
    return package_ids


def slug(heading: str) -> str:
    text = re.sub(r"[`*_~]", "", heading)
    text = text.strip().casefold()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def _extract_headings(content: str) -> list[str]:
    return [match.group(1).strip() for match in _MARKDOWN_HEADING_PATTERN.finditer(content)]


def _validate_contract_file_part(
    file_part: str, *, root: Path, context: str
) -> tuple[list[str], Path | None]:
    if not file_part.strip():
        return [f"{context}: contract reference has empty path"], None

    p = Path(file_part)
    if p.is_absolute() or bool(p.drive) or file_part.startswith(("/", "\\")):
        return [
            f"{context}: contract file {file_part!r} must be repository-relative, not absolute"
        ], None

    parts = p.parts
    if any(part == ".." for part in parts) or ".." in file_part.replace("\\", "/").split("/"):
        return [
            f"{context}: contract file {file_part!r} must not contain '..' traversal segments"
        ], None

    if len(parts) == 0 or parts[0] != "specs":
        return [f"{context}: contract file {file_part!r} must resolve under specs/"], None

    target = root / file_part

    if target == root or target == root / "specs" or (target.exists() and target.is_dir()):
        return [f"{context}: contract path {file_part!r} is a directory, not a file"], None

    if not file_part.endswith(".md"):
        return [f"{context}: contract file {file_part!r} must be a .md file"], None

    if not target.is_file():
        return [f"{context}: contract file {file_part!r} does not exist under specs/"], None

    return [], target


def _validate_contract_reference(contract_ref: str, *, root: Path, scenario_id: str) -> list[str]:
    context = f"scenario {scenario_id}"
    if "#" in contract_ref:
        file_part, anchor = contract_ref.split("#", 1)
    else:
        file_part, anchor = contract_ref, None

    errs, target = _validate_contract_file_part(file_part, root=root, context=context)
    if errs:
        return errs
    assert target is not None

    if anchor is None:
        return [f"{context}: contract reference {contract_ref!r} missing heading anchor"]
    if not anchor.strip():
        return [f"{context}: contract reference {contract_ref!r} has empty heading anchor"]

    content = target.read_text(encoding="utf-8")
    headings = _extract_headings(content)
    valid_slugs = {slug(h) for h in headings}
    if anchor not in valid_slugs:
        return [f"{context}: contract anchor {anchor!r} not found in {file_part}"]

    return []


def _scan_backend_debt(root: Path) -> list[_DebtSite]:
    sites: list[_DebtSite] = []
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        for py_path in sorted(tests_dir.rglob("*.py")):
            if not py_path.is_file():
                continue
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            except SyntaxError:
                continue
            rel_path = py_path.relative_to(root)
            sites.extend(_DebtVisitor(rel_path).scan(tree))
    return sites


def _scan_frontend_debt(root: Path) -> list[_FrontendDebtSite]:
    sites: list[_FrontendDebtSite] = []
    for sub in ("src", "e2e"):
        fe_dir = root / "frontend" / sub
        if not fe_dir.is_dir():
            continue
        for fe_path in sorted(fe_dir.rglob("*")):
            if not fe_path.is_file():
                continue
            rel_path = fe_path.relative_to(root)
            if not _is_frontend_test_file(REPO_ROOT / rel_path):
                continue
            content = fe_path.read_text(encoding="utf-8")
            sites.extend(_scan_frontend_source(content, rel_path))
    return sites


def _module_is_perf_marked(tree: ast.Module) -> bool:
    """True when a module-level ``pytestmark`` assignment names the perf marker."""
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            continue
        if _PERF_MARK_PATTERN.search(ast.unparse(node)):
            return True
    return False


def hypothesis_test_modules(root: Path) -> set[str]:
    """Repository-relative test modules that import Hypothesis (the exploration lane)."""
    found: set[str] = set()
    for path in (root / "tests").rglob("test_*.py"):
        if "performance" in path.relative_to(root).parts:
            continue
        if _HYPOTHESIS_IMPORT.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(root).as_posix())
    return found


def property_manifest_modules(root: Path) -> set[str]:
    """Modules listed in scripts/property_test_files.txt (comments and blanks skipped)."""
    lines = (root / _PROPERTY_MANIFEST).read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _is_valid_frontend_test_path(file_part: str) -> bool:
    parts = Path(file_part).parts
    if len(parts) < 2 or parts[0] != "frontend":
        return False
    sub = parts[1]
    name = parts[-1]
    subparts = parts[1:]
    if sub == "src":
        return "__tests__" in subparts and (name.endswith(".test.ts") or name.endswith(".test.tsx"))
    if sub == "e2e":
        if "__tests__" in subparts:
            return name.endswith(".test.ts")
        return name.endswith(".spec.ts")
    return False


def _skip_trivia(content: str, index: int) -> int:
    """Advance past whitespace and comments in the original source."""
    while index < len(content):
        if content[index].isspace():
            index += 1
        elif content.startswith("/*", index):
            end = content.find("*/", index + 2)
            if end < 0:
                return len(content)
            index = end + 2
        elif content.startswith("//", index):
            end = content.find("\n", index + 2)
            if end < 0:
                return len(content)
            index = end + 1
        else:
            break
    return index


_SIMPLE_JS_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "0": "\0",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "`": "`",
}


def _decode_js_escapes(literal: str) -> str | None:
    """Return the runtime value of a static JavaScript string body, or None if malformed."""
    out: list[str] = []
    index = 0
    while index < len(literal):
        char = literal[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(literal):
            return None
        escape = literal[index]
        if escape in _SIMPLE_JS_ESCAPES:
            out.append(_SIMPLE_JS_ESCAPES[escape])
            index += 1
            continue
        if escape == "u":
            if literal.startswith("{", index + 1):
                close = literal.find("}", index + 2)
                if close < 0:
                    return None
                hex_digits = literal[index + 2 : close]
                index = close + 1
            else:
                hex_digits = literal[index + 1 : index + 5]
                if len(hex_digits) != 4:
                    return None
                index += 5
        elif escape == "x":
            hex_digits = literal[index + 1 : index + 3]
            if len(hex_digits) != 2:
                return None
            index += 3
        elif escape in "\r\n":
            index += 1
            if escape == "\r" and literal.startswith("\n", index):
                index += 1
            continue
        else:
            out.append(escape)
            index += 1
            continue
        try:
            out.append(chr(int(hex_digits, 16)))
        except ValueError:
            return None
    return "".join(out)


def _first_string_argument(content: str, open_index: int) -> str | None:
    """Return the complete single string literal that is the call's first argument.

    ``open_index`` is the position of the opening parenthesis. Comments may
    precede or follow the literal, and it must be followed by a comma or the
    closing parenthesis, so a concatenation or template expression never
    matches a ledger title. Returns ``None`` when the first argument is not one
    complete string literal.
    """
    cursor = _skip_trivia(content, open_index + 1)
    quote = content[cursor : cursor + 1]
    if quote not in {"'", '"', "`"}:
        return None
    end = cursor + 1
    while end < len(content):
        char = content[end]
        if char == "\\":
            end += 2
            continue
        if char == quote:
            break
        if char == "\n" and quote != "`":
            return None
        end += 1
    else:
        return None
    literal = content[cursor + 1 : end]
    if quote == "`" and "${" in literal:
        # Only the expanded runtime titles are collected; the raw template
        # source can never be the exact title of a collected test.
        return None
    after = _skip_trivia(content, end + 1)
    if content[after : after + 1] not in {",", ")"}:
        return None
    return _decode_js_escapes(literal)


def _declares_test_title(content: str, title: str) -> bool:
    """Return whether an ``it()``/``test()`` declaration names *title*.

    The chain after ``it``/``test`` may pass through property modifiers and
    called factories whose balanced argument list (nested calls included) is
    consumed before the title-bearing call. Comments and string contents are
    masked for the structural walk; the title itself is read from the original
    source at the position the walk reaches. Suites, hooks, and fixture-only
    ``extend`` calls never match.
    """
    masked = _mask_frontend_comments_and_strings(content)
    for start in _TEST_CALL_START.finditer(masked):
        pos = start.end()
        while True:
            member = _MEMBER_PATTERN.match(masked, pos)
            if member is not None:
                name = member.group(1)
                pos = member.end()
                if name in _PROPERTY_MODIFIERS:
                    continue
                if name in _CALLED_FACTORIES and masked.startswith("(", pos):
                    pos = _skip_frontend_balanced_call(masked, pos)
                    continue
                break
            if masked.startswith("(", pos) and _first_string_argument(content, pos) == title:
                return True
            break
    return False


def _validate_test_reference(
    test_ref: str,
    *,
    root: Path,
    scenario_id: str,
    backend_debt_sites: list[_DebtSite],
    frontend_debt_sites: list[_FrontendDebtSite],
) -> list[str]:
    parsed = _parse_reference(test_ref)
    if parsed is None:
        return [f"scenario {scenario_id}: test reference {test_ref!r} missing '::' delimiter"]

    file_part = parsed.file_part
    remainder = parsed.remainder
    p = Path(file_part)

    if p.is_absolute() or bool(p.drive) or file_part.startswith(("/", "\\")):
        return [
            f"scenario {scenario_id}: test file {file_part!r} must be "
            "repository-relative, not absolute"
        ]

    parts = p.parts
    if any(part == ".." for part in parts) or ".." in file_part.replace("\\", "/").split("/"):
        return [
            f"scenario {scenario_id}: test file {file_part!r} must not contain "
            "'..' traversal segments"
        ]

    if (len(parts) > 0 and parts[0] == "src") or file_part.startswith("src/"):
        return [f"scenario {scenario_id}: test file {file_part!r} is a production file under src/"]

    if len(parts) == 0 or parts[0] not in {"tests", "frontend"}:
        return [f"scenario {scenario_id}: test file {file_part!r} is outside the allowed roots"]

    file_path = root / file_part
    if not file_path.is_file():
        return [f"scenario {scenario_id}: test file {file_part!r} does not exist under root"]

    posix_rel = p.as_posix()

    if file_part.endswith(".py"):
        if parts[0] != "tests":
            return [
                f"scenario {scenario_id}: python test file {file_part!r} must resolve inside tests/"
            ]

        if not (p.name.startswith("test_") and file_part.endswith(".py")):
            return [
                f"scenario {scenario_id}: python test file {file_part!r} "
                "file name must match test_*.py"
            ]

        if len(parts) > 2 and parts[1] == "performance":
            return [
                f"scenario {scenario_id}: test file {file_part!r} lives under "
                "tests/performance/ and is not collected by the ordinary lane"
            ]

        rem_parts = remainder.split("::")
        if len(rem_parts) == 1:
            class_name: str | None = None
            fn_name = rem_parts[0]
        elif len(rem_parts) == 2:
            class_name = rem_parts[0]
            fn_name = rem_parts[1]
        else:
            return [
                f"scenario {scenario_id}: test reference {test_ref!r} has invalid remainder format"
            ]

        if class_name is not None and not class_name.startswith("Test"):
            return [
                f"scenario {scenario_id}: class name {class_name!r} in {file_part} "
                "must start with 'Test'"
            ]

        if not fn_name.startswith("test"):
            return [
                f"scenario {scenario_id}: test function {fn_name!r} in {file_part} "
                "is a non-test helper function (must start with 'test')"
            ]

        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except SyntaxError as err:
            return [f"scenario {scenario_id}: syntax error parsing {file_part}: {err}"]

        if _module_is_perf_marked(tree):
            return [
                f"scenario {scenario_id}: test file {file_part!r} carries a module-level "
                "perf mark and is deselected by the ordinary lane"
            ]

        if class_name is not None:
            target_class: ast.ClassDef | None = None
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    target_class = node
                    break
            if target_class is None:
                return [f"scenario {scenario_id}: class {class_name!r} not found in {file_part}"]
            func_found = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name
                for node in target_class.body
            )
            if not func_found:
                return [
                    f"scenario {scenario_id}: function {fn_name!r} not found in "
                    f"class {class_name} in {file_part}"
                ]
            covering_scopes = {"<module>", class_name, f"{class_name}.{fn_name}"}
        else:
            func_found = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name
                for node in tree.body
            )
            if not func_found:
                return [
                    f"scenario {scenario_id}: function {fn_name!r} not found at "
                    f"module level in {file_part}"
                ]
            covering_scopes = {"<module>", fn_name}

        for site in backend_debt_sites:
            if site.path.as_posix() == posix_rel and site.scope in covering_scopes:
                return [
                    f"scenario {scenario_id}: test reference {test_ref!r} covered by debt site "
                    f"{site.id} ({site.kind} in {site.scope!r})"
                ]

        return []

    if file_part.endswith((".ts", ".tsx")):
        if not _is_valid_frontend_test_path(file_part):
            return [
                f"scenario {scenario_id}: frontend file {file_part} does not match allowed "
                "Vitest or Playwright test patterns"
            ]

        for fe_site in frontend_debt_sites:
            if fe_site.path.as_posix() == posix_rel:
                return [
                    f"scenario {scenario_id}: frontend file {file_part} has debt site "
                    f"{fe_site.id} ({fe_site.callee})"
                ]

        title = remainder
        content = file_path.read_text(encoding="utf-8")
        if not _declares_test_title(content, title):
            return [
                f"scenario {scenario_id}: test title {title!r} not found in it() or test() call "
                f"in {file_part}"
            ]
        return []

    return [f"scenario {scenario_id}: test file {file_part!r} has unsupported extension"]


def ledger_violations(
    data: dict[str, Any],
    *,
    root: Path,
    node_types: Iterable[str] | None = None,
) -> list[str]:
    violations: list[str] = []

    version = data.get("version")
    if version != 1:
        violations.append(f"version: expected 1, found {version!r}")

    allowed_top_keys = {"version", "workflows", "node_types", "scenarios"}
    for extra_key in sorted(set(data.keys()) - allowed_top_keys):
        violations.append(f"top-level: unexpected key {extra_key!r}")

    for table_name in ("workflows", "node_types", "scenarios"):
        tbl = data.get(table_name)
        if not isinstance(tbl, list) or len(tbl) == 0:
            violations.append(f"{table_name}: table must be present and non-empty")

    workflows_data = data.get("workflows") if isinstance(data.get("workflows"), list) else []
    node_types_data = data.get("node_types") if isinstance(data.get("node_types"), list) else []
    scenarios_data = data.get("scenarios") if isinstance(data.get("scenarios"), list) else []

    seen_wf_ids: set[str] = set()
    valid_wf_ids: set[str] = set()
    wf_entry_points: dict[str, set[str]] = {}
    all_mapped_components: set[str] = set()
    wf_documents: dict[str, set[str]] = {}
    all_workflow_documents: set[str] = set()

    for wf in workflows_data:
        if not isinstance(wf, dict):
            violations.append("workflows: entry must be a table")
            continue
        wf_id = wf.get("id")
        if not isinstance(wf_id, str) or not _WORKFLOW_ID_PATTERN.match(wf_id):
            violations.append(f"workflow {wf_id!r}: id must match ^W\\d{{2}}$")
        else:
            if wf_id in seen_wf_ids:
                violations.append(f"workflow {wf_id}: duplicate workflow id")
            seen_wf_ids.add(wf_id)
            valid_wf_ids.add(wf_id)

        title = wf.get("title")
        if not isinstance(title, str) or not title.strip():
            violations.append(f"workflow {wf_id}: title must be non-empty string")

        components = wf.get("components")
        if not isinstance(components, list) or len(components) == 0:
            violations.append(f"workflow {wf_id}: components must be non-empty list")
        else:
            for comp in components:
                if not isinstance(comp, str) or not comp.strip():
                    violations.append(f"workflow {wf_id}: component must be non-empty string")
                elif comp == "roadmap":
                    violations.append(f"workflow {wf_id}: component 'roadmap' is not allowed")
                else:
                    comp_dir = root / "specs" / comp
                    if not comp_dir.is_dir():
                        violations.append(f"workflow {wf_id}: unknown component directory {comp!r}")
                    else:
                        all_mapped_components.add(comp)

        documents = wf.get("documents")
        if not isinstance(documents, list) or len(documents) == 0:
            violations.append(f"workflow {wf_id}: documents must be non-empty list")
        else:
            if isinstance(wf_id, str):
                wf_documents.setdefault(wf_id, set())
            for doc in documents:
                if not isinstance(doc, str) or not doc.strip():
                    violations.append(f"workflow {wf_id}: document must be non-empty string")
                    continue
                doc_errs, _ = _validate_contract_file_part(
                    doc, root=root, context=f"workflow {wf_id} document"
                )
                violations.extend(doc_errs)
                all_workflow_documents.add(doc)
                if isinstance(wf_id, str):
                    wf_documents[wf_id].add(doc)

        eps = wf.get("entry_points")
        if not isinstance(eps, list) or len(eps) == 0:
            violations.append(f"workflow {wf_id}: entry_points must be non-empty list")
        else:
            eps_set = set(eps)
            invalid_eps = eps_set - _ENTRY_POINTS
            for iep in sorted(invalid_eps):
                violations.append(f"workflow {wf_id}: invalid entry point {iep!r}")
            if isinstance(wf_id, str):
                wf_entry_points[wf_id] = eps_set

    specs_dir = root / "specs"
    if specs_dir.is_dir():
        expected_comp_dirs = {
            p.name for p in specs_dir.iterdir() if p.is_dir() and p.name != "roadmap"
        }
        unmapped_dirs = expected_comp_dirs - all_mapped_components
        for d in sorted(unmapped_dirs):
            violations.append(f"specs: directory {d!r} is not named by any workflow")

    corpus_path = root / "specs" / "corpus.toml"
    if corpus_path.is_file():
        try:
            with corpus_path.open("rb") as f:
                corpus_data = tomllib.load(f)
            supp_docs = corpus_data.get("supplemental_document", [])
            if isinstance(supp_docs, list):
                for entry in supp_docs:
                    if isinstance(entry, dict):
                        supp_path = entry.get("path")
                        if isinstance(supp_path, str) and supp_path not in all_workflow_documents:
                            violations.append(
                                f"supplemental document {supp_path!r} not declared in any "
                                "workflow documents"
                            )
        except Exception as err:
            violations.append(f"specs/corpus.toml: error loading: {err}")

    if node_types is None:
        expected_node_types = {member.value for member in NodeType}
    else:
        expected_node_types = set(node_types)

    seen_nt_ids: set[str] = set()
    for nt in node_types_data:
        if not isinstance(nt, dict):
            violations.append("node_types: entry must be a table")
            continue
        nt_id = nt.get("id")
        if not isinstance(nt_id, str):
            violations.append(f"node_type {nt_id!r}: id must be a string")
            continue
        if nt_id in seen_nt_ids:
            violations.append(f"node_type {nt_id}: duplicate node type id")
        seen_nt_ids.add(nt_id)

        nt_wfs = nt.get("workflows")
        if not isinstance(nt_wfs, list) or len(nt_wfs) == 0:
            violations.append(f"node_type {nt_id}: workflows must be non-empty list")
        else:
            for w in nt_wfs:
                if w not in valid_wf_ids:
                    violations.append(
                        f"node_type {nt_id}: referenced workflow {w!r} does not exist"
                    )

    for missing_id in sorted(expected_node_types - seen_nt_ids):
        violations.append(f"node_types: missing node type {missing_id!r}")
    for unknown_id in sorted(seen_nt_ids - expected_node_types):
        violations.append(f"node_types: unknown node type {unknown_id!r}")

    seen_sc_ids: set[str] = set()
    workflows_with_scenarios: set[str] = set()
    roadmap_pkgs = roadmap_package_ids(root)

    backend_debt_sites: list[_DebtSite] | None = None
    frontend_debt_sites: list[_FrontendDebtSite] | None = None

    def get_backend_debt() -> list[_DebtSite]:
        nonlocal backend_debt_sites
        if backend_debt_sites is None:
            backend_debt_sites = _scan_backend_debt(root)
        return backend_debt_sites

    def get_frontend_debt() -> list[_FrontendDebtSite]:
        nonlocal frontend_debt_sites
        if frontend_debt_sites is None:
            frontend_debt_sites = _scan_frontend_debt(root)
        return frontend_debt_sites

    for sc in scenarios_data:
        if not isinstance(sc, dict):
            violations.append("scenarios: entry must be a table")
            continue
        sc_id = sc.get("id")
        if not isinstance(sc_id, str) or not _SCENARIO_ID_PATTERN.match(sc_id):
            violations.append(f"scenario {sc_id!r}: id must match ^W\\d{{2}}-S\\d{{2}}$")
        else:
            if sc_id in seen_sc_ids:
                violations.append(f"scenario {sc_id}: duplicate scenario id")
            seen_sc_ids.add(sc_id)

        prefix = sc_id.split("-S", 1)[0] if isinstance(sc_id, str) and "-S" in sc_id else ""
        wf_field = sc.get("workflow")
        if prefix != wf_field:
            violations.append(
                f"scenario {sc_id}: prefix {prefix!r} does not match workflow field {wf_field!r}"
            )

        if wf_field not in valid_wf_ids:
            violations.append(f"scenario {sc_id}: workflow {wf_field!r} does not exist")
        else:
            workflows_with_scenarios.add(wf_field)

        inv = sc.get("invariant")
        if not isinstance(inv, str) or not inv.strip():
            violations.append(f"scenario {sc_id}: invariant must be non-empty string")

        state = sc.get("state")
        if state not in _SCENARIO_STATES:
            violations.append(f"scenario {sc_id}: state {state!r} is invalid")

        entry_point = sc.get("entry_point")
        allowed_eps = wf_entry_points.get(wf_field, set()) if isinstance(wf_field, str) else set()
        if entry_point not in allowed_eps:
            violations.append(
                f"scenario {sc_id}: entry_point {entry_point!r} not in "
                f"workflow {wf_field!r} entry_points"
            )

        tier = sc.get("tier")
        if tier not in _SCENARIO_TIERS:
            violations.append(f"scenario {sc_id}: tier {tier!r} is invalid")

        lane = sc.get("lane")
        if lane not in _SCENARIO_LANES:
            violations.append(f"scenario {sc_id}: lane {lane!r} is invalid")

        real = sc.get("real")
        if not isinstance(real, list) or not all(isinstance(x, str) and x for x in real):
            violations.append(f"scenario {sc_id}: real must be a list of non-empty strings")

        stubbed = sc.get("stubbed")
        if not isinstance(stubbed, list) or not all(isinstance(x, str) and x for x in stubbed):
            violations.append(f"scenario {sc_id}: stubbed must be a list of non-empty strings")

        finding = sc.get("finding")
        if finding is not None:
            if not isinstance(finding, str) or not _FINDING_PATTERN.match(finding):
                violations.append(f"scenario {sc_id}: finding {finding!r} must match ^F\\d+$")

        contract = sc.get("contract")
        if not isinstance(contract, str) or not contract.strip():
            violations.append(f"scenario {sc_id}: contract must be non-empty string")
        else:
            violations.extend(
                _validate_contract_reference(contract, root=root, scenario_id=str(sc_id))
            )
            file_part = contract.split("#", 1)[0] if "#" in contract else contract
            doc_errs, _ = _validate_contract_file_part(
                file_part, root=root, context=f"scenario {sc_id}"
            )
            if not doc_errs:
                wf_docs = wf_documents.get(wf_field, set()) if isinstance(wf_field, str) else set()
                if file_part not in wf_docs:
                    violations.append(
                        f"scenario {sc_id}: contract file {file_part} "
                        f"is not among workflow {wf_field} documents"
                    )

        tests = sc.get("tests")
        if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
            violations.append(f"scenario {sc_id}: tests must be a list of strings")

        if state == "covered":
            if not tests:
                violations.append(f"scenario {sc_id}: covered state requires non-empty tests list")
            if "package" in sc and sc["package"] is not None:
                violations.append(f"scenario {sc_id}: covered state must not specify package")
            evidence = sc.get("evidence")
            if not isinstance(evidence, dict):
                violations.append(f"scenario {sc_id}: covered state requires evidence table")
            else:
                commit = evidence.get("commit")
                if not isinstance(commit, str) or not _COMMIT_PATTERN.match(commit):
                    violations.append(
                        f"scenario {sc_id}: evidence commit {commit!r} "
                        f"must match ^[0-9a-f]{{7,40}}$"
                    )
                cmd = evidence.get("command")
                if not isinstance(cmd, str) or not cmd.strip():
                    violations.append(f"scenario {sc_id}: evidence command must be non-empty")
                res = evidence.get("result")
                if not isinstance(res, str) or not res.strip():
                    violations.append(f"scenario {sc_id}: evidence result must be non-empty")

        elif state in {"gap", "decision"}:
            pkg = sc.get("package")
            if not isinstance(pkg, str) or not pkg.strip():
                violations.append(f"scenario {sc_id}: {state} state requires package")
            elif pkg not in roadmap_pkgs:
                violations.append(
                    f"scenario {sc_id}: package {pkg!r} not found in roadmap packages"
                )

        elif state == "not-applicable":
            reason = sc.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                violations.append(
                    f"scenario {sc_id}: not-applicable state requires non-empty reason"
                )
            if "package" in sc and sc["package"] is not None:
                violations.append(
                    f"scenario {sc_id}: not-applicable state must not specify package"
                )

        if isinstance(tests, list):
            for test_ref in tests:
                if isinstance(test_ref, str):
                    violations.extend(
                        _validate_test_reference(
                            test_ref,
                            root=root,
                            scenario_id=str(sc_id),
                            backend_debt_sites=get_backend_debt(),
                            frontend_debt_sites=get_frontend_debt(),
                        )
                    )

    for wf_id in valid_wf_ids:
        if wf_id not in workflows_with_scenarios:
            violations.append(f"workflow {wf_id}: must have at least one scenario")

    return sorted(violations)


def _valid_ledger(root: Path) -> dict[str, Any]:
    specs_alpha = root / "specs" / "alpha"
    specs_alpha.mkdir(parents=True, exist_ok=True)
    (specs_alpha / "high-level.md").write_text(
        "# Alpha\n\n## The `alpha` contract\n", encoding="utf-8"
    )
    (specs_alpha / "decision.md").write_text("# Decision\n\n## Context\n", encoding="utf-8")

    corpus_path = root / "specs" / "corpus.toml"
    corpus_path.write_text(
        'version = 1\n\n[[supplemental_document]]\npath = "specs/alpha/decision.md"\n',
        encoding="utf-8",
    )

    roadmap_dir = root / "specs" / "roadmap"
    roadmap_dir.mkdir(parents=True, exist_ok=True)
    (roadmap_dir / "alpha.md").write_text("### ALPHA-P01 Something\n", encoding="utf-8")

    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_alpha.py").write_text(
        "import pytest\n\n"
        "def test_ok() -> None:\n"
        "    pass\n\n"
        '@pytest.mark.skip(reason="x")\n'
        "def test_skipped() -> None:\n"
        "    pass\n\n"
        "def _helper() -> None:\n"
        "    pass\n\n"
        "class TestAlpha:\n"
        "    def test_in_class(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )

    src_dir = root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "alpha.py").write_text(
        "def not_a_test() -> None:\n    pass\n",
        encoding="utf-8",
    )

    fe_src_dir = root / "frontend" / "src" / "__tests__"
    fe_src_dir.mkdir(parents=True, exist_ok=True)
    (fe_src_dir / "alpha.test.ts").write_text(
        'it("alpha title", () => {});\n',
        encoding="utf-8",
    )

    fe_e2e_dir = root / "frontend" / "e2e"
    fe_e2e_dir.mkdir(parents=True, exist_ok=True)
    (fe_e2e_dir / "alpha.spec.ts").write_text(
        'test("alpha e2e title", async () => {});\n',
        encoding="utf-8",
    )

    return {
        "version": 1,
        "workflows": [
            {
                "id": "W01",
                "title": "Alpha workflow",
                "components": ["alpha"],
                "documents": ["specs/alpha/high-level.md", "specs/alpha/decision.md"],
                "entry_points": ["http"],
            }
        ],
        "node_types": [
            {
                "id": "alpha",
                "workflows": ["W01"],
            }
        ],
        "scenarios": [
            {
                "id": "W01-S01",
                "workflow": "W01",
                "contract": "specs/alpha/high-level.md#the-alpha-contract",
                "invariant": "Alpha workflow operates correctly.",
                "state": "covered",
                "entry_point": "http",
                "tier": "unit",
                "lane": "backend",
                "real": [],
                "stubbed": [],
                "tests": ["tests/test_alpha.py::test_ok"],
                "evidence": {
                    "commit": "0123456789abcdef",
                    "command": "uv run pytest tests/test_alpha.py::test_ok -q -p no:warnings",
                    "result": "1 passed",
                },
            }
        ],
    }


def test_workflow_coverage_ledger_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    ledger_path = root / "tests" / "workflow_coverage.toml"
    data = load_ledger(ledger_path)
    violations = ledger_violations(data, root=root)
    assert violations == []


def test_every_component_and_node_type_is_mapped() -> None:
    root = Path(__file__).resolve().parents[1]
    ledger_path = root / "tests" / "workflow_coverage.toml"
    data = load_ledger(ledger_path)

    specs_dir = root / "specs"
    expected_components = {
        p.name for p in specs_dir.iterdir() if p.is_dir() and p.name != "roadmap"
    }
    mapped_components = {
        comp for wf in data.get("workflows", []) for comp in wf.get("components", [])
    }
    assert expected_components <= mapped_components

    expected_node_types = {member.value for member in NodeType}
    mapped_node_types = {nt["id"] for nt in data.get("node_types", [])}
    assert expected_node_types == mapped_node_types


def test_minimal_ledger_is_valid(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_duplicate_scenario_id_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"].append(dict(data["scenarios"][0]))
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("W01-S01" in v for v in violations)


def test_unknown_component_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["workflows"][0]["components"].append("nonexistent_component")
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("nonexistent_component" in v for v in violations)


def test_gap_without_active_package_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["state"] = "gap"
    data["scenarios"][0].pop("evidence", None)
    data["scenarios"][0].pop("package", None)
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("W01-S01" in v and "package" in v for v in violations)

    data["scenarios"][0]["package"] = "UNKNOWN-P99"
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("UNKNOWN-P99" in v for v in violations)


@pytest.mark.parametrize(
    ("mutation", "expected_violation"),
    [
        (
            lambda sc: sc.__setitem__("tests", []),
            "scenario W01-S01: covered state requires non-empty tests list",
        ),
        (
            lambda sc: sc.pop("evidence", None),
            "scenario W01-S01: covered state requires evidence table",
        ),
        (
            lambda sc: sc["evidence"].__setitem__("commit", "bad"),
            "scenario W01-S01: evidence commit 'bad' must match ^[0-9a-f]{7,40}$",
        ),
        (
            lambda sc: sc["evidence"].__setitem__("command", "   "),
            "scenario W01-S01: evidence command must be non-empty",
        ),
        (
            lambda sc: sc["evidence"].__setitem__("result", ""),
            "scenario W01-S01: evidence result must be non-empty",
        ),
    ],
)
def test_covered_diagnostics(tmp_path: Path, mutation: Any, expected_violation: str) -> None:
    data = _valid_ledger(tmp_path)
    mutation(data["scenarios"][0])
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert expected_violation in violations


def test_missing_test_function_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["tests/test_alpha.py::nonexistent_fn"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("nonexistent_fn" in v for v in violations)


def test_class_test_reference_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["tests/test_alpha.py::TestAlpha::test_in_class"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_skipped_test_reference_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["tests/test_alpha.py::test_skipped"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("test_skipped" in v for v in violations)


def test_adversarial_non_test_helper_function_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["tests/test_alpha.py::_helper"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("non-test helper function" in v for v in violations)


def test_adversarial_production_file_under_src_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["src/alpha.py::not_a_test"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("production file under src" in v for v in violations)


def test_adversarial_traversal_path_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["tests/../tests/test_alpha.py::test_ok"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("traversal" in v for v in violations)


def test_adversarial_absolute_path_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["/tests/test_alpha.py::test_ok"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("absolute" in v for v in violations)


def test_adversarial_file_outside_allowed_roots_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["specs/test_alpha.py::test_ok"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("outside the allowed roots" in v for v in violations)


def test_focused_frontend_file_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    beta_file = tmp_path / "frontend" / "src" / "__tests__" / "beta.test.ts"
    beta_file.write_text('it.only("beta title", () => {});\n', encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/beta.test.ts::beta title"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("beta.test.ts" in v for v in violations)


def test_frontend_title_reference_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/alpha.test.ts::alpha title"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_playwright_e2e_reference_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["tests"] = ["frontend/e2e/alpha.spec.ts::alpha e2e title"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_frontend_title_merely_elsewhere_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    gamma_file = tmp_path / "frontend" / "src" / "__tests__" / "gamma.test.ts"
    gamma_file.write_text('// alpha title\nconst title = "alpha title";\n', encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/gamma.test.ts::alpha title"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("alpha title" in v for v in violations)


def test_contract_empty_path_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["contract"] = "#anything"
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("empty path" in v for v in violations)


def test_contract_directory_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["contract"] = "specs/alpha"
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("directory" in v for v in violations)


def test_contract_traversal_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["contract"] = "specs/../specs/alpha/high-level.md#the-alpha-contract"
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("traversal" in v for v in violations)


def test_contract_missing_file_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["contract"] = "specs/alpha/missing.md#the-alpha-contract"
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("does not exist" in v for v in violations)


def test_contract_missing_anchor_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["contract"] = "specs/alpha/high-level.md#nonexistent-anchor"
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("nonexistent-anchor" in v for v in violations)


def test_contract_with_backticks_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["contract"] = "specs/alpha/high-level.md#the-alpha-contract"
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_not_applicable_requires_reason(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["scenarios"][0]["state"] = "not-applicable"
    data["scenarios"][0].pop("evidence", None)
    data["scenarios"][0]["reason"] = ""
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("W01-S01" in v and "reason" in v for v in violations)

    data["scenarios"][0]["reason"] = "valid reason"
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_supplemental_document_missing_from_workflow_documents_is_reported(
    tmp_path: Path,
) -> None:
    data = _valid_ledger(tmp_path)
    data["workflows"][0]["documents"] = ["specs/alpha/high-level.md"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("decision.md" in v for v in violations)


def test_unmapped_component_and_node_type_are_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    (tmp_path / "specs" / "unmapped").mkdir(parents=True, exist_ok=True)
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha", "missing_node_type"])
    assert any("unmapped" in v for v in violations)
    assert any("missing_node_type" in v for v in violations)


def test_class_level_skip_mark_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    test_file = tmp_path / "tests" / "test_alpha.py"
    skipped_class = "\n".join(
        [
            "",
            '@pytest.mark.skip(reason="whole class")',
            "class TestSkippedClass:",
            "    def test_inner(self) -> None:",
            "        pass",
            "",
        ]
    )
    test_file.write_text(test_file.read_text(encoding="utf-8") + skipped_class, encoding="utf-8")
    data["scenarios"][0]["tests"] = ["tests/test_alpha.py::TestSkippedClass::test_inner"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("TestSkippedClass::test_inner" in v and "debt site" in v for v in violations)


def test_missing_workflow_document_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["workflows"][0]["documents"].append("specs/alpha/missing.md")
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("workflow W01 document" in v and "missing.md" in v for v in violations)


def test_describe_frontend_title_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    suite_file = tmp_path / "frontend" / "src" / "__tests__" / "suite.test.ts"
    suite_file.write_text('test.describe("suite title", () => {});\n', encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/suite.test.ts::suite title"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("test title 'suite title' not found in it() or test() call" in v for v in violations)


def test_commented_frontend_titles_are_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    ghost_file = tmp_path / "frontend" / "src" / "__tests__" / "ghost.test.ts"
    ghost_file.write_text(
        '// it("ghost title", () => {});\n/* it("ghost block", () => {}); */\n',
        encoding="utf-8",
    )
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/ghost.test.ts::ghost title"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("test title 'ghost title' not found in it() or test() call" in v for v in violations)

    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/ghost.test.ts::ghost block"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("test title 'ghost block' not found in it() or test() call" in v for v in violations)


def test_each_frontend_title_reference_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    each_file = tmp_path / "frontend" / "src" / "__tests__" / "each.test.ts"
    each_file.write_text('it.each([1])("each title %s", () => {});\n', encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/each.test.ts::each title %s"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_scenario_contract_not_among_workflow_documents_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    data["workflows"][0]["documents"] = ["specs/alpha/decision.md"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert (
        "scenario W01-S01: contract file specs/alpha/high-level.md "
        "is not among workflow W01 documents" in violations
    )


def test_hook_frontend_title_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    hook_file = tmp_path / "frontend" / "e2e" / "hooks.spec.ts"
    hook_file.write_text('test.beforeEach("setup", async () => {});\n', encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/e2e/hooks.spec.ts::setup"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("'setup'" in v and "it() or test() call" in v for v in violations)


def test_extend_fixture_only_title_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    fx_file = tmp_path / "frontend" / "src" / "__tests__" / "fixture.test.ts"
    fx_file.write_text(
        'test.extend("fixture-only", async () => 1);\nit("real control", () => {});\n',
        encoding="utf-8",
    )
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/fixture.test.ts::fixture-only"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("'fixture-only'" in v and "it() or test() call" in v for v in violations)


def test_inline_extended_test_title_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    ext_file = tmp_path / "frontend" / "src" / "__tests__" / "extended.test.ts"
    ext_file.write_text(
        'test.extend({ fx: 1 })("extended title", async () => {});\n', encoding="utf-8"
    )
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/extended.test.ts::extended title"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_nested_each_factory_title_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    nested_file = tmp_path / "frontend" / "src" / "__tests__" / "nested.test.ts"
    nested_file.write_text(
        'it.each(TARGETS.map((t) => [t]))("nested %s", () => {});\n', encoding="utf-8"
    )
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/nested.test.ts::nested %s"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_parenthesised_string_in_factory_argument_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    paren_file = tmp_path / "frontend" / "src" / "__tests__" / "paren.test.ts"
    paren_file.write_text('it.each(["a)", "b("])("paren %s", () => {});\n', encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/paren.test.ts::paren %s"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_interstitial_comment_before_title_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    trivia_file = tmp_path / "frontend" / "src" / "__tests__" / "trivia.test.ts"
    trivia_file.write_text(
        'test(/* explanation */ "commented title" /* trailing */, () => {});\n',
        encoding="utf-8",
    )
    data["scenarios"][0]["tests"] = ["frontend/src/__tests__/trivia.test.ts::commented title"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_concatenated_title_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    concat_file = tmp_path / "frontend" / "src" / "__tests__" / "concat.test.ts"
    concat_file.write_text('test("claimed" + " suffix", () => {});\n', encoding="utf-8")
    for title in ("claimed", "claimed suffix"):
        data["scenarios"][0]["tests"] = [f"frontend/src/__tests__/concat.test.ts::{title}"]
        violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
        assert any(repr(title) in v and "it() or test() call" in v for v in violations)


def test_interpolated_template_title_is_reported(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    template_file = tmp_path / "frontend" / "e2e" / "template.spec.ts"
    template_file.write_text("test(`${count} rows align`, async () => {});\n", encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/e2e/template.spec.ts::${count} rows align"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("${count} rows align" in v and "it() or test() call" in v for v in violations)


def test_plain_template_literal_title_resolves(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    plain_file = tmp_path / "frontend" / "e2e" / "plain-template.spec.ts"
    plain_file.write_text("test(`plain template`, async () => {});\n", encoding="utf-8")
    data["scenarios"][0]["tests"] = ["frontend/e2e/plain-template.spec.ts::plain template"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_escaped_title_is_compared_by_collected_value(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    escaped_file = tmp_path / "frontend" / "src" / "__tests__" / "escaped.test.ts"
    escaped_file.write_text('it("3 \\u00d7 4 and \\"quoted\\"", () => {});\n', encoding="utf-8")
    collected = '3 \u00d7 4 and "quoted"'
    data["scenarios"][0]["tests"] = [f"frontend/src/__tests__/escaped.test.ts::{collected}"]
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []

    raw_spelling = '3 \\u00d7 4 and \\"quoted\\"'
    data["scenarios"][0]["tests"] = [f"frontend/src/__tests__/escaped.test.ts::{raw_spelling}"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("it() or test() call" in v for v in violations)


def test_perf_marked_module_cannot_witness_a_scenario(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    marked = tmp_path / "tests" / "test_marked.py"
    marked.write_text(
        "import pytest\n\npytestmark = pytest.mark.perf\n\n\ndef test_ok() -> None:\n    pass\n",
        encoding="utf-8",
    )
    data["scenarios"][0]["tests"] = ["tests/test_marked.py::test_ok"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("module-level perf mark" in v for v in violations)

    marked.write_text(
        "import pytest\n\npytestmark = [pytest.mark.slow, pytest.mark.perf]\n\n\n"
        "def test_ok() -> None:\n    pass\n",
        encoding="utf-8",
    )
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("module-level perf mark" in v for v in violations)

    marked.write_text(
        "import pytest\n\npytestmark = pytest.mark.slow\n\n\ndef test_ok() -> None:\n    pass\n",
        encoding="utf-8",
    )
    assert ledger_violations(data, root=tmp_path, node_types=["alpha"]) == []


def test_performance_directory_module_cannot_witness_a_scenario(tmp_path: Path) -> None:
    data = _valid_ledger(tmp_path)
    perf_dir = tmp_path / "tests" / "performance"
    perf_dir.mkdir()
    (perf_dir / "test_perf.py").write_text("def test_ok() -> None:\n    pass\n", encoding="utf-8")
    data["scenarios"][0]["tests"] = ["tests/performance/test_perf.py::test_ok"]
    violations = ledger_violations(data, root=tmp_path, node_types=["alpha"])
    assert any("tests/performance/" in v and "ordinary lane" in v for v in violations)


def test_property_manifest_lists_every_hypothesis_module() -> None:
    """The exploration lane runs scripts/property_test_files.txt; keep it complete."""
    root = Path(__file__).resolve().parents[1]
    expected = hypothesis_test_modules(root)
    listed = property_manifest_modules(root)
    assert expected, "no Hypothesis modules found under tests/"
    assert listed == expected, (
        f"missing from manifest: {sorted(expected - listed)}; "
        f"stale in manifest: {sorted(listed - expected)}"
    )
    for module in listed:
        assert (root / module).is_file(), module
