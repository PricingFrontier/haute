"""Shared tmp projects, pipeline sources and helpers for the assistant tool suites.

The parquet-backed projects, pipeline sources, egress policies and operation helpers here are used
by several ``test_assistant_*`` modules that test ``haute.assistant._tools``, so each imports them
from this one place.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._types import GraphNode, NodeData, PipelineGraph
from haute.assistant._ops import GraphEditPlan
from tests._project import use_project

# ---------------------------------------------------------------------------
# Fixtures — a real tmp project with a parquet source
# ---------------------------------------------------------------------------


PIPELINE_SOURCE = '''\
import polars as pl

import haute

def add_flag(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.with_columns(flag=pl.lit(1))


pipeline = haute.Pipeline("main", description="schema tool fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    """Read the quote rows."""

    return pl.scan_parquet("data/quotes.parquet")


@pipeline.polars
def enriched(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Derive, rename, and drop columns so the schema visibly changes."""

    return add_flag(
        quotes.rename({"vehicle_year": "year"}).drop("notes")
    ).with_columns(age=2026 - pl.col("year"))
'''


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    use_project(tmp_path)
    (tmp_path / "data").mkdir()
    pl.DataFrame(
        {
            "quote_id": ["q1", "q2"],
            "vehicle_year": [2019, 2021],
            "notes": ["a", "b"],
        }
    ).write_parquet(tmp_path / "data" / "quotes.parquet")
    (tmp_path / "main.py").write_text(PIPELINE_SOURCE, encoding="utf-8")
    (tmp_path / "haute.toml").write_text(
        '[assistant]\nprovider = "openai"\nmodel = "test"\n'
        'base_url = "https://api.openai.com/v1"\n'
        '[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "restricted"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        "allow_row_samples = false\n"
        "allow_aggregate_statistics = false\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Authoring states the tool must answer usefully rather than refuse
# ---------------------------------------------------------------------------


def _profile(source_file: str, node: str, input_name: str | None = None) -> dict[str, object]:
    from haute.assistant._tools import _prepare_column_profile, column_profiles

    prepared = _prepare_column_profile(source_file, node, input_name)
    if isinstance(prepared, dict):
        return prepared
    return asyncio.run(column_profiles(*prepared, session_id="test"))


# ---------------------------------------------------------------------------
# Pre-flatten target validation (crafted hierarchical graph)
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str = "polars") -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config={}),
        position={"x": 0.0, "y": 0.0},
    )


# ---------------------------------------------------------------------------
# Remaining read tools + the executor dispatch seam
# ---------------------------------------------------------------------------


def _stored_plan(plan: Mapping[str, object]) -> GraphEditPlan:
    """The plan a dry-run result's hash names, as the plan store holds it."""

    from haute.assistant._tools import _PLAN_STORE

    return _PLAN_STORE.get(str(plan["plan_hash"]))


# ---------------------------------------------------------------------------
# Steps-first authoring replays (ASSIST-03)
# ---------------------------------------------------------------------------


def _guide_json_blocks() -> list[object]:
    import re

    from haute.assistant._assets import authoring_guide

    blocks = re.findall(r"```json\n(.*?)\n```", authoring_guide(), flags=re.DOTALL)
    return [json.loads(block) for block in blocks]


def _guide_step_lists() -> list[list[dict[str, str]]]:
    """The step lists the authoring guide shows, in the order it shows them."""

    return [block for block in _guide_json_blocks() if isinstance(block, list)]


@pytest.fixture()
def steps_first_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A saved pipeline whose Transform, Rating Step and Load File the analyst
    dropped from the palette and wired, but has not filled in."""

    from haute._config_io import palette_default_config
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._types import GraphEdge, NodeType
    from haute.assistant import _tools
    from haute.assistant._ops import PlanStore
    from haute.routes._save_pipeline import SavePipelineService

    use_project(tmp_path)
    use_project(tmp_path)  # restored by the autouse _restore_project_root
    monkeypatch.setattr(_tools, "_PLAN_STORE", PlanStore())
    monkeypatch.setattr(_tools, "mutations_readiness", lambda _root: (True, None))
    frames = {
        "proposer_claims": {
            "policy_id": ["p1", "p1", "p2"],
            "claim_month": ["2026-08", "2026-08", "2026-07"],
            "amount": [100.0, 50.0, 30.0],
        },
        "additional_drivers_claims": {
            "policy_id": ["p1", "p2"],
            "claim_month": ["2026-08", "2026-08"],
            "amount": [20.0, 10.0],
        },
        "quotes": {
            "quote_id": ["q1", "q2"],
            "region": ["north", "south"],
            "premium": [100.0, 200.0],
        },
        "regions": {"region": ["north", "south"], "zone": ["A", "B"]},
    }
    for name, frame in frames.items():
        pl.DataFrame(frame).write_parquet(tmp_path / f"{name}.parquet")
    (tmp_path / "loadings.json").write_text(json.dumps({"north": 1.1, "south": 0.9}))
    (tmp_path / "main.py").write_text(
        'import haute\n\npipeline = haute.Pipeline("main", description="replays")\n',
        encoding="utf-8",
    )
    palette = {
        "august_totals": NodeType.POLARS,
        "rated": NodeType.RATING_STEP,
        "loaded": NodeType.EXTERNAL_FILE,
    }
    nodes = [
        GraphNode(
            id=name,
            type=NodeType.DATA_INPUT.value,
            data=NodeData(
                label=name,
                nodeType=NodeType.DATA_INPUT,
                config={
                    **palette_default_config(NodeType.DATA_INPUT),
                    "path": f"{name}.parquet",
                },
            ),
        )
        for name in frames
    ] + [
        GraphNode(
            id=name,
            type=node_type.value,
            data=NodeData(label=name, nodeType=node_type, config=palette_default_config(node_type)),
        )
        for name, node_type in palette.items()
    ]
    wiring = [
        ("proposer_claims", "august_totals"),
        ("additional_drivers_claims", "august_totals"),
        ("quotes", "rated"),
        ("quotes", "loaded"),
        ("regions", "loaded"),
    ]
    SavePipelineService(project_root=tmp_path, pipeline_root=tmp_path).save_graph_transactionally(
        graph=PipelineGraph(
            nodes=nodes,
            edges=[GraphEdge(id=f"{s}->{t}", source=s, target=t) for s, t in wiring],
        ),
        name="main",
        description="replays",
        preamble=None,
        source_file="main.py",
        base_revision=load_pipeline_editor_document(
            tmp_path / "main.py", project_root=tmp_path
        ).source_revision,
    )
    return tmp_path


def _egress_toml(root: Path, *, max_sensitivity: str) -> None:
    (root / "haute.toml").write_text(
        '[assistant]\nprovider = "openai"\nmodel = "test"\n'
        'base_url = "https://api.openai.com/v1"\n'
        f'[assistant.egress]\ntrust = "organization"\nmax_sensitivity = "{max_sensitivity}"\n'
        "allow_project_knowledge = false\nallow_executable_source = false\n"
        "allow_row_samples = false\n"
        "allow_aggregate_statistics = false\n",
        encoding="utf-8",
    )


def _august_steps(code: str) -> list[dict[str, str]]:
    return [
        {"id": "start", "kind": "source", "input": "proposer_claims"},
        {"id": "logic", "kind": "free_code", "code": code},
    ]


def _august_ops(code: str) -> list[dict[str, object]]:
    return [
        {"op": "update_node", "node": "august_totals", "config": {"steps": _august_steps(code)}}
    ]


def _reloaded(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A stepped node's *steps* as its reload holds them: they live only in its code, and
    loading gives each the id of its kind and place (READ-22, READ-44). It holds for steps
    that each
    rebuild as themselves: a free-code statement a structured step renders exactly comes
    back as that step, and two adjacent code steps as one."""
    return [{**step, "id": f"{step['kind']}-{place}"} for place, step in enumerate(steps, 1)]


AUGUST_DISCARDED = "df.filter(pl.col('claim_month') == '2026-08')"


_REGION_BANDING = {
    "op": "recipe",
    "recipe": "categorical_banding",
    "arguments": {
        "source": "quotes",
        "name": "region_band",
        "column": "region",
        "output_column": "region_group",
        "rules": [
            {"value": "north", "assignment": "core"},
            {"value": "south", "assignment": "other"},
        ],
        "default": "unknown",
    },
}


EGRESS_SOURCE = """\
import polars as pl

import haute

pipeline = haute.Pipeline("main", description="egress fixture")


@pipeline.polars
def quotes() -> pl.LazyFrame:
    return pl.scan_csv("data/quotes.csv")


@pipeline.polars
def typed() -> pl.LazyFrame:
    return pl.scan_csv("data/quotes.csv", schema_overrides={"age": pl.Int64})


@pipeline.polars
def strict(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.with_columns(pl.col("age").cast(pl.Int64, strict=True)).collect().lazy()
"""


# Values that exist only in the rows: none may reach a tool result unless the
# project permits row samples.
_ROW_VALUES = ("Q-7781", "Q-9912", "1984-03-02", "1990-11-30", "abc", "secret-123")


@pytest.fixture()
def egress_project(project_root: Path) -> Path:
    """Quote rows with personal values and malformed ages, read from CSV.

    The first malformed age reads like a Polars column phrase, so a scraper
    that trusts the error text reports that cell as a column name.
    """

    (project_root / "data" / "quotes.csv").write_text(
        "quote_id,dob,age\nQ-7781,1984-03-02,41\nQ-5150,1977-06-01,column 'secret-123'\n"
        "Q-9912,1990-11-30,abc\n",
        encoding="utf-8",
    )
    (project_root / "main.py").write_text(EGRESS_SOURCE, encoding="utf-8")
    return project_root


def _row_values_in(result: object) -> list[str]:
    text = json.dumps(result)
    return [value for value in _ROW_VALUES if value in text]


def _egress_policy(
    monkeypatch: pytest.MonkeyPatch, *, executable_source: bool, row_samples: bool
) -> None:
    import haute.assistant._tools as tools_module
    from haute.assistant._config import EgressPolicy

    monkeypatch.setattr(
        tools_module,
        "resolve_egress_policy",
        lambda _root: EgressPolicy(
            trust="organization",
            max_sensitivity="restricted",
            allow_project_knowledge=False,
            allow_executable_source=executable_source,
            allow_row_samples=row_samples,
            allow_aggregate_statistics=False,
        ),
    )


def _policy(*, max_sensitivity: str, executable: bool):
    from haute.assistant._config import EgressPolicy

    return EgressPolicy(
        trust="organization",
        max_sensitivity=max_sensitivity,  # type: ignore[arg-type]
        allow_project_knowledge=False,
        allow_executable_source=executable,
        allow_row_samples=False,
        allow_aggregate_statistics=False,
    )


# ---------------------------------------------------------------------------
# Turn context
# ---------------------------------------------------------------------------


def _turn_policy(*, max_sensitivity: str = "internal", allow_row_samples: bool = False):
    from haute.assistant._config import EgressPolicy

    return EgressPolicy(
        trust="organization",
        max_sensitivity=max_sensitivity,  # type: ignore[arg-type]
        allow_project_knowledge=False,
        allow_executable_source=False,
        allow_row_samples=allow_row_samples,
        allow_aggregate_statistics=False,
    )
