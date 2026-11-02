"""Impact analysis - compare staging vs production endpoint predictions."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeGuard

import polars as pl

if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ColumnStats:
    """Impact statistics for a single numeric output column.

    The percent figures cover the quotes production prices above zero. A quote production
    prices at zero and staging does not (a newly priced cover, a reversed decline) has no
    percent change: it is counted in ``n_from_zero`` instead, and among ``n_changed``.
    """

    name: str
    n_rows: int
    n_changed: int
    n_from_zero: int
    mean_change_pct: float
    median_change_pct: float
    max_increase_pct: float
    max_decrease_pct: float
    p5: float
    p25: float
    p75: float
    p95: float
    staging_mean: float
    prod_mean: float
    total_premium_change_pct: float | None
    """``None`` when production's total is zero and staging's is not: priced from zero."""


@dataclass
class SegmentRow:
    """One row in a segment breakdown table."""

    value: str
    n_rows: int
    mean_change_pct: float | None
    """Over the segment's quotes production prices above zero; ``None`` when it has none."""
    staging_mean: float
    prod_mean: float


@dataclass
class ImpactReport:
    """Complete impact analysis result."""

    pipeline_name: str
    staging_endpoint: str
    prod_endpoint: str
    dataset_path: str
    total_rows: int
    sampled_rows: int
    column_stats: list[ColumnStats]
    segments: dict[str, list[SegmentRow]]
    first_deploy: str | None = None
    """Why production was not compared, when it was not: an authoritative record that
    production has never been released. ``None`` for a comparison."""


class ImpactEndpointError(RuntimeError):
    """An endpoint impact scores through did not answer: an HTTP error status, or no
    answer at all (unreachable, timed out). Never evidence that production is absent."""

    def __init__(self, url: str, problem: str, *, status: int | None = None) -> None:
        super().__init__(f"{url}: {problem}")
        self.url = url
        self.status = status


class ImpactAlignmentError(ValueError):
    """An endpoint answered with another number of predictions than quotes it was sent.

    Impact pairs staging's, production's and the dataset's rows by position, so any
    other count would compare the wrong quotes; it compares none instead.
    """


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_DEFAULT_BATCH_SIZE = 500
_QUOTE_ENVELOPE_REQUIRED_KEYS = frozenset(
    {"rows", "row_count", "returned_rows", "truncated", "limit"}
)


def _run_batched(
    records: list[dict],
    score_fn: Callable[[list[dict]], object],
    batch_size: int,
    progress: Callable[[str], None] | None,
) -> list:
    """Iterate *records* in batches, call *score_fn* per batch, collect results.

    Parameters
    ----------
    records:
        Full list of input records.
    score_fn:
        Called once per batch with ``score_fn(batch) -> preds``.
        *preds* may be a list (extended onto results) or a scalar (appended).
    batch_size:
        Maximum number of records per batch.
    progress:
        Optional callback receiving a status string per batch.

    Returns
    -------
    list
        Concatenated predictions from all batches, one per record, in order.

    Raises
    ------
    ImpactAlignmentError
        A batch answered with another number of predictions than quotes it was sent,
        checked batch by batch before anything is paired.
    """
    all_preds: list = []
    n_batches = math.ceil(len(records) / batch_size)
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        num = i // batch_size + 1
        if progress:
            progress(f"    batch {num}/{n_batches}")
        all_preds.extend(
            _batch_predictions(score_fn(batch), sent=len(batch), batch=f"{num} of {n_batches}")
        )
    return all_preds


def _batch_predictions(preds: object, *, sent: int, batch: str) -> list:
    """*preds*, a batch's answer, as one prediction per quote sent, its quote envelopes
    unwrapped; a single value answers only a one-quote batch."""
    if isinstance(preds, list):
        rows = _unwrap_prediction_envelopes(preds)
    elif sent == 1:
        rows = [preds]
    else:
        raise ImpactAlignmentError(
            f"Batch {batch} sent {sent} quotes but the endpoint answered with a single "
            "value, not one prediction per quote."
        )
    if len(rows) != sent:
        raise ImpactAlignmentError(
            f"Batch {batch} sent {sent} quotes but the endpoint answered {len(rows)} "
            "predictions; impact compares quotes by position, so it compares none rather "
            "than the wrong ones."
        )
    return rows


