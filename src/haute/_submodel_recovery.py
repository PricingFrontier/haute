"""Literal submodel identity evidence; never strict parsing or migration authority."""

from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass


@dataclass(frozen=True)
class SubmodelRegistrationEvidence:
    path: str
    name: str
    instance_id: str | None
    definition_id: str | None
    call: ast.Call


def submodel_registration_evidence(statement: ast.stmt) -> SubmodelRegistrationEvidence | None:
    """Identify a standalone literal registration without accepting its old schema.

    Conflicting identities, computed values and chained statements deliberately have
    no repairable identity. The caller still records their source diagnostic.
    """
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    call = statement.value
    if not (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "pipeline"
        and call.func.attr == "submodel"
        and len(call.args) <= 2
    ):
        return None
    values: dict[str, str] = {}
    for argument in call.keywords:
        if argument.arg is None or argument.arg in values:
            return None
        if not isinstance(argument.value, ast.Constant) or not isinstance(
            argument.value.value, str
        ):
            return None
        values[argument.arg] = argument.value.value
    for index, field in enumerate(("file", "name")):
        if len(call.args) <= index:
            continue
        positional = call.args[index]
        if (
            field in values
            or not isinstance(positional, ast.Constant)
            or not isinstance(positional.value, str)
        ):
            return None
        values[field] = positional.value
    path = values.get("file")
    name = values.get("name", values.get("alias"))
    if (
        not path
        or path != path.strip()
        or not name
        or not name.isidentifier()
        or keyword.iskeyword(name)
    ):
        return None
    if "alias" in values and values["alias"] != name:
        return None
    return SubmodelRegistrationEvidence(
        path=path,
        name=name,
        instance_id=values.get("instance_id"),
        definition_id=values.get("definition_id"),
        call=call,
    )
