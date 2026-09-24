"""What a node's data is, under every profile that may read or write it (CACHE-S06).

A snapshot is only safe to share between two executions if those executions
would have produced the same data. Bounded profiles read their sources
differently from the interactive preview — they project scans, refuse formats
that cannot be scanned, and require a CSV's dtypes to be declared — so "the same
node, the same graph" is not on its own a reason to reuse anything.

These fixtures materialise the same node under every bounded profile and compare
schema, values and row order exactly. Where a profile differs, that difference
is stated here rather than discovered later by a cache serving one profile's
data to another.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import polars as pl
import pytest

from haute._builders import _build_node_fn
from haute._execute_lazy import _execute_eager_core
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionProfile
from haute._io import read_source
from haute._polars_utils import bounded_collect_batches, bounded_sink
from haute._types import PipelineGraph
from haute.errors import BoundedMemoryUnsupportedError
from haute.execution import execute_lazy_graph
from tests.conftest import make_edge, make_graph

# Every profile that writes or reads a `bounded` snapshot. If this list and
# `_node_snapshots._BOUNDED_SNAPSHOT_PROFILES` ever disagree, a profile is
# sharing data this proof never compared.
BOUNDED_PROFILES: tuple[ExecutionProfile, ...] = (
    ExecutionProfile.TRAINING_PREP,
    ExecutionProfile.OPTIMISER_SETUP,
    ExecutionProfile.EXPLORE_ANALYSIS,
    ExecutionProfile.AUTO_RANGE,
    ExecutionProfile.LAZY_SINK,
    ExecutionProfile.CHUNKED_MAP_REDUCE,
    ExecutionProfile.NODE_SNAPSHOT,
)

_REFERENCE_PROFILE = BOUNDED_PROFILES[0]

# Where a bounded execution's rows are taken from. A node-output generation is
# what `bounded_sink` wrote, so that is the seam these fixtures compare by
# default; the others are here to say how far the agreement reaches.
Seam = Literal["sink", "collect", "batches"]

# Small enough that even these small fixtures really are several batches.
_BATCH_CHUNK_ROWS = 2


def test_the_bounded_profiles_here_are_the_ones_that_share_snapshots() -> None:
    """This proof is only worth anything if it covers what the store shares."""
    from haute._node_snapshots import _BOUNDED_SNAPSHOT_PROFILES

    assert set(BOUNDED_PROFILES) == set(_BOUNDED_SNAPSHOT_PROFILES)


@pytest.fixture()
def project(haute_scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(haute_scratch)
    (haute_scratch / "main.py").write_text("# pipeline\n", encoding="utf-8")
    return haute_scratch


QUOTES = pl.DataFrame(
    {
        "quote_id": ["q1", "q2", "q3", "q4", "q5"],
        "region": ["North", "South", "North", "South", "North"],
        "premium": [100.0, 250.5, 75.25, 310.0, 180.75],
        "vehicles": [1, 2, 1, 3, 2],
    }
)

DRIVERS = pl.DataFrame(
    {
        "quote_id": ["q1", "q2", "q3", "q4", "q5"],
        "age": [31, 52, 24, 45, 38],
    }
)


def _data_input(
    node_id: str, path: Path, fmt: str, *, mode: str = "scan", **arguments: Any
) -> dict[str, Any]:
    return {
        "id": node_id,
        "data": {
            "label": node_id,
            "nodeType": "dataInput",
            "config": {
                "inputType": "file",
                "format": fmt,
                "mode": mode,
                "path": str(path),
                "arguments": arguments,
            },
        },
    }


def _transform(node_id: str, code: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": "polars", "config": {"code": code}},
    }


def _graph(project: Path, nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> dict:
    return make_graph(
        {
            "source_file": str(project / "main.py"),
            "preamble": "import polars as pl",
            "nodes": nodes,
            "edges": [make_edge(source, target).model_dump() for source, target in edges],
        }
    ).model_dump()


# --------------------------------------------------------------- materialise


def _bounded_frame(
    graph: dict[str, Any],
    target: str,
    profile: ExecutionProfile,
    *,
    required_columns: Iterable[str] | None = None,
    prepare_inputs: bool = True,
    port: str | None = None,
    via: Seam = "sink",
    sink_dir: Path | None = None,
) -> pl.DataFrame:
    """The node's data as one bounded execution under *profile* produces it.

    `prepare_inputs` is the difference between the two source paths this proof
    has to keep apart: on, the node reads a published input snapshot, which was
    built by inspecting the source completely; off, it reads the file directly
    under the bounded policy, which refuses what it cannot read safely.

    `via` is the materialisation seam, and it matters: a node-output generation
    is whatever `bounded_sink` wrote, so `"sink"` is what a snapshot actually
    contains and is the default here. `"collect"` is a plain collection and
    `"batches"` is the streaming batch collection the chunked, deploy and
    optimiser-apply paths consume.
    """
    context = create_admitted_execution_context(
        operation="profile_semantics_proof", profile=profile
    )
    try:
        outputs, _order, _parents, _names = execute_lazy_graph(
            PipelineGraph.model_validate(graph),
            _build_node_fn,
            target_node_id=target,
            preamble_ns=None,
            source="live",
            enforce_contracts=True,
            required_columns_by_node=(
                None if required_columns is None else {target: set(required_columns)}
            ),
            execution_context=context,
            prepare_inputs=prepare_inputs,
        )
        frame = outputs[target]
        if isinstance(frame, dict):
            frame = frame[port]
        lazy = frame.lazy() if isinstance(frame, pl.DataFrame) else frame
        if via == "collect":
            return lazy.collect()
        if via == "batches":
            return pl.concat(
                list(
                    bounded_collect_batches(
                        lazy,
                        chunk_size=_BATCH_CHUNK_ROWS,
                        maintain_order=True,
                        execution_context=context,
                    )
                )
            )
        assert sink_dir is not None, "the sink seam needs somewhere to write"
        written = sink_dir / f"{target}-{profile.value}.parquet"
        bounded_sink(lazy, written)
        return pl.read_parquet(written)
    finally:
        context.release_admission(preserve_primary_error=True)


def _preview_frame(
    graph: dict[str, Any],
    target: str,
    *,
    row_limit: int | None = None,
    port: str | None = None,
) -> pl.DataFrame:
    """The node's data as the interactive preview produces it."""
    result = _execute_eager_core(
        PipelineGraph.model_validate(graph),
        _build_node_fn,
        target_node_id=target,
        row_limit=row_limit,
        preamble_ns=None,
        source="live",
        enforce_contracts=True,
    )
    assert not result.errors, result.errors
    frame = result.outputs[target]
    if isinstance(frame, dict):
        frame = frame[port]
    assert isinstance(frame, pl.DataFrame), frame
    return frame