def score_endpoint_batched(
    ws: WorkspaceClient,
    endpoint_name: str,
    records: list[dict],
    batch_size: int = _DEFAULT_BATCH_SIZE,
    progress: Callable[[str], None] | None = None,
) -> list:
    """Score records against a serving endpoint in batches."""

    def _score(batch: list[dict]) -> object:
        resp = ws.serving_endpoints.query(name=endpoint_name, dataframe_records=batch)
        return resp.predictions

    return _run_batched(records, _score, batch_size, progress)


def score_http_endpoint_batched(
    endpoint_url: str,
    records: list[dict],
    batch_size: int = _DEFAULT_BATCH_SIZE,
    progress: Callable[[str], None] | None = None,
) -> list:
    """Score records against an HTTP endpoint (container target) in batches.

    Sends POST requests to ``{endpoint_url}/quote`` with JSON arrays.
    """
    import json as _json
    import urllib.error
    import urllib.request

    quote_url = endpoint_url.rstrip("/") + "/quote"

    def _score(batch: list[dict]) -> object:
        body = _json.dumps(batch).encode("utf-8")
        req = urllib.request.Request(
            quote_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ImpactEndpointError(
                quote_url, f"HTTP {exc.code}: {error_body}", status=exc.code
            ) from exc
        except OSError as exc:
            # Unreachable, refused or timed out: no answer, which proves nothing.
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise ImpactEndpointError(quote_url, f"no answer ({reason})") from exc
        return _normalise_http_prediction_payload(result, source=quote_url)

    return _run_batched(records, _score, batch_size, progress)


def _is_haute_quote_envelope(value: object) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and _QUOTE_ENVELOPE_REQUIRED_KEYS.issubset(value)
        and isinstance(value.get("rows"), list)
    )


def _extract_envelope_rows(envelope: dict[str, Any], *, source: str) -> list:
    rows = envelope.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError(f"{source} response field 'rows' must be a JSON array")
    if envelope.get("truncated") is True:
        row_count = envelope.get("row_count", "unknown")
        returned_rows = envelope.get("returned_rows", len(rows))
        raise RuntimeError(
            f"{source} response was truncated: returned {returned_rows!r} "
            f"of {row_count!r} row(s). Increase the quote response row limit "
            "before running impact analysis."
        )

    returned_rows = envelope.get("returned_rows")
    if returned_rows is not None and returned_rows != len(rows):
        raise RuntimeError(
            f"{source} response returned_rows={returned_rows!r} but contained {len(rows)} row(s)"
        )

    row_count = envelope.get("row_count")
    if row_count is not None and row_count != len(rows):
        raise RuntimeError(
            f"{source} response row_count={row_count!r} "
            f"but contained {len(rows)} untruncated row(s)"
        )
    return rows


def _normalise_http_prediction_payload(result: object, *, source: str) -> list:
    if isinstance(result, list):
        return result
    if _is_haute_quote_envelope(result):
        return _extract_envelope_rows(result, source=source)
    raise RuntimeError(
        f"Unexpected HTTP impact response from {source}: expected a JSON array "
        "of predictions or a Haute quote envelope with a 'rows' array."
    )


def _unwrap_prediction_envelopes(preds: list) -> list:
    rows: list = []
    unwrapped = False
    for index, pred in enumerate(preds):
        if _is_haute_quote_envelope(pred):
            rows.extend(
                _extract_envelope_rows(
                    pred,
                    source=f"prediction envelope {index}",
                )
            )
            unwrapped = True
        else:
            rows.append(pred)
    return rows if unwrapped else preds


