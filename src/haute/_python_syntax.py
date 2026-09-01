"""Small LibCST boundary for formatting-preserving Python source operations."""

from __future__ import annotations

from dataclasses import dataclass

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from haute.errors import HauteError


@dataclass(frozen=True, slots=True)
class MethodCallSite:
    """The source span of a call whose callee is an attribute access."""

    name: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int


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


def _parse_keyword(keyword_source: str) -> cst.Arg:
    """Parse exactly one keyword argument without interpreting its value."""
    try:
        module = cst.parse_module(f"_haute_keyword_target({keyword_source})\n")
    except cst.ParserSyntaxError as exc:
        raise _syntax_error("keyword_syntax_invalid", exc) from exc
    statement = module.body[0]
    assert isinstance(statement, cst.SimpleStatementLine)
    expression = statement.body[0]
    assert isinstance(expression, cst.Expr)
    call = expression.value
    assert isinstance(call, cst.Call)
    if len(call.args) != 1:
        raise StructuredSyntaxError("keyword_syntax_invalid")
    arg = call.args[0]
    if arg.keyword is None or arg.star:
        raise StructuredSyntaxError("keyword_syntax_invalid")
    return arg


def _decorator_call(expression: cst.BaseExpression) -> cst.Call | None:
    return expression if isinstance(expression, cst.Call) else None


def _decorator_attribute(expression: cst.BaseExpression) -> cst.Attribute | None:
    candidate = expression.func if isinstance(expression, cst.Call) else expression
    return candidate if isinstance(candidate, cst.Attribute) else None


def _is_eligible(expression: cst.BaseExpression, roots: frozenset[str]) -> bool:
    attribute = _decorator_attribute(expression)
    return (
        attribute is not None
        and isinstance(attribute.value, cst.Name)
        and attribute.value.value in roots
    )


class _FunctionDecoratorFinder(cst.CSTVisitor):
    def __init__(self, decorator_roots: frozenset[str]) -> None:
        self.decorator_roots = decorator_roots
        self.target: cst.Decorator | None = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:  # noqa: N802
        if self.target is not None:
            return False
        for decorator in node.decorators:
            if _is_eligible(decorator.decorator, self.decorator_roots):
                self.target = decorator
                return False
        return None


class _DecoratorKeywordInjector(cst.CSTTransformer):
    def __init__(self, keyword: cst.Arg, target: cst.Decorator) -> None:
        assert keyword.keyword is not None
        self.keyword = keyword
        self.keyword_name = keyword.keyword.value
        self.target = target
        self.changed = False
        self.duplicate = False

    def leave_Decorator(  # noqa: N802
        self,
        original_node: cst.Decorator,
        updated_node: cst.Decorator,
    ) -> cst.Decorator:
        if self.changed or original_node is not self.target:
            return updated_node
        original_expression = original_node.decorator
        call = _decorator_call(original_expression)
        if call is not None:
            for arg in call.args:
                if arg.keyword is not None and arg.keyword.value == self.keyword_name:
                    self.duplicate = True
                    return updated_node
            new_expression = self._append_keyword(call)
        else:
            assert isinstance(original_expression, cst.Attribute)
            new_expression = cst.Call(func=original_expression, args=(self.keyword,))
        self.changed = True
        return updated_node.with_changes(decorator=new_expression)

    def _append_keyword(self, call: cst.Call) -> cst.Call:
        multiline = isinstance(call.whitespace_before_args, cst.ParenthesizedWhitespace)
        trailing_comma = bool(call.args) and isinstance(call.args[-1].comma, cst.Comma)
        keyword = self.keyword
        if multiline or trailing_comma:
            trailing = self._closing_whitespace(call)
            keyword = keyword.with_changes(comma=cst.Comma(), whitespace_after_arg=trailing)
        if not call.args:
            return call.with_changes(args=(keyword,))
        args = list(call.args)
        if isinstance(args[-1].comma, cst.Comma):
            old_whitespace = args[-1].comma.whitespace_after
            separator: cst.BaseParenthesizableWhitespace
            if isinstance(old_whitespace, cst.ParenthesizedWhitespace):
                initial_whitespace = call.whitespace_before_args
                assert isinstance(initial_whitespace, cst.ParenthesizedWhitespace)
                separator = old_whitespace.with_changes(last_line=initial_whitespace.last_line)
            else:
                assert isinstance(old_whitespace, cst.SimpleWhitespace)
                separator = old_whitespace if old_whitespace.value else cst.SimpleWhitespace(" ")
            args[-1] = args[-1].with_changes(
                comma=args[-1].comma.with_changes(whitespace_after=separator)
            )
        if args[-1].comma is cst.MaybeSentinel.DEFAULT:
            whitespace = args[-1].whitespace_after_arg if multiline else cst.SimpleWhitespace(" ")
            args[-1] = args[-1].with_changes(comma=cst.Comma(whitespace_after=whitespace))
        return call.with_changes(args=(*args, keyword))

    @staticmethod
    def _closing_whitespace(call: cst.Call) -> cst.BaseParenthesizableWhitespace:
        if not call.args or call.args[-1].comma is cst.MaybeSentinel.DEFAULT:
            if not call.args:
                return call.whitespace_before_args
            return call.args[-1].whitespace_after_arg
        comma = call.args[-1].comma
        assert isinstance(comma, cst.Comma)
        whitespace = comma.whitespace_after
        if not isinstance(whitespace, cst.ParenthesizedWhitespace):
            return whitespace
        return cst.ParenthesizedWhitespace(
            first_line=cst.TrailingWhitespace(newline=whitespace.first_line.newline),
            empty_lines=whitespace.empty_lines,
            indent=whitespace.indent,
            last_line=whitespace.last_line,
        )


def inject_decorator_keyword(
    source: str,
    keyword_source: str,
    *,
    decorator_roots: frozenset[str],
) -> str:
    """Add one keyword to the first eligible function decorator in *source*."""
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        raise _syntax_error("source_syntax_invalid", exc) from exc
    keyword = _parse_keyword(keyword_source)
    finder = _FunctionDecoratorFinder(decorator_roots)
    module.visit(finder)
    if finder.target is None:
        raise StructuredSyntaxError("decorator_not_found")
    injector = _DecoratorKeywordInjector(keyword, finder.target)
    updated = module.visit(injector)
    if injector.duplicate:
        raise StructuredSyntaxError("decorator_keyword_exists")
    if not injector.changed:
        raise StructuredSyntaxError("decorator_not_found")
    return updated.code