# ------------------------------------------------------------------ fixtures


def _parquet_input(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    QUOTES.write_parquet(project / "quotes.parquet")
    graph = _graph(project, [_data_input("quotes", project / "quotes.parquet", "parquet")], [])
    return graph, "quotes", ("quote_id", "premium")


def _csv_declared_dtypes(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    QUOTES.write_csv(project / "quotes.csv")
    graph = _graph(
        project,
        [
            _data_input(
                "quotes",
                project / "quotes.csv",
                "csv",
                schema_overrides={
                    "quote_id": "str",
                    "region": "str",
                    "premium": "float64",
                    "vehicles": "int64",
                },
            )
        ],
        [],
    )
    return graph, "quotes", ("quote_id", "premium")


def _transformed(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    QUOTES.write_parquet(project / "quotes.parquet")
    graph = _graph(
        project,
        [
            _data_input("quotes", project / "quotes.parquet", "parquet"),
            _transform(
                "shaped",
                "df = quotes.with_columns((pl.col('premium') * 1.1).alias('gross'))",
            ),
        ],
        [("quotes", "shaped")],
    )
    return graph, "shaped", ("quote_id", "gross")


def _joined(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    QUOTES.write_parquet(project / "quotes.parquet")
    DRIVERS.write_parquet(project / "drivers.parquet")
    graph = _graph(
        project,
        [
            _data_input("quotes", project / "quotes.parquet", "parquet"),
            _data_input("drivers", project / "drivers.parquet", "parquet"),
            _transform(
                "joined",
                # Declared, or a bounded profile refuses the join outright: without a
                # uniqueness contract only the many-to-many row product bounds it, and
                # that is not a materialisation it can admit. That refusal is the
                # admission surface's business; this fixture is about the data.
                "df = quotes.join(drivers, on='quote_id', how='inner', validate='1:1')",
            ),
        ],
        [("quotes", "joined"), ("drivers", "joined")],
    )
    return graph, "joined", ("quote_id", "age")


def _aggregated(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    QUOTES.write_parquet(project / "quotes.parquet")
    graph = _graph(
        project,
        [
            _data_input("quotes", project / "quotes.parquet", "parquet"),
            _transform(
                "totals",
                "df = quotes.group_by('region').agg("
                "pl.col('premium').sum().alias('total'), pl.len().alias('quotes')"
                ").sort('region')",
            ),
        ],
        [("quotes", "totals")],
    )
    return graph, "totals", ("region", "total")


class _TenTimes:
    """A model whose prediction is a fixed function of one feature.

    Real weights would prove nothing here: this proof is about whether the
    execution path around scoring is the same one under every profile, so the
    model has to be deterministic and nothing else.
    """

    def predict(self, features: Any) -> Any:
        import numpy as np

        column = features["feature"] if hasattr(features, "__getitem__") else features
        return np.asarray(column, dtype="float64") * 10.0


@pytest.fixture(autouse=True)
def _stub_scoring_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load the stub instead of an MLflow artifact, everywhere in this module.

    Only the Model Score fixture reaches this; the scoring code itself — the
    flavor dispatch and the batch/row-local fork a row limit selects — is the
    real one.
    """
    from haute import _mlflow_io

    monkeypatch.setattr(
        _mlflow_io,
        "load_mlflow_model",
        lambda *_args, **_kwargs: _mlflow_io.ScoringModel(
            _TenTimes(), ["feature"], flavor="pyfunc"
        ),
    )


def _model_scored(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    from haute.modelling._feature_contract import build_contract, save_contract

    pl.DataFrame(
        {
            "quote_id": ["q1", "q2", "q3", "q4", "q5"],
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    ).write_parquet(project / "scoring.parquet")
    contract_path = project / "feature_contract.json"
    save_contract(
        build_contract(
            features=["feature"],
            feature_types={"feature": "Float64"},
            categorical_features=[],
            target_name="target",
            target_type="Float64",
            task="regression",
        ),
        contract_path,
    )
    graph = _graph(
        project,
        [
            _data_input("scoring", project / "scoring.parquet", "parquet"),
            {
                "id": "scored",
                "data": {
                    "label": "scored",
                    "nodeType": "modelScore",
                    "config": {
                        "sourceType": "run",
                        "run_id": "run-1",
                        "artifact_path": "model.pyfunc",
                        "task": "regression",
                        "output_column": "prediction",
                        "feature_contract_path": str(contract_path),
                    },
                },
            },
        ],
        [("scoring", "scored")],
    )
    return graph, "scored", ("quote_id", "prediction")


def _api_input_table(project: Path) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    """One port of an apiInput, served from its built input snapshot."""
    import json

    from tests.conftest import build_test_api_input_snapshots

    data_path = project / "policies.json"
    data_path.write_text(
        json.dumps(
            [
                {"policy_id": 1001, "region": "North"},
                {"policy_id": 1002, "region": "South"},
                {"policy_id": 1003, "region": "North"},
            ]
        ),
        encoding="utf-8",
    )
    config: dict[str, Any] = {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                    {"name": "region", "path": "$[:].region", "type": "str", "selected": True},
                ],
            }
        ],
    }
    generations = build_test_api_input_snapshots(data_path, config)
    # The point of this fixture is the cached path, so a table must be built.
    assert generations

    graph = _graph(
        project,
        [{"id": "api", "data": {"label": "api", "nodeType": "apiInput", "config": config}}],
        [],
    )
    return graph, "api", ("policy_id",)


FRAME_FIXTURES = {
    "api_input_table": _api_input_table,
    "model_score": _model_scored,
    "parquet_input": _parquet_input,
    "csv_declared_dtypes": _csv_declared_dtypes,
    "transform": _transformed,
    "join": _joined,
    "aggregation": _aggregated,
}

# A multi-frame source emits one frame per port, so its reader names one.
FIXTURE_PORTS = {"api_input_table": "policies"}

# Fixtures whose row order the interactive preview reproduces. A join is not one
# of them: see `test_the_preview_shares_no_snapshot_with_a_bounded_execution`.
PREVIEW_ORDER_STABLE = tuple(name for name in sorted(FRAME_FIXTURES) if name != "join")


# ------------------------------------------------------- the proof itself


@pytest.mark.parametrize("fixture_name", sorted(FRAME_FIXTURES))
def test_every_bounded_profile_produces_the_same_data(fixture_name: str, project: Path) -> None:
    """One node, one graph, seven profiles, one answer."""
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)
    reference = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)

    for profile in BOUNDED_PROFILES:
        frame = _bounded_frame(graph, target, profile, port=port, sink_dir=project)
        assert frame.schema == reference.schema, f"{fixture_name} schema differs under {profile}"
        assert frame.equals(reference), f"{fixture_name} values differ under {profile}"


@pytest.mark.parametrize("fixture_name", sorted(FRAME_FIXTURES))
def test_a_projected_read_is_the_full_read_restricted_to_those_columns(
    fixture_name: str, project: Path
) -> None:
    """Projection is a narrowing, not a different calculation.

    A snapshot built for a wide demand serves a narrow reader from the same
    data, so asking for fewer columns must not change the rows or their values.
    """
    graph, target, projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    for profile in BOUNDED_PROFILES:
        full = _bounded_frame(graph, target, profile, port=port, sink_dir=project)
        narrow = _bounded_frame(
            graph, target, profile, required_columns=projection, port=port, sink_dir=project
        )
        # Every column demanded has to come back: an oracle built from whatever
        # arrived would accept a narrowing that quietly dropped one.
        assert set(projection) <= set(narrow.columns), (
            f"{fixture_name}/{profile} dropped demanded columns: "
            f"{sorted(set(projection) - set(narrow.columns))}"
        )
        assert set(narrow.columns) <= set(full.columns), f"{fixture_name}/{profile}"
        expected = full.select(narrow.columns)
        # `equals` compares values, not types, so the dtypes are checked too:
        # a projected read that changed Float64 to Int64 would otherwise pass.
        assert narrow.schema == expected.schema, (
            f"{fixture_name} projected schema differs under {profile}: "
            f"{narrow.schema} != {expected.schema}"
        )
        assert narrow.equals(expected), f"{fixture_name} projection differs under {profile}"


def _sorted(frame: pl.DataFrame) -> pl.DataFrame:
    """The frame's rows as a set, so two frames can be compared ignoring order."""
    return frame.sort(by=frame.columns)


@pytest.mark.parametrize("fixture_name", sorted(FRAME_FIXTURES))
def test_a_bounded_execution_repeats_itself_exactly(fixture_name: str, project: Path) -> None:
    """A snapshot is rows in an order, so the order has to be the node's own.

    Serving a stored generation in place of running the node again is only the
    same answer if running it again would have given the same answer — rows,
    values and the order they come in.
    """
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    first = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)
    second = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)

    assert first.schema == second.schema, f"{fixture_name} schema is not stable"
    assert first.equals(second), f"{fixture_name} does not repeat itself under one profile"


@pytest.mark.parametrize("fixture_name", sorted(FRAME_FIXTURES))
def test_a_preview_has_the_rows_the_bounded_data_has(fixture_name: str, project: Path) -> None:
    """The preview reads the same sources and computes the same values."""
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    bounded = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)
    preview = _preview_frame(graph, target, port=port)

    assert preview.schema == bounded.schema, f"{fixture_name} preview schema differs"
    assert _sorted(preview).equals(_sorted(bounded)), f"{fixture_name} preview rows differ"


@pytest.mark.parametrize("fixture_name", PREVIEW_ORDER_STABLE)
def test_a_preview_of_these_is_the_bounded_data_in_order(fixture_name: str, project: Path) -> None:
    """Where nothing reorders rows, the two paths agree completely."""
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    bounded = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)
    preview = _preview_frame(graph, target, port=port)

    assert preview.schema == bounded.schema, f"{fixture_name} preview schema differs"
    assert preview.equals(bounded), f"{fixture_name} preview is not the bounded data"


def test_an_admitted_preview_shares_the_bounded_class() -> None:
    """The mapping, and the measurement it was decided against.

    Every bounded profile above agrees with every other, exactly, including row
    order, and repeats itself at the sink. The interactive preview agrees on
    schema and on the rows — but not reliably on their order. Materialising the
    five-row join fixture five times gave preview orders `q1 q2 q3 q4 q5`,
    `q3 q1 q5 q4 q2`, `q1 q3 q2 q4 q5`, `q3 q1 q2 q4 q5` and `q1 q2 q4 q3 q5`
    against a bounded order of `q1 q2 q3 q4 q5` every time.

    That is the measurement, and it does not settle the policy. Rows carry the
    meaning in this domain and their order does not, so row order is not part
    of the snapshot contract: an admitted preview's captures are written into
    the `bounded` class and runs seed from them, with nothing gated on which
    execution wrote a generation. An operation that reads row position is
    written against an order the pipeline established with a `sort`, which a
    seed cannot disturb. A preview whose lineage is not admitted never asks the
    store for a class at all.
    """
    from haute._node_snapshots import (
        BOUNDED_SEMANTICS_CLASS,
        PREVIEW_SHARES_BOUNDED_SEMANTICS,
        snapshot_read_classes,
        snapshot_write_class,
    )

    assert PREVIEW_SHARES_BOUNDED_SEMANTICS is True
    assert (
        snapshot_write_class(ExecutionProfile.PREVIEW_EAGER, preview_admitted=True)
        == BOUNDED_SEMANTICS_CLASS
    )
    assert snapshot_write_class(ExecutionProfile.PREVIEW_EAGER) is None
    assert snapshot_read_classes(ExecutionProfile.PREVIEW_EAGER) == frozenset(
        {BOUNDED_SEMANTICS_CLASS}
    )


@pytest.mark.parametrize("row_limit", [1, 3, 500])
@pytest.mark.parametrize("fixture_name", PREVIEW_ORDER_STABLE)
def test_a_limited_preview_is_the_first_rows_of_the_bounded_data(
    fixture_name: str, row_limit: int, project: Path
) -> None:
    """A limit takes rows off the end; it does not compute something else."""
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    bounded = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)
    preview = _preview_frame(graph, target, row_limit=row_limit, port=port)

    assert preview.schema == bounded.schema, f"{fixture_name} preview schema differs"
    assert preview.equals(bounded.head(row_limit)), (
        f"{fixture_name} preview under limit {row_limit} is not the bounded head"
    )