def _flatten_structs(frame: pl.DataFrame) -> pl.DataFrame:
    """*frame* with each struct column's fields as columns named ``<column>.<field>``.

    A Workbench response holds a one-row table as an object, which Polars reads as a
    struct; its fields become columns so its numbers are compared as a flat response's
    are, nested tables included. A many-row table is a list and stays one column, which
    impact does not compare row by row.
    """
    while True:
        structs = {
            name: dtype for name, dtype in frame.schema.items() if isinstance(dtype, pl.Struct)
        }
        if not structs:
            return frame
        frame = frame.with_columns(
            pl.col(name).struct.rename_fields([f"{name}.{field.name}" for field in dtype.fields])
            for name, dtype in structs.items()
        ).unnest(list(structs))


def _preds_to_df(preds: list) -> pl.DataFrame:
    """Normalise endpoint predictions into a Polars DataFrame, nested objects flattened."""
    preds = _unwrap_prediction_envelopes(preds)
    if not preds:
        return pl.DataFrame()
    first = preds[0]
    if isinstance(first, dict):
        # Every response's fields: an output field Haute omits while it is null may first
        # appear late, and a sample of the first rows would drop it silently.
        return _flatten_structs(pl.DataFrame(preds, infer_schema_length=None))
    if isinstance(first, (list, tuple)):
        cols = [f"output_{i}" for i in range(len(first))]
        return pl.DataFrame(preds, schema=cols, orient="row")
    return pl.DataFrame({"prediction": preds})


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

_CHANGE_EPSILON = 1e-6
_ZERO_BASELINE_EPSILON = 1e-12


def _non_finite_count(series: pl.Series) -> int:
    return int((~series.is_finite().fill_null(False)).sum() or 0)


def _raise_for_non_finite_predictions(stg: pl.Series, prd: pl.Series, name: str) -> None:
    """Refuse an output impact cannot compare: a missing value or a NaN or infinity."""
    for label, series in (("staging", stg), ("production", prd)):
        missing = series.null_count()
        if missing:
            row_word = "row" if missing == 1 else "rows"
            raise ValueError(
                f"Cannot compute impact: {label} predictions leave output {name!r} empty on "
                f"{missing} {row_word} (the answer omits the field, or gives null)."
            )
        count = _non_finite_count(series)
        if count:
            value_word = "value" if count == 1 else "values"
            raise ValueError(
                f"Cannot compute impact: {label} predictions contain "
                f"{count} non-finite {value_word} for output {name!r}."
            )


def _from_zero(stg: pl.Series, prd: pl.Series) -> pl.Series:
    """Whether each quote is priced from zero: production zero, staging not."""
    zero_baseline = (prd.abs() <= _ZERO_BASELINE_EPSILON).fill_null(False)
    return zero_baseline & (stg != prd).fill_null(False)


def _percent_change_series(stg: pl.Series, prd: pl.Series, name: str) -> pl.Series:
    """Each quote's percent change, null for a quote priced from zero (no percentage
    describes it); a zero production value staging matches is no change."""
    _raise_for_non_finite_predictions(stg, prd, name)
    return pl.DataFrame({"stg": stg, "prd": prd}).select(
        pl.when(_from_zero(stg, prd))
        .then(None)
        .when(pl.col("prd").abs() <= _ZERO_BASELINE_EPSILON)
        .then(0.0)
        .otherwise((pl.col("stg") - pl.col("prd")) / pl.col("prd").abs() * 100)
        .alias(name)
    )[name]


def _total_percent_change(staging_sum: float, prod_sum: float) -> float | None:
    """The change in the total, ``None`` when production's total is zero and staging's is
    not (priced from zero)."""
    if abs(prod_sum) <= _ZERO_BASELINE_EPSILON:
        return 0.0 if abs(staging_sum - prod_sum) <= _CHANGE_EPSILON else None
    return (staging_sum - prod_sum) / abs(prod_sum) * 100


