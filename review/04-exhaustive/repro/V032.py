"""Isolated reproduction for V032.

Claim: ``_is_streaming_compatibility_error`` (src/haute/_polars_utils.py:42-44)
classifies a Polars ComputeError/InvalidOperationError/SchemaError as a
streaming-engine incompatibility purely by ``"stream" in str(exc).lower()``.
This naive substring test misclassifies in BOTH directions:

  (A) FALSE POSITIVE -- a genuine data/schema error whose rendered message
      merely CONTAINS the substring "stream" (e.g. a column named
      ``upstream_premium`` -> "...upstream_premium...", which contains
      "stream") is treated as a streaming incompatibility. In bounded_sink
      this masks the real SchemaError behind
      BoundedMemoryUnsupportedError("Bounded streaming sink failed"); in
      streaming_collect(allow_broad=True) it silently triggers the
      high-memory broad collect fallback instead of surfacing.

  (B) FALSE NEGATIVE -- a genuine streaming-incompatibility error whose
      message does NOT contain the literal "stream" is treated as a
      non-streaming error and propagates raw, so callers never receive the
      typed BoundedMemoryUnsupportedError that the bounded-memory contract
      promises.

This repro is fully in-memory: it uses tiny fake LazyFrame stand-ins that
raise the relevant Polars exception subclasses from collect / sink_parquet.
No disk I/O, no project files, no rating/ or src/ reads. Each assertion
checks the SPECIFIC wrong behaviour (which exception type is raised vs the
correct one), not merely that "something raised".

Run: uv run python review/04-exhaustive/repro/V032.py
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from haute._execution_context import ExecutionProfile
from haute._polars_utils import (
    _is_streaming_compatibility_error,
    bounded_sink,
    streaming_collect,
)
from haute.errors import BoundedMemoryUnsupportedError


# A realistic Polars message that contains the substring "stream" purely
# incidentally, via the column name "upstream_premium". This is a genuine
# *data/schema* error, NOT a streaming-engine incompatibility.
_INCIDENTAL_STREAM_MESSAGE = "column 'upstream_premium' not found in source"

# A realistic Polars *streaming-incompatibility* message that does NOT
# contain the literal substring "stream". Polars routinely emits messages of
# this form when the streaming engine cannot honour a plan node and falls
# back / errors -- e.g. "sink_parquet not yet supported in the new engine".
_REAL_INCOMPAT_NO_STREAM_MESSAGE = "not yet supported in the new engine"


class _CollectRaises:
    """Fake LazyFrame whose streaming collect raises a chosen exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def collect(self, *args: object, **kwargs: object) -> pl.DataFrame:
        del args, kwargs
        raise self._exc