# ---------------------------------------------------------------- refusals

# A file Data Input in a bounded execution always reads a *published input
# snapshot*, which is built by inspecting the source completely. The direct
# bounded read below is the other source path — the one that refuses what it
# cannot read within its memory policy — and these fixtures pin that the
# refusal is the same one under every bounded profile.


def _csv_without_declared_dtypes(project: Path) -> tuple[Path, dict[str, Any] | None]:
    QUOTES.write_csv(project / "quotes.csv")
    return project / "quotes.csv", None


def _plain_json(project: Path) -> tuple[Path, dict[str, Any] | None]:
    (project / "quotes.json").write_text(QUOTES.write_json(), encoding="utf-8")
    return project / "quotes.json", None


REJECTION_FIXTURES = {
    "csv_without_declared_dtypes": _csv_without_declared_dtypes,
    "plain_json": _plain_json,
}


@pytest.mark.parametrize("fixture_name", sorted(REJECTION_FIXTURES))
def test_every_bounded_profile_refuses_the_same_sources_the_same_way(
    fixture_name: str, project: Path
) -> None:
    """A source one bounded profile will not read directly, none of them will.

    A profile that read what the others refuse could publish a generation the
    rest could never have produced, and the store would hand it to them as
    though it were their own.
    """
    path, overrides = REJECTION_FIXTURES[fixture_name](project)

    messages: dict[ExecutionProfile, str] = {}
    for profile in BOUNDED_PROFILES:
        with pytest.raises(BoundedMemoryUnsupportedError) as refusal:
            read_source(path, profile=profile, schema_overrides=overrides).collect()
        # Each refusal names the profile that refused, which is the one thing
        # they are allowed to differ by.
        messages[profile] = str(refusal.value).replace(profile.value, "<profile>")

    assert len(set(messages.values())) == 1, (
        f"{fixture_name} is refused differently per profile: {messages}"
    )