def _column_stats(stg: pl.Series, prd: pl.Series, name: str) -> ColumnStats:
    """Compute change statistics for one numeric output column."""
    all_changes = _percent_change_series(stg, prd, name)
    n_from_zero = all_changes.null_count()
    # The percent figures describe the quotes a percentage describes.
    change = all_changes.drop_nulls()
    n = len(change)

    def _f(v: object) -> float:
        """Coerce a Polars scalar to float (handles None from empty series)."""
        return 0.0 if v is None else float(v)  # type: ignore[arg-type]

    s_sum = _f(stg.sum())
    p_sum = _f(prd.sum())
    total_pct = _total_percent_change(s_sum, p_sum)

    return ColumnStats(
        name=name,
        n_rows=len(all_changes),
        n_changed=(int((change.abs() > _CHANGE_EPSILON).sum()) if n else 0) + n_from_zero,
        n_from_zero=n_from_zero,
        mean_change_pct=_f(change.mean()) if n else 0.0,
        median_change_pct=_f(change.median()) if n else 0.0,
        max_increase_pct=_f(change.max()) if n else 0.0,
        max_decrease_pct=_f(change.min()) if n else 0.0,
        p5=_f(change.quantile(0.05)) if n else 0.0,
        p25=_f(change.quantile(0.25)) if n else 0.0,
        p75=_f(change.quantile(0.75)) if n else 0.0,
        p95=_f(change.quantile(0.95)) if n else 0.0,
        staging_mean=_f(stg.mean()) if len(stg) else 0.0,
        prod_mean=_f(prd.mean()) if len(prd) else 0.0,
        total_premium_change_pct=total_pct,
    )


def _segment_breakdown(
    stg_df: pl.DataFrame,
    prd_df: pl.DataFrame,
    input_df: pl.DataFrame,
    output_col: str,
    top_n: int = 10,
) -> dict[str, list[SegmentRow]]:
    """Compute segment breakdown by categorical input columns."""
    cat_cols = [
        c
        for c in input_df.columns
        if input_df[c].dtype == pl.Utf8 and 2 <= input_df[c].n_unique() <= 50
    ]
    if not cat_cols or output_col not in stg_df.columns:
        return {}

    stg_vals = stg_df[output_col]
    prd_vals = prd_df[output_col]
    change = _percent_change_series(stg_vals, prd_vals, output_col)

    result: dict[str, list[SegmentRow]] = {}
    for col in cat_cols:
        tmp = pl.DataFrame({"seg": input_df[col], "chg": change, "stg": stg_vals, "prd": prd_vals})
        grp = (
            tmp.group_by("seg")
            .agg(
                pl.col("chg").mean().alias("mean_chg"),
                pl.col("stg").mean().alias("stg_mean"),
                pl.col("prd").mean().alias("prd_mean"),
                pl.len().alias("n"),
            )
            .filter(pl.col("n") >= 10)
            # Equal changes ordered by segment value, so the cut is the same on every run.
            .sort(
                [pl.col("mean_chg").abs(), "seg"],
                descending=[True, False],
                nulls_last=True,
            )
            .head(top_n)
        )
        rows = [
            SegmentRow(
                value=str(r["seg"]),
                n_rows=int(r["n"]),
                mean_change_pct=None if r["mean_chg"] is None else float(r["mean_chg"]),
                staging_mean=float(r["stg_mean"]),
                prod_mean=float(r["prd_mean"]),
            )
            for r in grp.iter_rows(named=True)
        ]
        if rows:
            result[col] = rows
    return result


_NUMERIC_DTYPES = frozenset(
    {
        pl.Float64,
        pl.Float32,
        pl.Int64,
        pl.Int32,
        pl.Int16,
        pl.Int8,
        pl.UInt64,
        pl.UInt32,
        pl.UInt16,
        pl.UInt8,
    }
)


