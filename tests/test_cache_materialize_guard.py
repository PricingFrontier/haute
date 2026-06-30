"""Guard: a multi-frame source output must not reach cache materialization.

A multi-frame apiInput emits ``dict[label, LazyFrame]``. The in-RAM cache
path is gated only on the cache request (not on is_source), so without a
guard a multi-frame dict could reach ``dict.lazy()`` and raise an opaque
``AttributeError``. ``_lazy_frame_for_cache`` makes the unsupported
combination fail loud and named instead.
"""

from __future__ import annotations

import polars as pl
import pytest

from haute._execute_lazy import _lazy_frame_for_cache


def test_rejects_multiport_dict_with_named_error() -> None:
    bundle = {"policies": pl.LazyFrame(), "drivers": pl.LazyFrame()}
    with pytest.raises(RuntimeError) as ei:
        _lazy_frame_for_cache(bundle, "api_node")
    msg = str(ei.value)
    assert "api_node" in msg
    assert "multi-frame" in msg


def test_passes_through_lazyframe_identity() -> None:
    lf = pl.DataFrame({"x": [1]}).lazy()
    assert _lazy_frame_for_cache(lf, "n") is lf


def test_lazifies_a_dataframe() -> None:
    out = _lazy_frame_for_cache(pl.DataFrame({"x": [1]}), "n")
    assert isinstance(out, pl.LazyFrame)