@pytest.mark.parametrize("fixture_name", sorted(REJECTION_FIXTURES))
def test_an_unprofiled_read_takes_what_the_bounded_profiles_refuse(
    fixture_name: str, project: Path
) -> None:
    """The refusal is the profile's, not the file's — which is why it matters.

    The same source read without a bounded profile succeeds, so a generation
    could exist for data no bounded execution would ever have read itself.
    """
    path, overrides = REJECTION_FIXTURES[fixture_name](project)

    frame = read_source(path, schema_overrides=overrides).collect()

    assert frame.height == QUOTES.height


def test_a_bounded_execution_reads_an_undeclared_csv_through_its_input_snapshot(
    project: Path,
) -> None:
    """The prepared path accepts what the direct read refuses, and agrees with it.

    A Data Input's snapshot is built by inspecting the CSV completely, so a
    bounded execution reads a file whose dtypes were never declared — and gets
    the same data as declaring them would have given. The two source paths have
    different *acceptance*; this pins that they do not have different *data*.
    """
    QUOTES.write_csv(project / "quotes.csv")
    undeclared = _graph(project, [_data_input("quotes", project / "quotes.csv", "csv")], [])
    declared, target, _projection = _csv_declared_dtypes(project)

    for profile in BOUNDED_PROFILES:
        frame = _bounded_frame(undeclared, "quotes", profile, sink_dir=project)
        assert frame.equals(_bounded_frame(declared, target, profile, sink_dir=project)), profile


