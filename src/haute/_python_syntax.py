"""Small LibCST boundary for formatting-preserving Python source operations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar, cast

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from haute.errors import HauteError

_CSTNodeT = TypeVar("_CSTNodeT", bound=cst.CSTNode)


@dataclass(frozen=True, slots=True)
class MethodCallSite:
    """The source span of a call whose callee is an attribute access."""

    name: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class SourceNodeReplacement:
    """Exact character-based source coordinates for one expression or function."""

    start_line: int
    start_column: int
    end_line: int
    end_column: int
    source: str
    is_function: bool = False


class _SourceNodeReplacer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, replacements: Sequence[SourceNodeReplacement]) -> None:
        self.replacements = replacements
        self.applied: set[int] = set()

    def on_leave(self, original_node: _CSTNodeT, updated_node: _CSTNodeT) -> _CSTNodeT:
        if not isinstance(original_node, (cst.BaseExpression, cst.FunctionDef)):
            return updated_node
        position = self.get_metadata(PositionProvider, original_node)
        for index, replacement in enumerate(self.replacements):
            if index in self.applied or (
                position.start.line,
                position.start.column,
                position.end.line,
                position.end.column,
            ) != (
                replacement.start_line,
                replacement.start_column,
                replacement.end_line,
                replacement.end_column,
            ):
                continue
            if replacement.is_function:
                if not isinstance(original_node, cst.FunctionDef):
                    continue
                statements = cst.parse_module(replacement.source.rstrip("\n") + "\n").body
                if len(statements) != 1 or not isinstance(statements[0], cst.FunctionDef):
                    raise StructuredSyntaxError("replacement_function_invalid")
                result: cst.CSTNode = statements[0].with_changes(
                    leading_lines=original_node.leading_lines
                )
            else:
                if not isinstance(original_node, cst.BaseExpression):
                    continue
                result = cst.parse_expression(replacement.source)
            self.applied.add(index)
            # Both replacements have been checked for the original syntax role;
            # libcst's generic visitor return type cannot express that refinement.
            return cast(_CSTNodeT, result)
        return updated_node


def replace_source_nodes(source: str, replacements: Sequence[SourceNodeReplacement]) -> str:
    """Replace exact valid-source syntax nodes, preserving untouched syntax and trivia."""
    try:
        wrapper = MetadataWrapper(cst.parse_module(source))
        transformer = _SourceNodeReplacer(replacements)
        result = wrapper.visit(transformer)
    except cst.ParserSyntaxError as exc:
        raise _syntax_error("source_syntax_invalid", exc) from exc
    if len(transformer.applied) != len(replacements):
        raise StructuredSyntaxError("replacement_source_span_unmatched")
    return result.code


class StructuredSyntaxError(HauteError):
    """A value-free failure from the valid-Python structured syntax boundary."""

    def __init__(
        self,
        reason: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        context: dict[str, int | str] = {"reason": reason}
        if line is not None:
            context["line"] = line
        if column is not None:
            context["column"] = column
        super().__init__("structured Python syntax operation failed", **context)


def _syntax_error(reason: str, exc: cst.ParserSyntaxError) -> StructuredSyntaxError:
    return StructuredSyntaxError(reason, line=exc.raw_line, column=exc.raw_column)


class _MethodCallVisitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self) -> None:
        self.sites: list[MethodCallSite] = []

    def visit_Call(self, node: cst.Call) -> None:  # noqa: N802
        if not isinstance(node.func, cst.Attribute):
            return
        position = self.get_metadata(PositionProvider, node)
        self.sites.append(
            MethodCallSite(
                name=node.func.attr.value,
                start_line=position.start.line,
                start_column=position.start.column,
                end_line=position.end.line,
                end_column=position.end.column,
            )
        )


def method_call_sites(source: str) -> tuple[MethodCallSite, ...]:
    """Return exact attribute-call sites in *source*, excluding trivia/lookalikes."""
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        raise _syntax_error("source_syntax_invalid", exc) from exc
    visitor = _MethodCallVisitor()
    MetadataWrapper(module).visit(visitor)
    return tuple(
        sorted(
            visitor.sites,
            key=lambda site: (site.start_line, site.start_column, site.end_line, site.end_column),
        )
    )
