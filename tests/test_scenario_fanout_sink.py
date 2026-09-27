"""A Model Score below a scenario expansion sinks its input in fan-out-sized chunks.

The expansion's ``explode`` turns each streaming chunk of its source into
``stepCount`` times as many rows in every Polars thread, so the scorer's input
sink caps the chunk at the pipeline setting divided by the fan-out and an
expanded chunk stays within the setting.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

import haute._polars_utils as polars_utils
from haute._builders import _build_node_fn, _upstream_scenario_fanout
from haute._model_scorer import ModelScorer
from haute._node_apply import expand_scenarios_from_config
from haute._polars_utils import (
    DEFAULT_STREAMING_CHUNK_SIZE,
    current_streaming_chunk_size,
    set_streaming_chunk_size,
    streaming_chunk_size_cap,
)
from haute.graph_utils import GraphNode, NodeData
from tests.test_model_scorer import _make_scoring_model


def _polars_chunk() -> int | None:
    raw = pl.Config.state(if_set=True).get("POLARS_STREAMING_CHUNK_SIZE")
    return int(raw) if raw else None


# ---------------------------------------------------------------------------
# streaming_chunk_size_cap
# ---------------------------------------------------------------------------


def test_a_cap_lowers_the_polars_chunk_but_not_the_setting_and_restores_it() -> None:
    set_streaming_chunk_size(1_000)

    with streaming_chunk_size_cap(90):
        assert _polars_chunk() == 90
        assert current_streaming_chunk_size() == 1_000

    assert _polars_chunk() == 1_000
    assert current_streaming_chunk_size() == 1_000


def test_a_cap_never_raises_the_chunk_above_the_setting() -> None:
    set_streaming_chunk_size(50)

    with streaming_chunk_size_cap(400):
        assert _polars_chunk() == 50


def test_a_cap_over_an_unset_chunk_restores_it_unset() -> None:
    pl.Config.set_streaming_chunk_size(None)

    with streaming_chunk_size_cap(7):
        assert _polars_chunk() == 7
        assert current_streaming_chunk_size() == DEFAULT_STREAMING_CHUNK_SIZE

    assert _polars_chunk() is None


def test_overlapping_caps_from_two_threads_keep_the_smallest_until_each_exits() -> None:
    set_streaming_chunk_size(1_000)
    small_entered = threading.Event()
    small_release = threading.Event()

    def hold_small_cap() -> None:
        with streaming_chunk_size_cap(10):
            small_entered.set()
            small_release.wait(timeout=10)

    holder = threading.Thread(target=hold_small_cap)
    holder.start()
    try:
        assert small_entered.wait(timeout=10)
        with streaming_chunk_size_cap(200):
            # The other thread's smaller cap still wins.
            assert _polars_chunk() == 10
            small_release.set()
            holder.join(timeout=10)
            # With it gone, this block's own cap applies.
            assert _polars_chunk() == 200
    finally:
        small_release.set()
        holder.join(timeout=10)
    assert _polars_chunk() == 1_000


def test_a_setting_changed_inside_a_cap_applies_when_the_cap_ends() -> None:
    set_streaming_chunk_size(1_000)

    with streaming_chunk_size_cap(90):
        set_streaming_chunk_size(40)
        assert _polars_chunk() == 40
        assert current_streaming_chunk_size() == 40
        set_streaming_chunk_size(500)
        assert _polars_chunk() == 90

    assert _polars_chunk() == 500


def test_a_cap_is_released_when_its_block_raises() -> None:
    set_streaming_chunk_size(1_000)

    with pytest.raises(RuntimeError), streaming_chunk_size_cap(3):
        raise RuntimeError("boom")

    assert _polars_chunk() == 1_000


# ---------------------------------------------------------------------------
# The fan-out above a Model Score
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str, config: dict[str, Any] | None = None) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node_type, config=config or {}),
    )


def _expander(node_id: str, steps: Any) -> GraphNode:
    return _node(node_id, "scenarioExpander", {"stepCount": steps, "column_name": "adj"})


def test_the_fanout_is_the_product_of_the_upstream_expanders_step_counts() -> None:
    node_map = {
        "src": _node("src", "dataInput"),
        "grid_a": _expander("grid_a", 11),
        "grid_b": _expander("grid_b", 3),
        "calc": _node("calc", "polars"),
    }

    assert _upstream_scenario_fanout(["src", "grid_a", "calc", "grid_b"], node_map) == 33
    assert _upstream_scenario_fanout(["src", "calc"], node_map) == 1
    assert _upstream_scenario_fanout(None, node_map) == 1
    assert _upstream_scenario_fanout(["grid_a"], None) == 1


def test_an_expander_without_a_valid_step_count_does_not_count() -> None:
    node_map = {"bad": _expander("bad", None), "grid": _expander("grid", 5)}

    assert _upstream_scenario_fanout(["bad", "grid"], node_map) == 5


def test_the_model_score_builder_gives_its_scorer_the_upstream_fanout() -> None:
    node_map = {
        "src": _node("src", "dataInput"),
        "grid": _expander("grid", 11),
        "score": _node("score", "modelScore", {"sourceType": "run", "run_id": "abc"}),
    }
    _name, score_fn, _is_source = _build_node_fn(
        node_map["score"],
        source_names=["grid"],
        source_ids=["grid"],
        node_map=node_map,
        upstream_ids=["src", "grid"],
        source="batch",
    )

    assert score_fn.__self__.input_fanout == 11


# ---------------------------------------------------------------------------
# The scorer's input sink
# ---------------------------------------------------------------------------


def _expanded_quotes(steps: int) -> pl.LazyFrame:
    quotes = pl.DataFrame(
        {"quote_id": [f"q{i}" for i in range(6)], "a": [float(i) for i in range(6)]}
    ).lazy()
    return expand_scenarios_from_config(
        quotes,
        {"stepCount": steps, "column_name": "b", "min_value": 0.5, "max_value": 1.5},
    )


def _score_recording_sink_chunks(input_fanout: int, steps: int) -> tuple[list[Any], pl.DataFrame]:
    chunks_in_sink: list[Any] = []
    real_sink = polars_utils.bounded_sink

    def recording_sink(lf: pl.LazyFrame, path: Any, **kwargs: Any) -> Any:
        chunks_in_sink.append(_polars_chunk())
        return real_sink(lf, path, **kwargs)

    scoring_model = _make_scoring_model(feature_names=["a", "b"])
    scoring_model.raw_model.predict.side_effect = lambda x: np.full(len(x), 0.5)
    scorer = ModelScorer(source_type="run", run_id="abc", source="batch", input_fanout=input_fanout)
    with (
        patch("haute._mlflow_io.load_mlflow_model", return_value=scoring_model),
        patch.object(polars_utils, "bounded_sink", recording_sink),
    ):
        scored = scorer.score(_expanded_quotes(steps)).collect()
    return chunks_in_sink, scored


def test_the_scorer_sinks_an_expanded_input_in_setting_over_fanout_chunks() -> None:
    set_streaming_chunk_size(1_100)

    chunks_in_sink, scored = _score_recording_sink_chunks(input_fanout=11, steps=11)

    assert chunks_in_sink == [100]
    assert _polars_chunk() == 1_100
    assert scored.height == 6 * 11
    assert scored["prediction"].to_list() == [0.5] * 66


def test_the_scorer_sinks_an_unexpanded_input_at_the_setting() -> None:
    set_streaming_chunk_size(1_100)

    chunks_in_sink, scored = _score_recording_sink_chunks(input_fanout=1, steps=1)

    assert chunks_in_sink == [1_100]
    assert scored.height == 6
