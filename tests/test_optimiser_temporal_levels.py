"""Ratebook factor levels and breakdown levels of a temporal column keep their nanoseconds.

A Polars ``Datetime("ns")`` and every ``Time`` hold nanoseconds while Python's
``datetime`` and ``time`` hold microseconds, so a level read into Python before it
is keyed merges with a level that differs from it below a microsecond (OPM-06).
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from haute.routes._optimiser_outcomes import ChoiceTarget, FactorSegments
from haute.routes.optimiser import OPTIMISER_SEGMENTS_ROUTE, _choice_service
from tests._optimiser_route_helpers import _poll_until_done
from tests.test_optimiser_ratebook_solve import _make_ratebook_graph

_START_NS = 1_700_000_000_000_000_000
# Two policy start times 100 ns apart: one microsecond in Python, two levels in Polars.
_STARTS = pl.Series("start", [_START_NS, _START_NS + 100], dtype=pl.Int64).cast(pl.Datetime("ns"))


def _temporal_ratebook_data(tmp_path, n_quotes: int = 40, n_steps: int = 4):
    rng = np.random.RandomState(7)
    scenario_values = np.linspace(0.8, 1.2, n_steps).astype(np.float32)
    rows: dict[str, list] = {
        "quote_id": [],
        "scenario_index": [],
        "scenario_value": [],
        "expected_income": [],
        "volume": [],
    }
    for quote in range(n_quotes):
        income = rng.uniform(100, 1000)
        volume = rng.uniform(0.5, 1.5)
        for step, value in enumerate(scenario_values):
            rows["quote_id"].append(f"q_{quote:04d}")
            rows["scenario_index"].append(step)
            rows["scenario_value"].append(float(value))
            rows["expected_income"].append(float(income * value))
            rows["volume"].append(float(volume * (2.0 - value)))
    scored = pl.DataFrame(
        {
            "quote_id": rows["quote_id"],
            "scenario_index": pl.Series(rows["scenario_index"], dtype=pl.Int32),
            "scenario_value": pl.Series(rows["scenario_value"], dtype=pl.Float32),
            "expected_income": pl.Series(rows["expected_income"], dtype=pl.Float32),
            "volume": pl.Series(rows["volume"], dtype=pl.Float32),
        }
    )
    scored_path = tmp_path / "scored.parquet"
    scored.write_parquet(scored_path)
    banding = pl.DataFrame(
        {
            "quote_id": [f"q_{quote:04d}" for quote in range(n_quotes)],
            "region": pl.concat([_STARTS] * (n_quotes // 2)).alias("region"),
        }
    )
    banding_path = tmp_path / "banding.parquet"
    banding.write_parquet(banding_path)
    return str(scored_path), str(banding_path)


@pytest.mark.usefixtures("_widen_sandbox_root")
def test_a_ratebook_solve_counts_datetime_levels_100_ns_apart_as_two(client, tmp_path):
    scored_path, banding_path = _temporal_ratebook_data(tmp_path)
    graph = _make_ratebook_graph(scored_path, banding_path)

    response = client.post(
        "/api/optimiser/solve",
        json={"graph": graph, "target": {"occurrence": None, "node_id": "opt"}},
    )
    assert response.status_code == 200, response.text
    status = _poll_until_done(client, response.json()["job_id"])

    assert status["status"] == "completed", status.get("message", "")
    table = status["result"]["factor_tables"]["region"]
    assert {row["__factor_group__"]: row["quote_count"] for row in table} == _EXPECTED_LEVELS

    job_id = response.json()["job_id"]
    segments = client.post(OPTIMISER_SEGMENTS_ROUTE, json={"job_id": job_id, "key": "region"})
    assert segments.status_code == 200, segments.text
    assert {row["label"]: row["quotes"] for row in segments.json()["rows"]} == _EXPECTED_LEVELS

    outcome = _choice_service.choice_query(
        job_id, ChoiceTarget(None), FactorSegments(factor="region", limit=10)
    )
    assert dict(zip(outcome.rows["level"], outcome.rows["quotes"], strict=True)) == (
        _EXPECTED_LEVELS
    )


# The keys a rating lookup matches these levels on: all nine digits, kept apart.
_EXPECTED_LEVELS = {
    "2023-11-14 22:13:20.000000000": 20,
    "2023-11-14 22:13:20.000000100": 20,
}


def test_a_categorical_breakdown_of_a_time_column_keeps_its_fraction():
    """Polars' ``cast(pl.String)`` writes a Time without its fraction: 1.5 s and 1.7 s
    would be one level, ``00:00:01``."""
    from tests.test_optimiser_segments import _frames, _run

    times = pl.Series("start_time", [1_500_000_000, 1_700_000_000, 1_700_000_000]).cast(pl.Time)
    frames, n = _frames([0, 1, 2], {"start_time": times})

    response = _run(frames, n, column="start_time")

    assert {row.label: row.quotes for row in response.rows} == {
        "00:00:01.500000": 1,
        "00:00:01.700000": 2,
    }
