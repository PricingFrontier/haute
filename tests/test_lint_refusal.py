"""The pre-commit hook's ruff report, said in the editor's terms (``haute._lint_refusal``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._lint_refusal import REFUSAL_GUIDE_URL, explain_lint_refusal, lint_problems

_PIPELINE = """\
import haute
import polars as pl

pipeline = haute.Pipeline("rated")


@pipeline.polars
def priced(df: pl.LazyFrame) -> pl.LazyFrame:
    check = df.filter(pl.col("x") > 0)
    return df


@pipeline.polars(config="config/polars/rated.json")
def rated(priced: pl.LazyFrame) -> pl.LazyFrame:
    return priced.filter(pl.col("excess") == None)


def helper():
    return undefined_rate
"""


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "rating").mkdir()
    (tmp_path / "rating" / "main.py").write_text(_PIPELINE, encoding="utf-8")
    return tmp_path


def _lines(output: str, repository: Path) -> list[str]:
    explained = explain_lint_refusal(output, repository)
    assert explained is not None
    return explained.splitlines()


def test_each_problem_names_its_node_and_quotes_its_line(repository: Path) -> None:
    output = (
        "rating/main.py:9:5: F841 Local variable `check` is assigned to but never used\n"
        "rating/main.py:15:40: E711 Comparison to `None` should be `cond is None`\n"
    )

    lines = _lines(output, repository)

    assert lines[0] == "the project's pre-commit hook found 2 problems to fix first:"
    assert lines[1] == (
        '- Node `priced`: `check = df.filter(pl.col("x") > 0)` - Local variable `check` is '
        "assigned to but never used (F841)"
    )
    # A decorator called with arguments registers its node too.
    assert lines[2] == (
        '- Node `rated`: `return priced.filter(pl.col("excess") == None)` - Comparison to '
        "`None` should be `cond is None` (E711)"
    )
    assert lines[3].endswith(f"`# noqa: F841` (its code). More: {REFUSAL_GUIDE_URL}")


def test_a_line_outside_a_node_is_named_by_its_file_and_line(repository: Path) -> None:
    lines = _lines("rating/main.py:19:12: F821 Undefined name `undefined_rate`\n", repository)

    assert lines[1] == (
        "- rating/main.py, line 19: `return undefined_rate` - Undefined name `undefined_rate` "
        "(F821)"
    )


def test_windows_separators_name_the_same_file(repository: Path) -> None:
    lines = _lines(
        "rating\\main.py:9:5: F841 Local variable `check` is assigned to but never used\n",
        repository,
    )

    assert lines[1].startswith("- Node `priced`: ")


def test_a_file_that_cannot_be_read_is_named_without_its_line(repository: Path) -> None:
    lines = _lines("rating/gone.py:3:1: F401 `os` imported but unused\n", repository)

    assert lines[1] == "- rating/gone.py, line 3: `os` imported but unused (F401)"


def test_problems_past_five_are_counted(repository: Path) -> None:
    output = "".join(f"rating/main.py:9:5: F841 Problem {n}\n" for n in range(7))

    lines = _lines(output, repository)

    assert lines[0] == "the project's pre-commit hook found 7 problems to fix first:"
    assert len([line for line in lines if line.startswith("- Node")]) == 5
    assert "- and 2 more" in lines


@pytest.mark.parametrize(
    "output",
    [
        "pre-commit: ruff could not fix and format rating/main.py\n",
        "error: Failed to parse: `pyproject.toml`\n",
        "rating.py: line too long\n",
    ],
)
def test_output_holding_no_ruff_problem_is_left_to_speak_for_itself(
    repository: Path, output: str
) -> None:
    assert lint_problems(output) == []
    assert explain_lint_refusal(output, repository) is None
