"""Call shapes under which a Polars expression method reads only literals.

``replace`` and ``replace_strict`` look each value up in a mapping. A literal
mapping keeps that per-row; a mapping taken from an expression or column reads
whole columns. The chunk classifier, the row-semantics classifier and the
planner's recompute analysis share these shape rules so they cannot drift.
"""

from __future__ import annotations

import ast

__all__ = [
    "is_literal_collection",
    "is_literal_scalar",
    "is_pl_dtype_reference",
    "replace_call_has_literal_mapping",
    "replace_strict_call_has_literal_mapping",
]


def is_literal_scalar(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        return isinstance(node.operand, ast.Constant)
    return isinstance(node, ast.Constant)


def is_literal_collection(node: ast.expr) -> bool:
    return isinstance(node, ast.List | ast.Tuple) and all(
        is_literal_scalar(element) for element in node.elts
    )


def is_pl_dtype_reference(node: ast.expr) -> bool:
    """``pl.Date`` and friends: a module-level dtype constant, not row data."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "pl"
    )


def replace_call_has_literal_mapping(call: ast.Call) -> bool:
    """Admit only a literal mapping: ``replace({old: new})`` or ``replace(old=[...], new=[...])``.

    A non-literal mapping (an expression or column) would let the replacement
    table depend on data outside the current chunk, so only constants are
    admitted.  The deprecated ``default=`` form is rejected: the pinned Polars
    only tolerates it with a deprecation warning, and an upgrade would remove
    it silently from under a proof.
    """
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
    if len(keywords) != len(call.keywords):
        return False
    if call.args:
        if len(call.args) != 1 or keywords:
            return False
        mapping = call.args[0]
        return isinstance(mapping, ast.Dict) and all(
            key is not None and is_literal_scalar(key) and is_literal_scalar(value)
            for key, value in zip(mapping.keys, mapping.values, strict=True)
        )
    if set(keywords) != {"old", "new"}:
        return False
    old, new = keywords["old"], keywords["new"]
    if not is_literal_collection(old) or not is_literal_collection(new):
        return False
    return len(old.elts) == len(new.elts)  # type: ignore[attr-defined]


def replace_strict_call_has_literal_mapping(call: ast.Call) -> bool:
    """Admit ``replace_strict`` with ``replace``'s literal mapping forms.

    A literal ``default=`` and a ``return_dtype=`` naming a Polars dtype are
    allowed beside it: an expression default or mapping could read other rows.
    """
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
    if len(keywords) != len(call.keywords):
        return False
    default = keywords.pop("default", None)
    if default is not None and not is_literal_scalar(default):
        return False
    return_dtype = keywords.pop("return_dtype", None)
    if return_dtype is not None and not is_pl_dtype_reference(return_dtype):
        return False
    mapping_only = ast.Call(
        func=call.func,
        args=call.args,
        keywords=[ast.keyword(arg=name, value=value) for name, value in keywords.items()],
    )
    return replace_call_has_literal_mapping(mapping_only)
