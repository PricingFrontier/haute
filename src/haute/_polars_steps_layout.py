"""Layout of the step renderer's statements as ``ruff format`` lays them out.

Each structured step renders one statement. A statement that does not fit in
:data:`LINE_WIDTH` columns is split the way ``ruff format`` splits it: the last
call's brackets break first, a list or dict that still does not fit puts one
entry per line with a trailing comma, a call chain with two or more links after
a call or parentheses breaks before each such link (ruff's fluent layout), a
binary expression breaks before its weakest operators, and a long name or
literal after ``=`` is parenthesised only when that makes it fit. Doubled
parentheses collapse to one pair, as ruff prints them. One choice is ours: a
broken call always puts one argument per line with a trailing comma, where
ruff would keep arguments that fit on one indented line together; ruff reads
that trailing comma as a magic trailing comma and keeps the layout. The result
is a fixed point of ``ruff format`` at line length 88 with quotes preserved;
the renderer writes quotes with ``repr``.

This is not a general formatter. It reads only the renderer's own
closed-vocabulary statements (names, numbers, strings, attribute and call
chains, lists, dicts, unary and binary operators, and parentheses) and refuses
anything else loudly. Free-code steps are authored code and never pass through
it.
"""

from __future__ import annotations

import io
import tokenize
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

__all__ = ["LINE_WIDTH", "layout_statement"]

#: ruff format's default line length.
LINE_WIDTH = 88
_INDENT = "    "

# ---------------------------------------------------------------------------
# Expression tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Atom:
    text: str


@dataclass(frozen=True, slots=True)
class _Paren:
    inner: _Node


@dataclass(frozen=True, slots=True)
class _Collection:
    open: str
    close: str
    entries: tuple[_Node, ...]


@dataclass(frozen=True, slots=True)
class _Pair:
    key: _Node
    value: _Node


@dataclass(frozen=True, slots=True)
class _Keyword:
    name: str
    value: _Node


@dataclass(frozen=True, slots=True)
class _Attr:
    value: _Node
    name: str


@dataclass(frozen=True, slots=True)
class _Call:
    func: _Node
    args: tuple[_Node, ...]


@dataclass(frozen=True, slots=True)
class _Unary:
    op: str
    operand: _Node


@dataclass(frozen=True, slots=True)
class _Binary:
    left: _Node
    op: str
    right: _Node


_Node: TypeAlias = (
    "_Atom | _Paren | _Collection | _Pair | _Keyword | _Attr | _Call | _Unary | _Binary"
)

#: Binding power of each binary operator, weakest first (Python's precedence).
_PRECEDENCE: dict[str, int] = {
    **dict.fromkeys(("==", "!=", "<", "<=", ">", ">="), 1),
    "|": 2,
    "^": 3,
    "&": 4,
    **dict.fromkeys(("<<", ">>"), 5),
    **dict.fromkeys(("+", "-"), 6),
    **dict.fromkeys(("*", "/", "//", "%", "@"), 7),
    "**": 8,
}
_UNARY = frozenset({"~", "-", "+"})
_IGNORED_TOKENS = frozenset({tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER})
_ATOM_TOKENS = frozenset({tokenize.NAME, tokenize.NUMBER, tokenize.STRING})


