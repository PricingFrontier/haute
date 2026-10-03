"""The names support code binds, and the collisions they make.

Support code is what a generated module runs before its node functions: the
root preamble, each submodel preamble (appended to the parent's when its
instances expand), the preserved blocks, and every ``utility.<module>`` they
star-import. This module inventories the names it binds statically, with
provenance (what each binding binds), without importing anything, and reports:

- a node function name or input binding equal to a support-code binding,
  which rebinds the helper for every other node when the file runs on its own
  while the canvas, running each body against the preamble, calls the helper;
- one name bound by two support-code sources to different objects, where
  only the last binding is seen;
- a support-code binding of a reserved name, apart from the generated
  module's own ``import haute`` and ``import polars as pl``;
- anything that keeps the inventory from being complete, naming the
  statement: a computed ``__all__``, a binding under ``if``/``try`` (or any
  other block) at module level in a star-imported utility, a star import of a
  module outside ``utility``, or a star import of a utility file that does
  not exist.

Supported forms are ``import`` and ``from ... import`` (with aliases),
top-level ``def``, ``class`` and assignment targets, and a star import of
``utility.<module>``, resolved by parsing that file under the same forms,
recursively, with cycle detection. A utility module exports its literal
``__all__`` or, without one, its top-level names without a leading
underscore. Re-importing one object (``import polars as pl`` in two places)
is one provenance; so is the same definition written twice.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from haute._executable_names import (
    RESERVED_NAMES,
    ROOT_MODULE,
    NameParty,
    NameViolation,
    _edge_bindings,
    _mapping_bindings,
    _named_parties,
    _port_bindings,
    executable_name_violations,
)
from haute._io import read_user_text
from haute._types import PipelineGraph

#: Reads ``utility/<module>.py`` as the executor would resolve it, or ``None``
#: when no such file exists. Nothing else is read, and nothing is imported.
UtilityReader = Callable[[str], "str | None"]

#: The generated module's own bindings of reserved names.
_CANONICAL_RESERVED = {
    "haute": ("module", "haute"),
    "pl": ("module", "polars"),
}

_UTILITY_PREFIX = "utility."


@dataclass(frozen=True)
class SupportBinding:
    """One name support code binds: what it binds and where."""

    name: str
    provenance: tuple[str, ...]
    source: str
    line: int

    def where(self) -> str:
        return f"{self.source} (line {self.line})"


@dataclass
class SupportInventory:
    """Every support-code binding, and every statement the inventory could not read."""

    bindings: list[SupportBinding] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def _import_bindings(stmt: ast.Import | ast.ImportFrom) -> Iterator[tuple[str, tuple[str, ...]]]:
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            if alias.asname is not None:
                yield alias.asname, ("module", alias.name)
            else:
                top = alias.name.split(".", 1)[0]
                yield top, ("module", top)
        return
    module = "." * stmt.level + (stmt.module or "")
    for alias in stmt.names:
        yield alias.asname or alias.name, ("from", module, alias.name)


def _target_names(target: ast.expr) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _binds_anything(stmt: ast.stmt) -> bool:
    """Whether a compound statement binds a name at module level."""
    for node in ast.walk(stmt):
        if node is not stmt and isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            return True
        if isinstance(node, ast.Import | ast.ImportFrom):
            return True
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            return True
    return False


def _literal_all(value: ast.expr) -> list[str] | None:
    if not isinstance(value, ast.List | ast.Tuple):
        return None
    names = [element.value for element in value.elts if isinstance(element, ast.Constant)]
    if len(names) != len(value.elts) or not all(isinstance(name, str) for name in names):
        return None
    return [str(name) for name in names]


class _Inventory:
    def __init__(self, read_utility: UtilityReader) -> None:
        self.read_utility = read_utility
        self.result = SupportInventory()
        self._exports: dict[str, list[SupportBinding]] = {}
        self._resolving: list[str] = []

    def source(self, text: str, label: str, *, utility: bool = False) -> list[SupportBinding]:
        """Inventory one source's bindings; *utility* applies a utility file's stricter rules."""
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            self.result.unsupported.append(f"{label} is not valid Python (line {exc.lineno}).")
            return []
        bindings: list[SupportBinding] = []
        for stmt in tree.body:
            bindings.extend(self._statement(stmt, label, utility=utility))
        return bindings

    def _statement(self, stmt: ast.stmt, label: str, *, utility: bool) -> Iterator[SupportBinding]:
        line = stmt.lineno

        def bound(name: str, provenance: tuple[str, ...]) -> SupportBinding:
            return SupportBinding(name=name, provenance=provenance, source=label, line=line)

        if isinstance(stmt, ast.ImportFrom) and any(alias.name == "*" for alias in stmt.names):
            yield from self._star_import(stmt, label)
        elif isinstance(stmt, ast.Import | ast.ImportFrom):
            for name, provenance in _import_bindings(stmt):
                yield bound(name, provenance)
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield bound(stmt.name, ("define", ast.dump(stmt)))
        elif isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                for name in _target_names(target):
                    if name == "__all__" and not (
                        isinstance(stmt, ast.Assign | ast.AnnAssign)
                        and stmt.value is not None
                        and _literal_all(stmt.value) is not None
                    ):
                        self.result.unsupported.append(
                            f"{label} line {line} computes `__all__`; write it as a literal "
                            "list or tuple of names so they can be checked."
                        )
                    yield bound(name, ("define", ast.dump(stmt)))
        elif _binds_anything(stmt):
            if utility:
                self.result.unsupported.append(
                    f"{label} line {line} binds names inside a block at module level, which "
                    "cannot be checked; define them at the top level."
                )
            else:
                # A preamble's block binds whatever either branch binds.
                for inner in ast.iter_child_nodes(stmt):
                    if isinstance(inner, ast.stmt):
                        yield from self._statement(inner, label, utility=utility)
                for handler in getattr(stmt, "handlers", []):
                    for inner in handler.body:
                        yield from self._statement(inner, label, utility=utility)

    def _star_import(self, stmt: ast.ImportFrom, label: str) -> list[SupportBinding]:
        module = "." * stmt.level + (stmt.module or "")
        if stmt.level or not module.startswith(_UTILITY_PREFIX):
            self.result.unsupported.append(
                f"{label} line {stmt.lineno} star-imports `{module}`; only "
                "`from utility.<module> import *` can be checked. Import the names you use."
            )
            return []
        return self._utility_exports(module[len(_UTILITY_PREFIX) :], label, stmt.lineno)

    def _utility_exports(self, module: str, label: str, line: int) -> list[SupportBinding]:
        if module in self._exports:
            return self._exports[module]
        source_label = f"utility/{module.replace('.', '/')}.py"
        if module in self._resolving:
            cycle = " -> ".join([*self._resolving, module])
            self.result.unsupported.append(f"Utility star imports form a cycle: {cycle}.")
            return []
        text = self.read_utility(module)
        if text is None:
            self.result.unsupported.append(
                f"{label} line {line} star-imports `utility.{module}`, but {source_label} "
                "does not exist."
            )
            self._exports[module] = []
            return []
        self._resolving.append(module)
        try:
            bindings = self.source(text, source_label, utility=True)
        finally:
            self._resolving.pop()
        # Only what the module exports reaches the importing namespace.
        exports = self._exported(text, bindings, source_label)
        self._exports[module] = exports
        return exports

    def _exported(
        self, text: str, bindings: list[SupportBinding], label: str
    ) -> list[SupportBinding]:
        declared: list[str] | None = None
        for stmt in ast.parse(text).body:
            if isinstance(stmt, ast.Assign | ast.AnnAssign) and stmt.value is not None:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                    declared = _literal_all(stmt.value)
        last: dict[str, SupportBinding] = {}
        for binding in bindings:
            last[binding.name] = binding
        if declared is None:
            return [b for name, b in last.items() if not name.startswith("_")]
        missing = [name for name in declared if name not in last]
        if missing:
            self.result.unsupported.append(
                f"{label} lists {', '.join(repr(n) for n in missing)} in `__all__` without "
                "binding them."
            )
        return [last[name] for name in declared if name in last]


def support_code_inventory(graph: PipelineGraph, read_utility: UtilityReader) -> SupportInventory:
    """Every binding the support code of *graph* makes, with provenance."""
    inventory = _Inventory(read_utility)
    sources = [(graph.preamble or "", "the preamble")]
    sources.extend(
        (definition.graph.preamble or "", f"submodel {definition_id!r}'s preamble")
        for definition_id, definition in (graph.submodels or {}).items()
    )
    sources.extend(
        (block, f"preserved block {index + 1}")
        for index, block in enumerate(graph.preserved_blocks or [])
    )
    for text, label in sources:
        if text.strip():
            inventory.result.bindings.extend(inventory.source(text, label))
    return inventory.result


def _describe(provenance: tuple[str, ...]) -> str:
    kind = provenance[0]
    if kind == "module":
        return f"module `{provenance[1]}`"
    if kind == "from":
        return f"`{provenance[2]}` from `{provenance[1]}`"
    return "a definition"


def support_code_violations(
    graph: PipelineGraph, read_utility: UtilityReader
) -> list[NameViolation]:
    """Every collision between node names, inputs and support code, and within support code."""
    inventory = support_code_inventory(graph, read_utility)
    violations = [
        NameViolation(kind="support_unsupported", name="", parties=(), origin=problem)
        for problem in dict.fromkeys(inventory.unsupported)
    ]
    by_name: dict[str, list[SupportBinding]] = {}
    for binding in inventory.bindings:
        by_name.setdefault(binding.name, []).append(binding)

    for name, bindings in by_name.items():
        if name in RESERVED_NAMES:
            for binding in bindings:
                if binding.provenance != _CANONICAL_RESERVED.get(name):
                    violations.append(
                        NameViolation(
                            kind="support_reserved",
                            name=name,
                            parties=(),
                            origin=binding.where(),
                        )
                    )
        sources: dict[str, set[tuple[str, ...]]] = {}
        for binding in bindings:
            sources.setdefault(binding.source, set()).add(binding.provenance)
        provenances = {p for found in sources.values() for p in found}
        if len(sources) > 1 and len(provenances) > 1:
            places = " and ".join(
                f"{binding.where()} to {_describe(binding.provenance)}" for binding in bindings
            )
            violations.append(
                NameViolation(kind="support_conflict", name=name, parties=(), origin=places)
            )

    helpers = {name: bindings[-1] for name, bindings in by_name.items()}
    function_names: set[str] = set()
    for name, party in _named_parties(graph):
        function_names.add(name)
        helper = helpers.get(name)
        if helper is not None:
            violations.append(
                NameViolation(
                    kind="support_collision", name=name, parties=(party,), origin=helper.where()
                )
            )
    # An input named after a node repeats that node's collision, reported above.
    unreported = {name: b for name, b in helpers.items() if name not in function_names}
    violations.extend(_input_support_collisions(graph, unreported))
    return violations


def _input_support_collisions(
    graph: PipelineGraph, helpers: dict[str, SupportBinding]
) -> Iterator[NameViolation]:
    ports = _port_bindings(graph)
    modules = [(ROOT_MODULE, graph)]
    modules.extend((did, definition.graph) for did, definition in (graph.submodels or {}).items())
    for module, scoped in modules:
        edges = _edge_bindings(scoped)
        for node in scoped.nodes:
            names = [
                *(name for name, _origin in edges.get(node.id, [])),
                *(name for name, _origin in _mapping_bindings(node)),
                *ports.get((module, node.id), []),
            ]
            for name in dict.fromkeys(names):
                binding = helpers.get(name)
                if binding is None:
                    continue
                party = NameParty(node_id=node.id, label=node.data.label, module=module)
                yield NameViolation(
                    kind="support_input", name=name, parties=(party,), origin=binding.where()
                )


def utility_reader(*directories: Path) -> UtilityReader:
    """Read ``utility/<module>.py`` from the first of *directories* holding it.

    The executor searches the pipeline directory, then the project root, for
    the ``utility`` package; this reads the same files and imports nothing.
    """

    def read(module: str) -> str | None:
        relative = Path("utility", *module.split(".")).with_suffix(".py")
        for directory in directories:
            candidate = directory / relative
            if candidate.is_file():
                return read_user_text(candidate)
        return None

    return read


def name_violations(graph: PipelineGraph, read_utility: UtilityReader) -> list[NameViolation]:
    """Every executable-name and support-code violation in *graph*."""
    return [*executable_name_violations(graph), *support_code_violations(graph, read_utility)]