def test_which_sources_a_bounded_execution_reads_without_a_prepared_snapshot(
    project: Path,
) -> None:
    """Which source path the frame fixtures above actually exercise.

    They run with input preparation on, so a Data Input reads the snapshot
    prepared for it. The two formats do not depend on that preparation equally:
    Parquet is scannable as it stands and is read from the file, while a CSV —
    declared dtypes or not — has no direct path here and says its snapshot is
    missing. That is why the CSV fixtures above are comparisons of
    snapshot-served data and the Parquet ones are not necessarily.
    """
    from haute._polars_io_registry import PolarsIoConfigError

    parquet_graph, parquet_target, _parquet_projection = _parquet_input(project)
    csv_graph, csv_target, _csv_projection = _csv_declared_dtypes(project)

    unprepared = _bounded_frame(
        parquet_graph, parquet_target, _REFERENCE_PROFILE, prepare_inputs=False, sink_dir=project
    )
    assert unprepared.equals(
        _bounded_frame(parquet_graph, parquet_target, _REFERENCE_PROFILE, sink_dir=project)
    )

    with pytest.raises(PolarsIoConfigError) as missing:
        _bounded_frame(
            csv_graph, csv_target, _REFERENCE_PROFILE, prepare_inputs=False, sink_dir=project
        )
    assert "input_snapshot_missing" in str(missing.value)


