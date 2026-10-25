"""A flat Quote Input's served request is typed by the manifest's input schema (DEP-R05).

Every path a request reaches (the container's single quote, its batch worker and the
Databricks model) frames it with ``frame_rows`` against the schema the deploy recorded from
the sample, never by inference from the JSON values: a value its column's type does not hold
exactly is refused naming the column and the quote, nothing is coerced or truncated, and a
missing value is a null of the declared type (specs/deploy).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._types import PipelineGraph
from haute.deploy._batch_scoring import prepare_batch_scoring, score_batch_scoring_request
from haute.deploy._container import _generate_app_source
from haute.deploy._request_frame import frame_rows, request_frame_schema
from haute.deploy._request_rows import RequestRows
from haute.deploy._scorer import admit_deploy_execution
from haute.errors import DeployError, QuoteInputError

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

INT64_MAX = 2**63 - 1
EXACT = 2**53


def _framed(dtype: pl.DataType, value: object) -> Any:
    return frame_rows([{"x": value}], {"x": dtype})["x"][0]


@pytest.mark.parametrize(
    ("dtype", "value", "expected"),
    [
        (pl.Int64(), 30, 30),
        (pl.Int64(), INT64_MAX, INT64_MAX),
        (pl.Int64(), -(2**63), -(2**63)),
        (pl.UInt8(), 255, 255),
        (pl.Float64(), 30, 30.0),
        (pl.Float64(), 30.0, 30.0),
        (pl.Float64(), 30.9, 30.9),
        (pl.Float64(), EXACT, float(EXACT)),
        (pl.Float32(), 2**24, float(2**24)),
        (pl.String(), "30", "30"),
        (pl.Categorical(), "north", "north"),
        (pl.Enum(["a", "b"]), "a", "a"),
        (pl.Boolean(), True, True),
        (pl.Date(), "2024-05-01", date(2024, 5, 1)),
        (pl.Date(), date(2024, 5, 1), date(2024, 5, 1)),
        # A pandas Timestamp for a date column: midnight, without a zone.
        (pl.Date(), datetime(2024, 5, 1), date(2024, 5, 1)),
        (pl.Datetime("us"), "2024-05-01T10:30:00", datetime(2024, 5, 1, 10, 30)),
        (pl.Null(), None, None),
    ],
)
def test_a_value_its_type_holds_exactly_is_accepted(
    dtype: pl.DataType, value: object, expected: object
) -> None:
    assert _framed(dtype, value) == expected


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        (pl.Int64(), 30.0),
        (pl.Int64(), 30.9),
        (pl.Int64(), "30"),
        (pl.Int64(), True),
        (pl.Int64(), INT64_MAX + 1),
        (pl.Int64(), -(2**63) - 1),
        (pl.UInt8(), 256),
        (pl.UInt8(), -1),
        (pl.Float64(), "30"),
        (pl.Float64(), True),
        (pl.Float64(), EXACT + 1),
        (pl.Float64(), 10**400),
        (pl.Float32(), 2**24 + 1),
        (pl.Float32(), 1e39),
        (pl.String(), 30),
        (pl.String(), True),
        (pl.Enum(["a", "b"]), "c"),
        (pl.Boolean(), 1),
        (pl.Boolean(), "true"),
        (pl.Date(), "2024-5-1"),
        (pl.Date(), "2024-02-30"),
        (pl.Date(), 20240501),
        (pl.Date(), datetime(2024, 5, 1, 10, 30)),
        (pl.Datetime("us"), "yesterday"),
        (pl.Datetime("us"), "2024-05-01T10:30:00+01:00"),
        (pl.Datetime("us", "UTC"), "2024-05-01T10:30:00"),
        (pl.Null(), 1),
    ],
)
def test_a_value_its_type_does_not_hold_is_refused_naming_the_column(
    dtype: pl.DataType, value: object
) -> None:
    with pytest.raises(QuoteInputError) as refused:
        _framed(dtype, value)
    assert (refused.value.column, refused.value.quote) == ("x", 0)
    assert refused.value.to_payload()["error_code"] == "quote_input_invalid"


def test_a_zoned_date_time_keeps_its_instant() -> None:
    framed = _framed(pl.Datetime("us", "UTC"), "2024-05-01T10:30:00+01:00")
    assert framed.isoformat() == "2024-05-01T09:30:00+00:00"


def test_typed_nulls_keep_the_declared_type_and_unknown_keys_are_ignored() -> None:
    schema = {"age": pl.Int64(), "region": pl.String(), "start": pl.Date()}

    frame = frame_rows([{"age": None, "extra": 1}, {"region": "N"}], schema)

    assert frame.schema == pl.Schema(schema)
    assert frame.to_dicts() == [
        {"age": None, "region": None, "start": None},
        {"age": None, "region": "N", "start": None},
    ]


def test_a_mixed_list_is_refused_at_the_quote_that_breaks_it() -> None:
    with pytest.raises(QuoteInputError) as refused:
        frame_rows([{"age": 30}, {"age": "30"}], {"age": pl.Int64()})
    assert (refused.value.column, refused.value.quote) == ("age", 1)


def test_a_quote_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(QuoteInputError, match="Quote 0 is not an object"):
        frame_rows([30], {"age": pl.Int64()})  # type: ignore[list-item]


def test_a_type_a_json_request_cannot_carry_refuses_the_deploy() -> None:
    assert request_frame_schema({"age": "Int64", "start": "Date"}) == {
        "age": pl.Int64(),
        "start": pl.Date(),
    }
    with pytest.raises(DeployError, match="'limits' is List\\(Int64\\)"):
        request_frame_schema({"age": "Int64", "limits": "List(Int64)"})


# ---------------------------------------------------------------------------
# Every path frames with the same rules
# ---------------------------------------------------------------------------

SCHEMA = {"quote_id": "Int64", "age": "Float64"}


def _graph() -> PipelineGraph:
    fields = ["quote_id", "age", "rate"]
    return PipelineGraph.model_validate(
        {
            "nodes": [
                {"id": "quotes", "data": {"label": "quotes", "nodeType": "apiInput", "config": {}}},
                {
                    "id": "calc",
                    "data": {
                        "label": "calc",
                        "nodeType": "polars",
                        "config": {"code": "df = quotes.with_columns(rate=pl.col('age') * 2)"},
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "calc",
                                    "source_column": f,
                                    "output_path": f"$[:].{f}",
                                    "enabled": True,
                                }
                                for f in fields
                            ],
                            "outputFormat": "json",
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "quotes", "target": "calc", "sourceHandle": "quotes"},
                {"id": "e2", "source": "calc", "target": "out"},
            ],
        }
    )


def _manifest() -> dict[str, Any]:
    return {
        "polars_version": pl.__version__,
        "pruned_graph": _graph().model_dump(mode="json"),
        "input_node_ids": ["quotes"],
        "output_node_id": "out",
        "input_schema": SCHEMA,
        "request_rows": RequestRows(multi_quote="in_request_order").to_manifest(),
        "artifacts": {},
        "output_fields": None,
    }


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, TestClient]:
    (tmp_path / "deploy_manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    app_path = tmp_path / "app.py"
    app_path.write_text(_generate_app_source("motor", 8080), encoding="utf-8")
    module_name = f"_haute_request_frame_app_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    async def in_process(function: Any, *args: Any, config: Any) -> Any:
        del config
        return function(*args)

    # The batch worker runs in this process, as its spawn would run it.
    monkeypatch.setattr(module, "run_isolated_worker_async", in_process)
    return module, TestClient(module.app)


def test_the_container_types_a_quote_by_its_schema(tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    admitted: list[int] = []
    admit = module.admit_deploy_execution

    def counting(**kwargs: Any) -> Any:
        admitted.append(kwargs["row_count"])
        return admit(**kwargs)

    monkeypatch.setattr(module, "admit_deploy_execution", counting)

    whole = client.post("/quote", json={"quote_id": 1, "age": 30})
    written = client.post("/quote", json={"quote_id": 1, "age": 30.0})
    truncated = client.post("/quote", json={"quote_id": 1.9, "age": 30})

    # 30 and 30.0 are one Float64 age, and score equally.
    assert whole.status_code == written.status_code == 200, whole.text
    assert (
        whole.json()["rows"]
        == written.json()["rows"]
        == [{"quote_id": 1, "age": 30.0, "rate": 60.0}]
    )
    # 1.9 for Int64 is refused, not truncated, before anything is admitted.
    assert truncated.status_code == 422, truncated.text
    assert truncated.json()["error_code"] == "quote_input_invalid"
    assert (truncated.json()["column"], truncated.json()["quote"]) == ("quote_id", 0)
    assert admitted == [1, 1]


def test_the_container_s_batch_worker_frames_by_the_schema(tmp_path, monkeypatch):
    _module, client = _app(tmp_path, monkeypatch)

    answered = client.post("/quote", json=[{"quote_id": 1, "age": 30}, {"quote_id": 2}])
    refused = client.post("/quote", json=[{"quote_id": 1, "age": 30}, {"quote_id": "2"}])

    assert answered.status_code == 200, answered.text
    assert answered.json()["rows"] == [
        {"quote_id": 1, "age": 30.0, "rate": 60.0},
        # A missing age is a null Float64: the answer leaves its nulls out.
        {"quote_id": 2},
    ]
    assert refused.status_code == 422, refused.text
    assert (refused.json()["error_code"], refused.json()["column"], refused.json()["quote"]) == (
        "quote_input_invalid",
        "quote_id",
        1,
    )


def test_a_body_that_is_not_utf8_is_invalid_json(tmp_path, monkeypatch):
    _module, client = _app(tmp_path, monkeypatch)

    response = client.post(
        "/quote", content=b'{"age": "\xe9"}', headers={"content-type": "application/json"}
    )

    assert response.status_code == 422, response.text
    assert response.json()["reason"] == "invalid_json"


def test_the_dry_run_writes_only_dates_beyond_json_and_a_flat_batch_needs_its_schema(tmp_path):
    from haute.deploy._batch_scoring import BatchScoreRequest, _iso_value

    assert _iso_value(datetime(2024, 5, 1, 9, 30)) == "2024-05-01T09:30:00"
    with pytest.raises(TypeError, match="set is not JSON serialisable"):
        _iso_value({1})

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps([{"quote_id": 1, "age": 30}]), encoding="utf-8")
    request = BatchScoreRequest(
        graph=_graph(),
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths={},
        output_fields=None,
        input_path=str(input_path),
        result_path=str(tmp_path / "result.parquet"),
    )
    context = admit_deploy_execution(operation="deploy_quote", row_count=1)
    try:
        with pytest.raises(ValueError, match="needs its input schema"):
            score_batch_scoring_request(request, execution_context=context)
    finally:
        context.release_admission(preserve_primary_error=True)


def test_a_dated_dry_run_reaches_the_worker_as_iso_strings(tmp_path):
    """The deploy's dry run sends its sample's dates through JSON; the worker types them back."""
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                {"id": "quotes", "data": {"label": "quotes", "nodeType": "apiInput", "config": {}}},
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "quotes",
                                    "source_column": "start",
                                    "output_path": "$[:].start",
                                    "enabled": True,
                                }
                            ],
                            "outputFormat": "json",
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "quotes", "target": "out", "sourceHandle": "quotes"}],
        }
    )
    sample = pl.DataFrame({"start": [date(2024, 5, 1)]})
    plan = prepare_batch_scoring(
        sample.to_dicts(),
        graph=graph,
        input_node_ids=["quotes"],
        output_node_id="out",
        artifact_paths={},
        output_fields=None,
        operation="deploy_bundle_schema",
        input_schema=sample.schema,
    )
    try:
        assert json.loads(Path(plan.request.input_path).read_text(encoding="utf-8")) == [
            {"start": "2024-05-01"}
        ]
        context = admit_deploy_execution(operation="deploy_quote", row_count=1)
        try:
            outcome = score_batch_scoring_request(plan.request, execution_context=context)
        finally:
            context.release_admission(preserve_primary_error=False)
        assert outcome.row_count == 1
        assert pl.read_parquet(plan.request.result_path)["start"].to_list() == [date(2024, 5, 1)]
    finally:
        plan.cleanup(primary_error=None)


def test_the_databricks_model_frames_by_the_schema(tmp_path):
    """MLflow has enforced the signature before predict; Haute's check applies to what is left."""
    from haute.deploy._model_code import HauteModel

    manifest_path = tmp_path / "deploy_manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    context = MagicMock()
    context.artifacts = {"deploy_manifest": str(manifest_path)}
    model = HauteModel()
    model.load_context(context)

    # A missing value arrives as pandas' NA (or NaN, NaT): a null of the declared type.
    quotes = pd.DataFrame({"quote_id": pd.array([1, None], dtype="Int64"), "age": [30.0, 41.0]})
    answer = model.predict(MagicMock(), quotes)
    assert pl.from_pandas(answer).to_dicts() == [
        {"quote_id": 1, "age": 30.0, "rate": 60.0},
        {"quote_id": None, "age": 41.0, "rate": 82.0},
    ]
    with pytest.raises(QuoteInputError) as refused:
        model.predict(MagicMock(), pd.DataFrame({"quote_id": [1.9], "age": [30.0]}))
    assert refused.value.column == "quote_id"