class _Parser:
    """A precedence-climbing parser for one rendered ``target = value`` statement."""

    def __init__(self, statement: str) -> None:
        self.statement = statement
        try:
            raw = list(tokenize.generate_tokens(io.StringIO(statement).readline))
        except (tokenize.TokenError, SyntaxError) as exc:
            raise self.error(str(exc)) from None
        self.tokens = [(tok.type, tok.string) for tok in raw if tok.type not in _IGNORED_TOKENS]
        self.pos = 0

    def error(self, detail: str) -> ValueError:
        return ValueError(f"Cannot lay out the rendered statement {self.statement!r}: {detail}.")

    def peek(self, offset: int = 0) -> tuple[int, str] | None:
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def at(self, text: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == tokenize.OP and token[1] == text

    def take(self, text: str | None = None) -> tuple[int, str]:
        token = self.peek()
        if token is None:
            raise self.error("the statement ends early")
        if text is not None and (token[0] != tokenize.OP or token[1] != text):
            raise self.error(f"expected {text!r}, found {token[1]!r}")
        self.pos += 1
        return token

    def statement_parts(self) -> tuple[str, _Node]:
        kind, target = self.take()
        if kind != tokenize.NAME:
            raise self.error("a statement assigns to a name")
        self.take("=")
        value = self.expression(0)
        rest = self.peek()
        if rest is not None:
            raise self.error(f"unexpected {rest[1]!r}")
        return target, value

    def expression(self, min_power: int) -> _Node:
        left = self.unary()
        while True:
            token = self.peek()
            if token is None or token[0] != tokenize.OP or token[1] == "**":
                return left
            power = _PRECEDENCE.get(token[1])
            if power is None or power <= min_power:
                return left
            self.take()
            left = _Binary(left, token[1], self.expression(power))

    def unary(self) -> _Node:
        token = self.peek()
        if token is not None and token[0] == tokenize.OP and token[1] in _UNARY:
            self.take()
            return _Unary(token[1], self.unary())
        base = self.primary()
        if self.at("**"):
            self.take()
            return _Binary(base, "**", self.unary())
        return base

    def primary(self) -> _Node:
        node = self.atom()
        while True:
            if self.at("."):
                self.take()
                kind, name = self.take()
                if kind != tokenize.NAME:
                    raise self.error(f"expected an attribute name, found {name!r}")
                node = _Attr(node, name)
            elif self.at("("):
                node = _Call(node, self.entries("(", ")", self.argument))
            else:
                return node

    def atom(self) -> _Node:
        token = self.peek()
        if token is None:
            raise self.error("the statement ends early")
        if token[0] in _ATOM_TOKENS:
            self.take()
            return _Atom(token[1])
        if self.at("("):
            self.take()
            inner = self.expression(0)
            self.take(")")
            # ruff prints a parenthesized expression with exactly one pair.
            return inner if isinstance(inner, _Paren) else _Paren(inner)
        if self.at("["):
            return _Collection("[", "]", self.entries("[", "]", lambda: self.expression(0)))
        if self.at("{"):
            return _Collection("{", "}", self.entries("{", "}", self.pair))
        raise self.error(f"unexpected {token[1]!r}")

    def entries(self, open_: str, close: str, entry: Callable[[], _Node]) -> tuple[_Node, ...]:
        self.take(open_)
        found: list[_Node] = []
        while not self.at(close):
            found.append(entry())
            if not self.at(close):
                self.take(",")
        self.take(close)
        return tuple(found)

    def argument(self) -> _Node:
        name, equals = self.peek(), self.peek(1)
        if name is not None and name[0] == tokenize.NAME and equals == (tokenize.OP, "="):
            self.pos += 2
            return _Keyword(name[1], self.expression(0))
        return self.expression(0)

    def pair(self) -> _Node:
        key = self.expression(0)
        self.take(":")
        return _Pair(key, self.expression(0))


# ---------------------------------------------------------------------------
# Document and printer (the Wadler/Prettier algorithm ruff's printer follows)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Group:
    """Printed flat when it fits on the line up to the next possible break."""

    contents: _Doc


@dataclass(frozen=True, slots=True)
class _Indent:
    contents: _Doc


@dataclass(frozen=True, slots=True)
class _Line:
    """A line break in a broken group; flat, a space (or nothing when soft)."""

    soft: bool


@dataclass(frozen=True, slots=True)
class _IfBreak:
    """Text printed only when the enclosing group breaks (a trailing comma)."""

    text: str


_Doc: TypeAlias = "str | _Group | _Indent | _Line | _IfBreak | list[_Doc]"
_SOFT = _Line(soft=True)
_SPACE = _Line(soft=False)


def _width(text: str) -> int:
    """Display columns, as ruff measures them: wide characters take two, marks none."""
    if text.isascii():
        return len(text)
    return sum(
        0
        if unicodedata.combining(char)
        else 2
        if unicodedata.east_asian_width(char) in ("W", "F")
        else 1
        for char in text
    )


def _fits(contents: _Doc, rest: list[tuple[int, bool, _Doc]], width: int) -> bool:
    """Whether *contents*, printed flat, and the rest up to its next break fit."""
    pending: list[tuple[bool, _Doc]] = [(False, contents)]
    rest_index = len(rest)
    while width >= 0:
        if not pending:
            if rest_index == 0:
                return True
            rest_index -= 1
            _indent, broken, item = rest[rest_index]
            pending.append((broken, item))
            continue
        broken, item = pending.pop()
        if isinstance(item, str):
            width -= _width(item)
        elif isinstance(item, list):
            pending.extend((broken, part) for part in reversed(item))
        elif isinstance(item, (_Group, _Indent)):
            pending.append((broken, item.contents))
        elif isinstance(item, _Line):
            if broken:
                return True
            width -= 0 if item.soft else 1
        elif broken:
            width -= _width(item.text)
    return False


def _print(doc: _Doc) -> str:
    out: list[str] = []
    column = 0
    stack: list[tuple[int, bool, _Doc]] = [(0, True, doc)]
    while stack:
        indent, broken, item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            column += _width(item)
        elif isinstance(item, list):
            stack.extend((indent, broken, part) for part in reversed(item))
        elif isinstance(item, _Indent):
            stack.append((indent + 1, broken, item.contents))
        elif isinstance(item, _Group):
            breaks = broken and not _fits(item.contents, stack, LINE_WIDTH - column)
            stack.append((indent, breaks, item.contents))
        elif isinstance(item, _Line):
            if broken:
                out.append("\n" + _INDENT * indent)
                column = len(_INDENT) * indent
            elif not item.soft:
                out.append(" ")
                column += 1
        elif broken:
            out.append(item.text)
            column += _width(item.text)
    return "".join(out)


# ---------------------------------------------------------------------------
# Tree to document, following ruff's rules for each expression
# ---------------------------------------------------------------------------


def _bracketed(open_: str, entries: tuple[_Node, ...], close: str) -> _Doc:
    """A bracketed, comma-separated sequence: flat, or one entry per line.

    A broken sequence of two or more entries ends with a trailing comma, which
    ruff reads as a magic trailing comma and keeps expanded.
    """
    if not entries:
        return open_ + close
    body: list[_Doc] = []
    for index, entry in enumerate(entries):
        if index:
            body.extend([",", _SPACE])
        body.append(_expression(entry))
    if len(entries) > 1:
        body.append(_IfBreak(","))
    return _Group([open_, _Indent([_SOFT, body]), _SOFT, close])


def _fluent_links(node: _Node) -> int:
    """Links of a call chain that follow a call or parentheses (ruff's count)."""
    links = 0
    while True:
        if isinstance(node, _Attr):
            if isinstance(node.value, _Paren):
                return links + 1
            if isinstance(node.value, _Call):
                links += 1
            node = node.value
        elif isinstance(node, _Call) and not isinstance(node.func, _Paren):
            node = node.func
        else:
            return links


def _chain(node: _Attr | _Call, fluent: bool) -> _Doc:
    """A call chain; fluent, each link after a call or parentheses may break."""
    if isinstance(node, _Call):
        func = node.func
        head = _chain(func, fluent) if isinstance(func, (_Attr, _Call)) else _expression(func)
        return [head, _bracketed("(", node.args, ")")]
    value = node.value
    head = _chain(value, fluent) if isinstance(value, (_Attr, _Call)) else _expression(value)
    breaks = [_SOFT] if fluent and isinstance(value, (_Call, _Paren)) else []
    return [head, *breaks, f".{node.name}"]


def _binary_operands(node: _Node, items: list[_Node | str]) -> None:
    if isinstance(node, _Binary):
        _binary_operands(node.left, items)
        items.append(node.op)
        _binary_operands(node.right, items)
    else:
        items.append(node)


def _binary_slice(items: list[_Node | str]) -> _Doc:
    """Operands split before the slice's weakest operators, each part grouped."""
    if len(items) == 1:
        only = items[0]
        assert not isinstance(only, str)
        return _expression(only)
    weakest = min(_PRECEDENCE[op] for op in items[1::2] if isinstance(op, str))
    parts: list[_Doc] = []
    start = 0
    for index in range(1, len(items), 2):
        op = items[index]
        assert isinstance(op, str)
        if _PRECEDENCE[op] == weakest:
            parts.extend([_Group(_binary_slice(items[start:index])), _SPACE, f"{op} "])
            start = index + 1
    parts.append(_Group(_binary_slice(items[start:])))
    return parts


def _expression(node: _Node) -> _Doc:
    """An expression inside brackets, where ruff may break it."""
    if isinstance(node, _Atom):
        return node.text
    if isinstance(node, _Paren):
        return _Group(["(", _Indent([_SOFT, _expression(node.inner)]), _SOFT, ")"])
    if isinstance(node, _Collection):
        return _bracketed(node.open, node.entries, node.close)
    if isinstance(node, _Pair):
        return [_expression(node.key), ": ", _expression(node.value)]
    if isinstance(node, _Keyword):
        return [node.name, "=", _expression(node.value)]
    if isinstance(node, (_Attr, _Call)):
        fluent = _fluent_links(node) >= 2
        chain = _chain(node, fluent)
        return _Group(chain) if fluent else chain
    if isinstance(node, _Unary):
        return [node.op, _expression(node.operand)]
    items: list[_Node | str] = []
    _binary_operands(node, items)
    return _Group(_binary_slice(items))


def layout_statement(statement: str) -> str:
    """Lay out one rendered single-line statement as ``ruff format`` would.

    The statement's text is unchanged apart from line breaks, indentation,
    trailing commas in broken sequences and collapsed doubled parentheses.
    """
    target, value = _Parser(statement).statement_parts()
    head = f"{target} = "
    if isinstance(value, (_Attr, _Call)):
        # Outside brackets ruff never lays a chain out fluently and adds no
        # parentheses around a call: the last call's brackets break first.
        return _print([head, _chain(value, fluent=False)])
    if isinstance(value, _Atom) or (isinstance(value, _Unary) and isinstance(value.operand, _Atom)):
        text = _print(_expression(value))
        if _width(head + text) <= LINE_WIDTH or len(_INDENT) + _width(text) > LINE_WIDTH:
            return head + text
        # ruff's best fit: parentheses only when they make the value fit.
        return f"{head}(\n{_INDENT}{text}\n)"
    raise ValueError(f"Cannot lay out the rendered statement {statement!r}: unexpected value.")