# ------------------------------------------------------- materialisation seams


@pytest.mark.parametrize("fixture_name", sorted(FRAME_FIXTURES))
def test_a_collection_is_what_the_sink_would_have_written(fixture_name: str, project: Path) -> None:
    """Collecting a bounded plan and sinking it give the same data.

    The comparisons above are made at the sink, because that is what a
    node-output generation holds. This says the plainer collection agrees with
    it, so a reader that collects rather than reads a generation sees the same
    thing.
    """
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    sunk = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)
    collected = _bounded_frame(
        graph, target, _REFERENCE_PROFILE, port=port, via="collect", sink_dir=project
    )

    assert collected.schema == sunk.schema, f"{fixture_name} collected schema differs"
    assert collected.equals(sunk), f"{fixture_name} collected data differs from the sink's"


@pytest.mark.parametrize("fixture_name", sorted(FRAME_FIXTURES))
def test_streaming_batches_carry_the_same_rows_but_not_a_promised_order(
    fixture_name: str, project: Path
) -> None:
    """What the batch seam does and does not guarantee.

    The chunked, deploy-container and optimiser-apply paths consume
    `bounded_collect_batches` rather than a sink. Those batches carry the same
    rows and the same schema — but not, for a join, in a promised order:
    concatenating them for a 20,000-row `1:1` join gave a frame beginning at
    `q1820` on one run and `q0` on the next, with `maintain_order=True`, while
    the sink began at `q0` every time.

    So row order is a property of the sink, not of bounded execution in
    general. A capture that publishes batch-collected rows as a node's
    generation would be publishing an order the next run need not reproduce;
    CACHE-S07 has to sink what it captures, or capture something order-free.
    """
    graph, target, _projection = FRAME_FIXTURES[fixture_name](project)
    port = FIXTURE_PORTS.get(fixture_name)

    sunk = _bounded_frame(graph, target, _REFERENCE_PROFILE, port=port, sink_dir=project)
    batched = _bounded_frame(
        graph, target, _REFERENCE_PROFILE, port=port, via="batches", sink_dir=project
    )

    assert batched.schema == sunk.schema, f"{fixture_name} batched schema differs"
    assert _sorted(batched).equals(_sorted(sunk)), f"{fixture_name} batched rows differ"
