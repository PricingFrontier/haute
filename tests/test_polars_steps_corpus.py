"""Executed equivalence corpus for the low-code Polars step builder.

``tests/fixtures/polars_steps_corpus/corpus.json`` holds hand-written Polars
snippets across the categories an analyst writes (filters, formulas,
conditionals, strings, dates, casts, sorting, dedup, aggregation, windows,
joins, reshaping, pipelines); ``translations.json`` holds each snippet's step
list, with auxiliary upstream Transform nodes where the snippet builds an
intermediate frame. Every translation must reproduce the snippet exactly on
the normal and the hard synthetic data (exact dtypes, null pattern, row order
unless the snippet leaves it unspecified); a snippet the vocabulary cannot
express is listed in ``NOT_EXPRESSIBLE`` with its reason, so an addition that
makes it expressible fails here until the fixture is retranslated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._polars_steps import (
    CAST_DTYPES,
    FUNCTIONS,
    STEP_KINDS,
    PolarsStepError,
    render_polars_steps,
)
from tests.assistant_eval._frames import frames_equal, run, synthetic_inputs

FIXTURES = Path(__file__).parent / "fixtures" / "polars_steps_corpus"
CORPUS: dict[str, dict[str, Any]] = {
    item["id"]: item for item in json.loads((FIXTURES / "corpus.json").read_text(encoding="utf-8"))
}
TRANSLATIONS: dict[str, dict[str, Any]] = json.loads(
    (FIXTURES / "translations.json").read_text(encoding="utf-8")
)
#: Snippets the vocabulary does not express, with the fragment their fixture reason must carry.
NOT_EXPRESSIBLE: dict[str, str] = {
    "typed_rating_export": "Enum",
    "vehicle_and_age_bands": "Banding node",
}


def _expected(code: str, inputs: dict[str, pl.LazyFrame]) -> tuple[pl.DataFrame | None, str | None]:
    try:
        return run(code, inputs), None
    except Exception as exc:  # noqa: BLE001 - the snippet's own failure is the expectation
        return None, type(exc).__name__


def test_fixture_and_gap_list_agree() -> None:
    assert set(TRANSLATIONS) == set(CORPUS)
    gaps = {sid for sid, entry in TRANSLATIONS.items() if entry.get("steps") is None}
    assert gaps == set(NOT_EXPRESSIBLE)
    for sid, fragment in NOT_EXPRESSIBLE.items():
        assert fragment in TRANSLATIONS[sid]["reason"], sid
    known = set(synthetic_inputs(hard=False))
    for sid, item in CORPUS.items():
        assert set(item["inputs"]) <= known, sid


def test_the_listed_gaps_are_still_outside_the_vocabulary() -> None:
    """The gap list is honest: the constructs it names are not expressible."""
    assert "Enum" not in CAST_DTYPES
    with pytest.raises(PolarsStepError, match="must be one of"):
        render_polars_steps(
            [
                {"id": "s", "kind": "source", "input": "quotes"},
                {"id": "c", "kind": "cast", "casts": [{"column": "region", "dtype": "Enum"}]},
            ],
            ["quotes"],
            start="input",
        )
    assert not {"cut", "band", "qcut"} & set(FUNCTIONS)
    assert not {"band", "banding", "cut"} & set(STEP_KINDS)


@pytest.mark.parametrize("hard", [False, True], ids=["normal", "hard"])
@pytest.mark.parametrize("snippet_id", sorted(CORPUS))
def test_steps_reproduce_hand_written_polars(snippet_id: str, hard: bool) -> None:
    item = CORPUS[snippet_id]
    entry = TRANSLATIONS[snippet_id]
    if entry.get("steps") is None:
        assert snippet_id in NOT_EXPRESSIBLE
        return
    expected, expected_error = _expected(item["code"], synthetic_inputs(hard=hard))

    # Auxiliary transform nodes render against the base inputs and are bound
    # under their names, as upstream nodes are in the graph.
    bound = synthetic_inputs(hard=hard)
    for aux_name, aux_steps in (entry.get("aux") or {}).items():
        aux_code = render_polars_steps(aux_steps, list(bound), start="input").code
        bound[aux_name] = run(aux_code, dict(bound)).lazy()
    rendered = render_polars_steps(entry["steps"], list(bound), start="input")
    try:
        actual = run(rendered.code, bound)
    except Exception as exc:  # noqa: BLE001 - compared against the snippet's own failure
        assert expected_error is not None, f"steps raised {type(exc).__name__}: {exc}"
        assert type(exc).__name__ == expected_error, (
            f"{type(exc).__name__} != {expected_error}: {exc}"
        )
        return
    assert expected_error is None, f"the snippet raises ({expected_error}) but the steps succeed"
    assert expected is not None
    ok, why = frames_equal(expected, actual, order_free=bool(entry.get("order_free")))
    assert ok, f"{why}\n{rendered.code}"
