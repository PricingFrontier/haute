"""Requests are bounded: every free string, list and mapping a request body carries has a
limit, so an over-long value is refused with a 422 before a route acts on it."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from haute.schemas import (
    MAX_REQUEST_ITEMS,
    WorkbenchAppPriceRequest,
    WorkbenchAppQuoteChange,
    WorkbenchAppQuoteCreateRequest,
    WorkbenchAppQuoteNote,
    WorkbenchAppQuoteReviseRequest,
)

#: The documents a request carries whole: a pipeline graph with its constants, and a
#: workbench form. They are what the project's files hold, checked as those files are.
_DOCUMENTS = frozenset({"PipelineGraph", "GlobalConstant", "FormSpec"})


def _unbounded(schema: dict[str, Any], defs: dict[str, Any], at: str) -> list[str]:
    """Each string, list and mapping in ``schema`` with no limit, by where it is."""
    found: list[str] = []

    def walk(node: Any, at: str, seen: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            # A schema used both ways is named PipelineGraph-Input in the OpenAPI document.
            if name.split("-")[0] not in _DOCUMENTS and name not in seen:
                walk(defs[name], f"{at}<{name}>", seen | {name})
            return
        kind = node.get("type")
        if kind == "string" and not {"maxLength", "enum", "const"} & node.keys():
            found.append(f"{at}: a string with no maxLength")
        if kind == "array":
            if "maxItems" not in node:
                found.append(f"{at}: a list with no maxItems")
            walk(node.get("items"), f"{at}[]", seen)
            for index, item in enumerate(node.get("prefixItems", [])):
                walk(item, f"{at}[{index}]", seen)
        values = node.get("additionalProperties")
        if kind == "object" and (isinstance(values, dict) or values is True):
            if "maxProperties" not in node:
                found.append(f"{at}: a mapping with no maxProperties")
            if not {"maxLength", "enum", "const"} & node.get("propertyNames", {}).keys():
                found.append(f"{at}: a mapping whose keys have no maxLength")
            walk(values, f"{at}{{}}", seen)
        for name, field in (node.get("properties") or {}).items():
            walk(field, f"{at}.{name}", seen)
        for combinator in ("anyOf", "oneOf", "allOf"):
            for option in node.get(combinator, []):
                walk(option, at, seen)

    walk(schema, at, frozenset())
    return found


def _subclasses(model: type[BaseModel]) -> list[type[BaseModel]]:
    return [model, *(sub for child in model.__subclasses__() for sub in _subclasses(child))]


def test_every_request_body_bounds_its_strings_lists_and_mappings() -> None:
    from haute.server import app

    spec = app.openapi()
    components = spec["components"]["schemas"]
    found: list[str] = []
    walked: list[str] = []
    for route, operations in spec["paths"].items():
        for method, operation in operations.items():
            for media in operation.get("requestBody", {}).get("content", {}).values():
                walked.append(f"{method.upper()} {route}")
                found += _unbounded(media["schema"], components, walked[-1])
    # The underwriter app reads its bodies itself, so its models are named here.
    app_models: list[type[BaseModel]] = [
        WorkbenchAppPriceRequest,
        WorkbenchAppQuoteCreateRequest,
        WorkbenchAppQuoteReviseRequest,
        WorkbenchAppQuoteNote,
        *_subclasses(WorkbenchAppQuoteChange),
    ]
    for model in app_models:
        schema = model.model_json_schema()
        found += _unbounded(schema, schema.get("$defs", {}), model.__name__)

    assert {"POST /api/rating/levels", "POST /api/pipeline/save"} <= set(walked)
    assert found == []


_COLUMNS = [{"name": "premium", "dtype": "Float64"}]


@pytest.mark.parametrize(
    ("route", "body", "loc", "kind"),
    [
        pytest.param(
            "/api/rating/levels",
            {"graph": {}, "node_id": "rating", "columns": ["c" * 513]},
            ["body", "columns", 0],
            "string_too_long",
            id="a level column",
        ),
        pytest.param(
            "/api/explore/relationships",
            {"graph": {}, "node_id": "explore", "features": ["f" * 513]},
            ["body", "features", 0],
            "string_too_long",
            id="a relationship feature",
        ),
        pytest.param(
            "/api/explore/relationships",
            {"graph": {}, "node_id": "explore", "key_columns": ["k" * 513]},
            ["body", "key_columns", 0],
            "string_too_long",
            id="a key column",
        ),
        pytest.param(
            "/api/pipeline/polars-steps/free-code-columns",
            {
                "node_id": "rated",
                "steps": [],
                "input_names": ["quotes"],
                "start": "input",
                "input_columns": {"quotes": _COLUMNS * (MAX_REQUEST_ITEMS + 1)},
                "frame_columns": [],
            },
            ["body", "input_columns", "quotes"],
            "too_long",
            id="an input's columns",
        ),
        pytest.param(
            "/api/pipeline/polars-steps/free-code-columns",
            {
                "node_id": "rated",
                "steps": [],
                "input_names": ["quotes"],
                "start": "input",
                "input_columns": {"quotes": [{"name": "p" * 513, "dtype": "Float64"}]},
                "frame_columns": [],
            },
            ["body", "input_columns", "quotes", 0, "name"],
            "string_too_long",
            id="an input column's name",
        ),
    ],
)
def test_an_over_long_nested_value_is_refused_before_the_route_acts(
    client: TestClient, route: str, body: dict[str, Any], loc: list[Any], kind: str
) -> None:
    """The request's validation answers, naming the one value: the route never ran."""
    response = client.post(route, json=body)

    assert response.status_code == 422
    assert [(error["loc"], error["type"]) for error in response.json()["detail"]] == [(loc, kind)]


def test_the_sheets_values_are_bounded_to_the_cell_and_stay_strict() -> None:
    def price(values: dict[str, Any]) -> None:
        WorkbenchAppPriceRequest.model_validate(
            {"form_revision": "r1", "release": "rel", "values": values}
        )

    price({"policy": [{"age": "40", "smoker": False}]})
    for values, loc in (
        ({"policy": [{}] * (MAX_REQUEST_ITEMS + 1)}, ("values", "policy")),
        ({"policy": [{"a" * 513: "40"}]}, ("values", "policy", 0, "a" * 513, "[key]")),
        ({"policy": [{"age": 40}]}, ("values", "policy", 0, "age")),
    ):
        with pytest.raises(ValidationError) as caught:
            price(values)
        assert caught.value.errors()[0]["loc"][: len(loc)] == loc