class _SinkRaises:
    """Fake LazyFrame whose sink_parquet raises a chosen exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def sink_parquet(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise self._exc

    # bounded_sink -> streaming_sink only calls sink_parquet for fmt="parquet";
    # sink_csv is never reached in this repro.


def _classifier_sanity() -> None:
    """Confirm the classifier itself is the naive substring test."""
    incidental = pl.exceptions.SchemaError(_INCIDENTAL_STREAM_MESSAGE)
    real_incompat = pl.exceptions.ComputeError(_REAL_INCOMPAT_NO_STREAM_MESSAGE)

    # "upstream_premium" contains "stream" -> classifier says True (WRONG).
    assert _is_streaming_compatibility_error(incidental) is True, (
        "Precondition failed: the incidental-'stream' message was not "
        "classified as a streaming error. If False, the classifier is no "
        "longer a naive substring match."
    )
    # A genuine streaming-incompat message lacking 'stream' -> False (WRONG).
    assert _is_streaming_compatibility_error(real_incompat) is False, (
        "Precondition failed: a streaming-incompat message lacking the "
        "literal 'stream' was classified as streaming. If True, the "
        "classifier no longer hinges on the substring."
    )
    print(
        "classifier sanity OK: 'upstream_premium' -> True (false positive), "
        "'not yet supported in the new engine' -> False (false negative)"
    )


def _case_a_false_positive_bounded_sink() -> None:
    """A genuine SchemaError on column 'upstream_premium' is masked.

    The correct behaviour is for the real SchemaError (the true root cause:
    a missing column) to propagate. Instead it is swallowed and re-raised as
    BoundedMemoryUnsupportedError, hiding the actual problem.
    """
    real_schema_error = pl.exceptions.SchemaError(_INCIDENTAL_STREAM_MESSAGE)
    lf = _SinkRaises(real_schema_error)
    out = Path("unused_in_memory.parquet")  # never written: sink_parquet raises first

    raised: BaseException | None = None
    try:
        bounded_sink(lf, out)  # type: ignore[arg-type]
    except BaseException as exc:  # noqa: BLE001 - we classify what was raised
        raised = exc

    print("case A raised:", type(raised).__name__, "->", raised)

    # The BUG: the genuine schema error is masked behind the bounded-memory
    # wrapper. The original SchemaError is only reachable via __cause__.
    assert isinstance(raised, BoundedMemoryUnsupportedError), (
        "Expected the real SchemaError to be WRONGLY wrapped as "
        f"BoundedMemoryUnsupportedError (the bug). Got {type(raised).__name__}. "
        "If this is SchemaError, the misclassification has been FIXED."
    )
    assert raised.__cause__ is real_schema_error, (
        "Expected the genuine SchemaError to be hidden as __cause__ of the "
        f"bounded-memory wrapper. Got cause={raised.__cause__!r}."
    )
    print(
        "case A REPRODUCED: a missing-column SchemaError "
        f"({_INCIDENTAL_STREAM_MESSAGE!r}) was masked behind "
        "BoundedMemoryUnsupportedError('Bounded streaming sink failed')."
    )


def _case_b_false_positive_broad_fallback() -> None:
    """A genuine data error containing 'stream' silently broadens collect.

    With allow_broad=True (only legal for PREVIEW_EAGER), the correct
    behaviour for a real *data* error is to surface it. Instead the naive
    classifier routes it into the high-memory broad collect fallback, which
    succeeds and returns a frame -- the data error vanishes entirely.
    """
    broad_calls: list[dict[str, object]] = []

    class _BroadFallbackLazy:
        def collect(self, *args: object, **kwargs: object) -> pl.DataFrame:
            del args
            broad_calls.append(dict(kwargs))
            if kwargs == {"engine": "streaming"}:
                # Genuine data error, NOT a streaming incompatibility, but the
                # message happens to contain 'stream' via 'upstream_premium'.
                raise pl.exceptions.InvalidOperationError(_INCIDENTAL_STREAM_MESSAGE)
            # Broad (non-streaming) collect "succeeds" -> data error swallowed.
            return pl.DataFrame({"x": [1]})

    result = streaming_collect(
        _BroadFallbackLazy(),  # type: ignore[arg-type]
        profile=ExecutionProfile.PREVIEW_EAGER,
        allow_broad=True,
    )

    print("case B broad collect calls:", broad_calls)
    print("case B result:", result.to_dict(as_series=False))

    # The BUG: a genuine InvalidOperationError was swallowed and the broad
    # (high-memory) fallback ran, returning a frame.
    assert broad_calls == [{"engine": "streaming"}, {}], (
        "Expected streaming collect to fail, then a broad collect() fallback "
        f"to run (the bug). Got call sequence {broad_calls!r}. If the broad "
        "collect did not run, the data error was correctly surfaced."
    )
    assert result["x"].to_list() == [1], (
        "Expected the broad fallback to silently succeed and mask the data "
        f"error. Got {result!r}."
    )
    print(
        "case B REPRODUCED: a genuine InvalidOperationError "
        f"({_INCIDENTAL_STREAM_MESSAGE!r}) was swallowed and the high-memory "
        "broad collect fallback ran instead of surfacing the error."
    )


def _case_c_false_negative_bounded_collect() -> None:
    """A real streaming incompatibility lacking 'stream' is NOT wrapped.

    bounded callers (allow_broad=False) are promised a typed
    BoundedMemoryUnsupportedError when Polars cannot honour streaming. A
    genuine streaming-incompat ComputeError whose message lacks the literal
    'stream' instead propagates raw, defeating the bounded-memory contract.
    """
    real_incompat = pl.exceptions.ComputeError(_REAL_INCOMPAT_NO_STREAM_MESSAGE)
    lf = _CollectRaises(real_incompat)

    raised: BaseException | None = None
    try:
        # Default allow_broad=False -> bounded contract: should map to typed.
        streaming_collect(lf, profile=ExecutionProfile.DEPLOY_BATCH)  # type: ignore[arg-type]
    except BaseException as exc:  # noqa: BLE001 - we classify what was raised
        raised = exc

    print("case C raised:", type(raised).__name__, "->", raised)

    # The BUG: the contract promises BoundedMemoryUnsupportedError here, but
    # because the message lacks 'stream' the raw ComputeError escapes.
    assert isinstance(raised, pl.exceptions.ComputeError), (
        "Expected the raw ComputeError to escape unwrapped (the bug). Got "
        f"{type(raised).__name__}."
    )
    assert not isinstance(raised, BoundedMemoryUnsupportedError), (
        "If a BoundedMemoryUnsupportedError was raised, the streaming-incompat "
        "error was correctly classified despite lacking 'stream' -- bug FIXED."
    )
    print(
        "case C REPRODUCED: a real streaming-incompat ComputeError "
        f"({_REAL_INCOMPAT_NO_STREAM_MESSAGE!r}) propagated raw instead of "
        "being wrapped as BoundedMemoryUnsupportedError, breaking the bounded "
        "contract."
    )


def main() -> None:
    _classifier_sanity()
    _case_a_false_positive_bounded_sink()
    _case_b_false_positive_broad_fallback()
    _case_c_false_negative_bounded_collect()
    print(
        "\nV032 REPRODUCED: the 'stream' substring classifier misclassifies in "
        "both directions -- masking genuine data/schema errors (false "
        "positive) and failing to wrap genuine streaming incompatibilities "
        "(false negative)."
    )


if __name__ == "__main__":
    main()
