"""Every collect in Haute names its engine (PL2-10, specs/io-layer).

Polars' ``auto`` engine resolves to the streaming engine for a lazy query on 2.0 and
follows ``POLARS_ENGINE_AFFINITY``, so a collect that leaves the engine to Polars
changes behaviour with the version or the host. ``execution_collect`` takes the engine
as a required argument, and every direct ``collect()``, ``collect_all`` and sink in
``src/haute/`` (the generated container app included) names one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from haute._polars_utils import execution_collect
from haute.deploy._container import _generate_app_source

SRC = Path(__file__).resolve().parents[1] / "src" / "haute"
_SINKS = frozenset({"sink_parquet", "sink_csv", "sink_ipc", "sink_ndjson", "sink_batches"})


def engineless_collects(source: str) -> list[int]:
    """The lines of *source* holding a Polars collect or sink that names no engine."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if any(keyword.arg in {"engine", None} for keyword in node.keywords):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name, receiver = func.id, None
        elif isinstance(func, ast.Attribute):
            name, receiver = func.attr, func.value
        else:
            continue
        if name in {"execution_collect", "collect_all", "collect_batches"} or name in _SINKS:
            lines.append(node.lineno)
        # ``LazyFrame.collect`` takes no positional argument; ``gc.collect()`` is not Polars,
        # and a Haute helper named ``collect`` takes the frame it collects.
        elif name == "collect" and not node.args:
            if not (isinstance(receiver, ast.Name) and receiver.id == "gc"):
                lines.append(node.lineno)
    return sorted(lines)


def test_every_collect_in_haute_names_its_engine() -> None:
    reports = [
        f"src/haute/{path.relative_to(SRC).as_posix()}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in engineless_collects(path.read_text(encoding="utf-8"))
    ]
    reports += [
        f"generated container app:{line}"
        for line in engineless_collects(_generate_app_source("model", 8080))
    ]

    assert reports == [], "A collect leaves its engine to Polars:\n" + "\n".join(reports)


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        ("frame = lf.collect()\n", [1]),
        ("frame = lf.collect(engine='streaming')\n", []),
        ("gc.collect()\n", []),
        ("rows = frames.collect(plan)\n", []),
        ("frame = execution_collect(lf)\n", [1]),
        ("frame = execution_collect(lf, engine='in-memory')\n", []),
        ("lf.sink_parquet(path)\n", [1]),
        ("frames = pl.collect_all(plans, **options)\n", []),
    ],
)
def test_the_rule_reads_a_collect_s_engine(source: str, lines: list[int]) -> None:
    assert engineless_collects(source) == lines


def test_the_collect_seam_takes_no_engine_but_a_named_one() -> None:
    import polars as pl

    with pytest.raises(ValueError, match="engine must be 'streaming' or 'in-memory'"):
        execution_collect(pl.LazyFrame({"x": [1]}), engine="auto")  # type: ignore[arg-type]
