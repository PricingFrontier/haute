"""Low-code Polars steps: the render and free-code columns endpoints.

The render endpoint reports an invalid step as data and returns free-code line ranges, the free-
code columns endpoint resolves the columns after each free-code step or says why they are unknown,
and a free-code request times out or is superseded per node.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute._execution_context import ExecutionContext
from tests._polars_steps_helpers import (
    GOLDEN_CODE,
    GOLDEN_STEPS,
    _render_body,
    num,
    source,
    step,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ---------------------------------------------------------------------------
# Render endpoint
# ---------------------------------------------------------------------------


FREE_CODE_COLUMNS = "/api/pipeline/polars-steps/free-code-columns"


def _columns_body(
    steps: list[dict[str, Any]],
    input_names: list[str],
    *,
    start: str = "input",
    input_columns: dict[str, list[dict[str, str]]] | None = None,
    frame_columns: list[dict[str, str]] | None = None,
    node_id: str = "rated",
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "steps": steps,
        "input_names": input_names,
        "start": start,
        "input_columns": input_columns or {},
        "frame_columns": frame_columns or [],
    }


QUOTE_COLUMNS = [
    {"name": "premium", "dtype": "Float64"},
    {"name": "inception", "dtype": "Datetime(time_unit='us', time_zone=None)"},
]


def test_render_endpoint_reports_invalid_step_as_data(client: TestClient) -> None:
    ok = client.post(
        "/api/pipeline/polars-steps/render",
        json=_render_body(GOLDEN_STEPS, ["quotes", "rates"]),
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["code"] == GOLDEN_CODE
    assert body["step_lines"][:2] == [[1, 1], [2, 2]]

    bad = client.post(
        "/api/pipeline/polars-steps/render",
        json=_render_body([source(), step("f", "filter", match="all", conditions=[])], ["quotes"]),
    )
    assert bad.status_code == 200
    assert bad.json() == {
        "ok": False,
        "code": "",
        "step_lines": [],
        "step_index": 1,
        "message": "Add at least one condition.",
    }

    malformed = client.post("/api/pipeline/polars-steps/render", json={"steps": "nope"})
    assert malformed.status_code == 422


def test_render_endpoint_returns_free_code_line_ranges_and_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rendering never runs the snippet, so its code text never waits on it."""
    import haute._polars_steps as steps_module

    def never(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the render ran authored code")

    monkeypatch.setattr(steps_module, "_exec_user_code", never)
    steps = [
        source(),
        step("c", "free_code", code="df = df.with_columns(\n    pl.lit(1).alias('x')\n)"),
        step("l", "limit", n=2),
    ]
    response = client.post(
        "/api/pipeline/polars-steps/render",
        json=_render_body(steps, ["quotes"]),
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["step_lines"] == [[1, 1], [2, 4], [5, 5]]
    steps[1]["code"] = "return df"
    invalid = client.post(
        "/api/pipeline/polars-steps/render",
        json=_render_body(steps, ["quotes"]),
    )
    assert invalid.status_code == 200
    assert invalid.json()["ok"] is False
    assert invalid.json()["step_index"] == 1


def test_free_code_columns_endpoint_resolves_the_columns_after_each_free_code_step(
    client: TestClient,
) -> None:
    steps = [
        source(),
        step(
            "c",
            "free_code",
            code="df = df.with_columns(\n    pl.col('inception').dt.year().alias('year')\n)",
        ),
        step("d", "drop", columns=["premium"]),
        step("e", "free_code", code="df = df.with_columns(band=pl.col('year') // 10)"),
        step("w", "with_column", name="flag", expr={"type": "operand", "operand": num(1)}),
    ]
    response = client.post(
        FREE_CODE_COLUMNS,
        json=_columns_body(steps, ["quotes"], input_columns={"quotes": QUOTE_COLUMNS}),
    )

    assert response.status_code == 200
    body = response.json()
    year = {"name": "year", "dtype": "Int32"}
    assert body["free_code_columns"] == [
        {"step_index": 1, "columns": [*QUOTE_COLUMNS, year], "message": ""},
        {
            "step_index": 3,
            "columns": [QUOTE_COLUMNS[1], year, {"name": "band", "dtype": "Int32"}],
            "message": "",
        },
    ]


def test_free_code_columns_endpoint_says_why_columns_are_unknown(client: TestClient) -> None:
    def resolve(code: str, **columns: list[dict[str, str]]) -> dict[str, Any]:
        steps = [source(), step("c", "free_code", code=code)]
        response = client.post(
            FREE_CODE_COLUMNS,
            json=_columns_body(steps, ["quotes", "rates"], input_columns=columns),
        )
        assert response.status_code == 200
        [entry] = response.json()["free_code_columns"]
        assert entry["step_index"] == 1
        assert entry["columns"] is None
        return entry

    assert resolve("df = df")["message"] == "The columns of input 'quotes' are not known yet."
    assert (
        resolve("df = df.join(rates, on='k')", quotes=QUOTE_COLUMNS)["message"]
        == "The columns of input 'rates' are not known yet."
    )
    assert resolve("df = df.select('missing')", quotes=QUOTE_COLUMNS)["message"].startswith(
        'ColumnNotFoundError: unable to find column "missing"'
    )
    assert (
        resolve("df = helper(df)", quotes=QUOTE_COLUMNS)["message"]
        == "NameError: name 'helper' is not defined"
    )
    assert (
        resolve("df = 3", quotes=QUOTE_COLUMNS)["message"]
        == "The code must leave df as a Polars frame."
    )
    assert resolve("df = df", quotes=[{"name": "x", "dtype": "col"}])["message"] == (
        "The type of column 'x' in input 'quotes' cannot be read: col."
    )


def test_free_code_columns_endpoint_resolves_free_code_on_a_frame_surface(
    client: TestClient,
) -> None:
    steps = [step("c", "free_code", code="df = df.with_columns(gross=pl.col('premium') * 1.2)")]
    resolved = client.post(
        FREE_CODE_COLUMNS,
        json=_columns_body(steps, [], start="frame", frame_columns=QUOTE_COLUMNS),
    ).json()
    assert resolved["free_code_columns"] == [
        {
            "step_index": 0,
            "columns": [*QUOTE_COLUMNS, {"name": "gross", "dtype": "Float64"}],
            "message": "",
        }
    ]

    unknown = client.post(FREE_CODE_COLUMNS, json=_columns_body(steps, [], start="frame")).json()
    assert unknown["free_code_columns"] == [
        {
            "step_index": 0,
            "columns": None,
            "message": "The columns of this node's data are not known yet.",
        }
    ]

    stray = client.post(
        FREE_CODE_COLUMNS,
        json=_columns_body([source(), *steps], ["quotes"], frame_columns=QUOTE_COLUMNS),
    )
    assert stray.status_code == 422


def test_free_code_columns_endpoint_reads_the_constants_for_the_requested_source(
    client: TestClient,
) -> None:
    """Free code reads each constant's value for the source the request names."""
    steps = [
        source(),
        step(
            "c",
            "free_code",
            code="df = df.with_columns(pl.lit(1).alias(global_constants.column))",
        ),
    ]
    constants = [
        {"name": "column", "type": "text", "by_source": {"live": "live_col", "nb_batch": "nb_col"}}
    ]

    def resolve(source_name: str) -> dict[str, Any]:
        body = _columns_body(steps, ["quotes"], input_columns={"quotes": QUOTE_COLUMNS})
        response = client.post(
            FREE_CODE_COLUMNS,
            json={**body, "global_constants": constants, "source": source_name},
        )
        assert response.status_code == 200
        [entry] = response.json()["free_code_columns"]
        return entry

    added = {"dtype": "Int32"}
    assert resolve("live")["columns"] == [*QUOTE_COLUMNS, {"name": "live_col", **added}]
    assert resolve("nb_batch")["columns"] == [*QUOTE_COLUMNS, {"name": "nb_col", **added}]
    missing = resolve("other")
    assert missing["columns"] is None
    assert "no value for source 'other'" in missing["message"]


def test_free_code_columns_endpoint_runs_nothing_without_free_code(client: TestClient) -> None:
    """A list without free code answers at once; one that does not render is a 422."""
    plain = client.post(
        FREE_CODE_COLUMNS,
        json=_columns_body([source(), step("l", "limit", n=2)], ["quotes"]),
    )
    assert plain.status_code == 200
    assert plain.json() == {"free_code_columns": []}

    broken = client.post(
        FREE_CODE_COLUMNS,
        json=_columns_body([source(), step("f", "filter", match="all", conditions=[])], ["quotes"]),
    )
    assert broken.status_code == 422
    assert broken.json()["detail"] == "Add at least one condition."

    stray = client.post(
        FREE_CODE_COLUMNS,
        json={**_columns_body([source()], ["quotes"]), "input_columns": {"rates": QUOTE_COLUMNS}},
    )
    assert stray.status_code == 422


class _ConnectedClient:
    async def is_disconnected(self) -> bool:
        return False


async def test_a_free_code_timeout_in_thread_mode_is_every_steps_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thread mode cannot stop the snippet, but the response still keeps the
    deadline, and the thread holds its admission only until it ends."""
    import asyncio
    import threading

    import haute.routes.pipeline as pipeline_module
    from haute.schemas import PolarsFreeCodeColumnsRequest

    release = threading.Event()
    admitted: list[ExecutionContext] = []
    real_admit = pipeline_module.create_admitted_execution_context

    def admit(**kwargs: Any) -> ExecutionContext:
        admitted.append(real_admit(**kwargs))
        return admitted[-1]

    def stuck(*_args: object, **_kwargs: object) -> object:
        assert release.wait(30)
        return []

    monkeypatch.setattr(pipeline_module, "FREE_CODE_COLUMNS_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(pipeline_module, "resolve_free_code_columns", stuck)
    monkeypatch.setattr(pipeline_module, "create_admitted_execution_context", admit)
    steps = [
        source(),
        step("a", "free_code", code="df = df"),
        step("l", "limit", n=2),
        step("b", "free_code", code="df = df"),
    ]
    body = PolarsFreeCodeColumnsRequest.model_validate(
        _columns_body(steps, ["quotes"], input_columns={"quotes": QUOTE_COLUMNS})
    )
    try:
        response = await pipeline_module.resolve_free_code_columns_endpoint(
            body,
            _ConnectedClient(),  # type: ignore[arg-type]
        )
        [context] = admitted
        assert not context.lease.released
    finally:
        release.set()

    note = "The code did not finish within 0.2 seconds."
    assert response.model_dump() == {
        "free_code_columns": [
            {"step_index": 1, "columns": None, "message": note},
            {"step_index": 3, "columns": None, "message": note},
        ]
    }
    deadline = time.monotonic() + 30
    while not context.lease.released:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)


async def test_a_newer_free_code_request_for_the_node_stops_the_older(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One resolution runs per node: the older request's worker is told to
    stop and answers 409, and the newer one resolves."""
    import asyncio
    import threading

    from fastapi import HTTPException

    import haute._interactive_workers as workers_module
    import haute.routes.pipeline as pipeline_module
    from haute.schemas import PolarsFreeCodeColumnsRequest

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    running = threading.Event()
    reasons: list[str] = []
    affinities: list[object] = []

    class _Pool:
        def run(
            self, function: Any, *_args: object, affinity_key: object, stop_reason: Any, **_kw: Any
        ) -> object:
            affinities.append(affinity_key)
            if not running.is_set():
                running.set()
                deadline = time.monotonic() + 30
                while (reason := stop_reason()) is None:
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                reasons.append(reason)
                raise workers_module.WorkerStoppedError(reason)
            return function()

    monkeypatch.setattr(workers_module, "interactive_worker_pool", _Pool)
    steps = [source(), step("c", "free_code", code="df = df.with_columns(x=pl.lit(1))")]
    body = PolarsFreeCodeColumnsRequest.model_validate(
        _columns_body(steps, ["quotes"], input_columns={"quotes": QUOTE_COLUMNS})
    )
    older = asyncio.ensure_future(
        pipeline_module.resolve_free_code_columns_endpoint(body, _ConnectedClient())  # type: ignore[arg-type]
    )
    assert await asyncio.to_thread(running.wait, 30)
    newer = await pipeline_module.resolve_free_code_columns_endpoint(body, _ConnectedClient())  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as stopped:
        await older
    assert stopped.value.status_code == 409
    assert reasons == ["superseded"]
    assert affinities == [("polars_free_code_columns", "rated")] * 2
    [entry] = newer.free_code_columns
    assert entry.columns is not None
    assert entry.columns[-1].name == "x"