def build_report(
    staging_preds: list,
    prod_preds: list,
    input_df: pl.DataFrame,
    *,
    pipeline_name: str,
    staging_endpoint: str,
    prod_endpoint: str,
    dataset_path: str,
    total_rows: int,
) -> ImpactReport:
    """Compare staging and production predictions and build an impact report.

    The three row sources pair by position, so each must hold one row per sampled quote;
    any other count raises :class:`ImpactAlignmentError` rather than comparing the
    wrong quotes.
    """
    staging_preds = _unwrap_prediction_envelopes(staging_preds)
    prod_preds = _unwrap_prediction_envelopes(prod_preds)

    sampled_rows = len(input_df)
    if len(staging_preds) != sampled_rows or len(prod_preds) != sampled_rows:
        raise ImpactAlignmentError(
            f"Impact compares {sampled_rows} quotes, but staging answered "
            f"{len(staging_preds)} and production {len(prod_preds)}."
        )

    stg_df = _preds_to_df(staging_preds)
    prd_df = _preds_to_df(prod_preds)

    num_cols = [
        c for c in stg_df.columns if stg_df[c].dtype in _NUMERIC_DTYPES and c in prd_df.columns
    ]

    stats = [_column_stats(stg_df[c], prd_df[c], c) for c in num_cols]

    primary = num_cols[0] if num_cols else None
    segments = (
        _segment_breakdown(stg_df, prd_df, input_df, primary)
        if primary and sampled_rows > 0
        else {}
    )

    return ImpactReport(
        pipeline_name=pipeline_name,
        staging_endpoint=staging_endpoint,
        prod_endpoint=prod_endpoint,
        dataset_path=dataset_path,
        total_rows=total_rows,
        sampled_rows=sampled_rows,
        column_stats=stats,
        segments=segments,
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _fmt_pct(v: float | None) -> str:
    """A percent change; ``None``, a change from a zero production value, as such."""
    if v is None:
        return "from zero"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def _fmt_num(v: float) -> str:
    return f"{v:,.2f}"


def _fmt_int(v: int) -> str:
    return f"{v:,}"


def format_terminal(report: ImpactReport) -> str:
    """Produce a human-readable terminal report."""
    w = 64
    lines: list[str] = []
    lines.append("=" * w)
    lines.append("  IMPACT REPORT")
    lines.append(f"  Pipeline:    {report.pipeline_name}")
    lines.append(f"  Staging:     {report.staging_endpoint}  →  Production: {report.prod_endpoint}")
    sample = ""
    if report.sampled_rows < report.total_rows:
        sample = f" ({_fmt_int(report.sampled_rows)} of {_fmt_int(report.total_rows)} sampled)"
    lines.append(f"  Dataset:     {report.dataset_path}{sample}")
    lines.append("=" * w)

    if report.first_deploy is not None:
        lines.append("")
        lines.append(f"  First deployment - production not compared: {report.first_deploy}")
        lines.append("  Staging predictions scored successfully.")
        lines.append("")
        return "\n".join(lines)

    for cs in report.column_stats:
        lines.append("")
        lines.append(f"  Output: {cs.name}")
        lines.append("  " + "─" * (w - 4))
        lines.append(
            f"  Staging mean:    {_fmt_num(cs.staging_mean):>12}"
            f"     Production mean: {_fmt_num(cs.prod_mean)}"
        )
        pct_changed = cs.n_changed / max(cs.n_rows, 1) * 100
        lines.append(
            f"  Rows changed:    {_fmt_int(cs.n_changed):>12}"
            f" / {_fmt_int(cs.n_rows)} ({pct_changed:.1f}%)"
        )
        if cs.n_from_zero:
            lines.append(
                f"  From zero:       {_fmt_int(cs.n_from_zero):>12}"
                "     (production priced 0; left out of the % figures)"
            )
        lines.append(f"  Mean change:     {_fmt_pct(cs.mean_change_pct):>12}")
        lines.append(f"  Median change:   {_fmt_pct(cs.median_change_pct):>12}")
        lines.append(f"  Premium impact:  {_fmt_pct(cs.total_premium_change_pct):>12}")
        lines.append(f"  Max increase:    {_fmt_pct(cs.max_increase_pct):>12}")
        lines.append(f"  Max decrease:    {_fmt_pct(cs.max_decrease_pct):>12}")
        lines.append("")
        lines.append(
            f"  Distribution:  P5={_fmt_pct(cs.p5)}  P25={_fmt_pct(cs.p25)}"
            f"  P50={_fmt_pct(cs.median_change_pct)}"
            f"  P75={_fmt_pct(cs.p75)}  P95={_fmt_pct(cs.p95)}"
        )

    for col_name, seg_rows in report.segments.items():
        lines.append("")
        lines.append(f"  Segment: {col_name}")
        hdr = (
            f"  {'Value':<20} {'Rows':>8}  {'Avg Change':>12}  {'Stg Mean':>12}  {'Prod Mean':>12}"
        )
        lines.append(hdr)
        lines.append(f"  {'─' * 20} {'─' * 8}  {'─' * 12}  {'─' * 12}  {'─' * 12}")
        for sr in seg_rows:
            lines.append(
                f"  {sr.value:<20} {_fmt_int(sr.n_rows):>8}"
                f"  {_fmt_pct(sr.mean_change_pct):>12}"
                f"  {_fmt_num(sr.staging_mean):>12}"
                f"  {_fmt_num(sr.prod_mean):>12}"
            )

    lines.append("")
    return "\n".join(lines)


def format_markdown(report: ImpactReport) -> str:
    """Produce a Markdown report suitable for GitHub Step Summary."""
    lines: list[str] = []
    lines.append("# Impact Report")
    lines.append("")
    lines.append(f"**Pipeline:** {report.pipeline_name}  ")
    lines.append(
        f"**Staging:** `{report.staging_endpoint}` → **Production:** `{report.prod_endpoint}`  "
    )
    sample = ""
    if report.sampled_rows < report.total_rows:
        sample = f" ({report.sampled_rows:,} of {report.total_rows:,} sampled)"
    lines.append(f"**Dataset:** `{report.dataset_path}`{sample}  ")

    if report.first_deploy is not None:
        lines.append("")
        lines.append(
            f"> First deployment - production not compared: {report.first_deploy}"
            " Staging predictions scored successfully."
        )
        lines.append("")
        return "\n".join(lines)

    for cs in report.column_stats:
        pct_changed = cs.n_changed / max(cs.n_rows, 1) * 100
        lines.append("")
        lines.append(f"## Output: `{cs.name}`")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|------:|")
        lines.append(f"| Staging mean | {_fmt_num(cs.staging_mean)} |")
        lines.append(f"| Production mean | {_fmt_num(cs.prod_mean)} |")
        lines.append(f"| Rows changed | {cs.n_changed:,} / {cs.n_rows:,} ({pct_changed:.1f}%) |")
        if cs.n_from_zero:
            lines.append(f"| Priced from zero (left out of the % figures) | {cs.n_from_zero:,} |")
        lines.append(f"| Mean change | {_fmt_pct(cs.mean_change_pct)} |")
        lines.append(f"| Median change | {_fmt_pct(cs.median_change_pct)} |")
        lines.append(f"| Total premium impact | {_fmt_pct(cs.total_premium_change_pct)} |")
        lines.append(f"| Max increase | {_fmt_pct(cs.max_increase_pct)} |")
        lines.append(f"| Max decrease | {_fmt_pct(cs.max_decrease_pct)} |")
        lines.append("")
        lines.append("**Distribution:**")
        lines.append("")
        lines.append("| P5 | P25 | P50 | P75 | P95 |")
        lines.append("|---:|----:|----:|----:|----:|")
        lines.append(
            f"| {_fmt_pct(cs.p5)} | {_fmt_pct(cs.p25)}"
            f" | {_fmt_pct(cs.median_change_pct)}"
            f" | {_fmt_pct(cs.p75)} | {_fmt_pct(cs.p95)} |"
        )

    for col_name, seg_rows in report.segments.items():
        lines.append("")
        lines.append(f"### Segment: {col_name}")
        lines.append("")
        lines.append("| Segment | Rows | Avg Change | Staging Mean | Prod Mean |")
        lines.append("|---------|-----:|-----------:|-------------:|----------:|")
        for sr in seg_rows:
            lines.append(
                f"| {sr.value} | {sr.n_rows:,}"
                f" | {_fmt_pct(sr.mean_change_pct)}"
                f" | {_fmt_num(sr.staging_mean)}"
                f" | {_fmt_num(sr.prod_mean)} |"
            )

    lines.append("")
    return "\n".join(lines)
