"""How strongly each chosen feature relates to a target, and whether a key is unique.

An Explore node's grid shows rows, not relationships: seeing which features move
a target, or whether the columns a user joins on really identify a row, meant
building a pivot per feature over whatever the preview held. This answers both
over the whole data point the node reads. It stays bounded whatever the data's
size: a numeric feature is read as a fixed number of equal-width bins, a
categorical one keeps its heaviest levels and folds the rest into one, and the
key check is a handful of counts rather than the duplicated rows themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl
from fastapi import HTTPException

from haute._analysis_results import SynchronousAnalysisCache
from haute._data_points import (
    CacheRequiredError,
    ConsumerPoint,
    DataPointResolver,
    LeasedPointFrame,
    NodeDataPointInvalidError,
    PointColumnsMissingError,
    PointDataChangedError,
    consumer_point,
)
from haute._execution_context import ExecutionCancellationToken, ExecutionContext
from haute._frame_profile import (
    categorical_label_expr,
    is_unhashable_dtype,
    numeric_bin_scale,
    numeric_finite_mask,
)
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._polars_utils import cancellable_streaming_collect
from haute.routes._node_data_service import NodeDataService, node_data_project_root
from haute.routes._synchronous_analysis import run_synchronous_analysis
from haute.schemas import (
    ExploreKeyCheck,
    ExploreRelationship,
    ExploreRelationshipLevel,
    ExploreRelationshipsRequest,
    ExploreRelationshipsResponse,
)

EXPLORE_RELATIONSHIPS_VERSION = 1
NUMERIC_RELATIONSHIP_BINS = 10

_MISSING_LABEL = "(missing)"
_OTHER_LABEL = "Other"

LevelKind = Literal["value", "bin", "missing", "other"]


@dataclass(frozen=True, slots=True)
class _ExploreRelationships:
    """The relationships and key check read, and which version of the data they describe.

    The version is the lease's rather than the one the point reported when the
    request resolved: a refresh between the two makes those different, and the
    client compares this against its own point to decide whether the answer
    describes the data it is looking at.
    """

    data_version: str
    total_rows: int
    used_rows: int
    relationships: tuple[ExploreRelationship, ...]
    key_check: ExploreKeyCheck | None


@dataclass(frozen=True, slots=True)
class _Question:
    """The request once normalised: what the cache is keyed by and what is read."""

    target: str | None
    weight: str | None
    features: tuple[str, ...]
    key_columns: tuple[str, ...]
    level_limit: int

    @property
    def referenced(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for column in (self.target, self.weight, *self.features, *self.key_columns):
            if column is not None:
                seen.setdefault(column, None)
        return tuple(seen)


def _unprocessable(message: str) -> HTTPException:
    """A question this data cannot answer; `detail` stays a plain string (issue #76)."""
    return HTTPException(status_code=422, detail=message)


def _distinct(columns: list[str]) -> tuple[str, ...]:
    """The columns named, once each, in the order first named; blank entries dropped.

    A name is used exactly as given: trimming only recognises a blank entry.
    """
    seen: dict[str, None] = {}
    for column in columns:
        if column.strip():
            seen.setdefault(column, None)
    return tuple(seen)


def _named(column: str | None) -> str | None:
    return column if column is not None and column.strip() else None


def _question(body: ExploreRelationshipsRequest) -> _Question:
    features = _distinct(body.features)
    key_columns = _distinct(body.key_columns)
    target = _named(body.target)
    weight = _named(body.weight)
    if not features and not key_columns:
        raise _unprocessable("Choose at least one feature or key column.")
    if features and target is None:
        raise _unprocessable("Choose a target to relate the features to.")
    if target is not None:
        if weight == target:
            raise _unprocessable("The weight column must differ from the target column.")
        for feature in features:
            if feature == target:
                raise _unprocessable(f"{feature!r} cannot be both the target and a feature.")
            if feature == weight:
                raise _unprocessable(f"{feature!r} cannot be both the weight and a feature.")
    return _Question(
        target=target,
        weight=weight,
        features=features,
        key_columns=key_columns,
        level_limit=body.level_limit,
    )


def _edge_text(value: int | float) -> str:
    """A bin boundary as a label: integers exactly, other numbers to six figures."""
    return str(value) if isinstance(value, int) else f"{value:.6g}"


def _column_list(columns: list[str]) -> str:
    return ", ".join(repr(column) for column in columns)


def _strength(between: float, total_weight: float, s: float, q: float, centre: float) -> float:
    """The weighted correlation ratio: the share of the target's variance the levels explain.

    The sums are of the target centred on its weighted mean ``centre``, so they
    stay at the scale of the target's spread whatever its magnitude. ``between``
    is the sum of S_l²/W_l over the groups; S²/W, the rounding left in the
    centred total, comes off it as it does off the total.
    """
    if total_weight <= 0:
        return 0.0
    grand = s * s / total_weight
    total = q - grand
    # A spread below a millionth of a millionth of the target's own scale is
    # rounding, not variance: the target is constant.
    if total <= total_weight * (1e-12 * max(abs(centre), 1.0)) ** 2:
        return 0.0
    return min(max((between - grand) / total, 0.0), 1.0)


def _level(
    label: str, kind: LevelKind, rows: int, weight: float, s: float, centre: float
) -> ExploreRelationshipLevel:
    """One level; ``s`` sums the centred target, so its mean is ``centre + s / weight``."""
    return ExploreRelationshipLevel(
        label=label,
        kind=kind,
        rows=rows,
        weight=weight,
        target_mean=centre + s / weight if weight else None,
    )


class ExploreRelationshipsService:
    """Target relationships and a key check for an Explore node, over its shared data point."""

    def __init__(self, node_data: NodeDataService) -> None:
        self._node_data = node_data
        self._cache: SynchronousAnalysisCache = SynchronousAnalysisCache()

    def relationships(
        self,
        body: ExploreRelationshipsRequest,
        *,
        cancellation_token: ExecutionCancellationToken | None = None,
    ) -> ExploreRelationshipsResponse:
        """Return the relationships and key check, or say the data is not there to read.

        The token is the request's, so a client that abandons the question stops
        the scan rather than leaving it holding an admission and a lease.
        """
        consumer, resolver = self._consumer(body)
        question = _question(body)
        # The columns asked about may be ones the saved node does not read, so
        # the demand covers them: a snapshot without one of them is not current
        # for this request rather than answering from data that lacks it.
        reader = ConsumerPoint(
            consumer.consumer_node_id,
            consumer.point,
            consumer.demand
            if consumer.demand.names is None
            else NodeSnapshotColumns.of({*consumer.demand.names, *question.referenced}),
        )
        point = self._node_data.point_for(reader, resolver)

        request = {
            "analysis": "explore_relationships",
            "version": EXPLORE_RELATIONSHIPS_VERSION,
            "target": question.target,
            "weight": question.weight,
            "features": list(question.features),
            "key_columns": list(question.key_columns),
            "level_limit": question.level_limit,
        }
        try:
            result = run_synchronous_analysis(
                resolver,
                reader.point,
                reader.demand,
                operation="explore_relationships",
                request=request,
                cache=self._cache,
                compute=lambda leased, context: self._compute(question, leased, context),
                cancellation_token=cancellation_token,
            )
        except (CacheRequiredError, PointDataChangedError):
            return ExploreRelationshipsResponse(status="cache_required", point=point)
        except PointColumnsMissingError as exc:
            raise _unprocessable(
                f"Column(s) {_column_list(exc.missing)} are not in the data this node reads."
            ) from exc
        return ExploreRelationshipsResponse(
            status="ok",
            point=point,
            data_version=result.data_version,
            total_rows=result.total_rows,
            target=question.target,
            weight=question.weight,
            used_rows=result.used_rows,
            relationships=list(result.relationships),
            key_check=result.key_check,
        )

    def _consumer(
        self, body: ExploreRelationshipsRequest
    ) -> tuple[ConsumerPoint, DataPointResolver]:
        try:
            consumer = consumer_point(body.graph, body.node_id)
        except NodeDataPointInvalidError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resolver = DataPointResolver(
            body.graph, source=body.source, store=NodeSnapshotStore(node_data_project_root())
        )
        return consumer, resolver

    def _compute(
        self,
        question: _Question,
        leased: LeasedPointFrame,
        context: ExecutionContext,
    ) -> _ExploreRelationships:
        frame = leased.scan
        schema = frame.collect_schema()
        missing = [column for column in question.referenced if column not in schema]
        if missing:
            raise _unprocessable(
                f"Column(s) {_column_list(missing)} are not in the data this node reads."
            )
        target, weight = question.target, question.weight
        if target is not None:
            dtype = schema[target]
            if not (dtype.is_numeric() or dtype == pl.Boolean):
                raise _unprocessable(
                    f"The target must be numeric or boolean, but {target!r} is {dtype}."
                )
        if weight is not None and not schema[weight].is_numeric():
            raise _unprocessable(f"The weight must be numeric, but {weight!r} is {schema[weight]}.")
        for feature in question.features:
            dtype = schema[feature]
            if is_unhashable_dtype(dtype) or dtype.is_nested():
                raise _unprocessable(f"{feature!r} is {dtype}, which cannot be grouped.")
        for column in question.key_columns:
            if is_unhashable_dtype(schema[column]):
                raise _unprocessable(
                    f"Key column {column!r} is {schema[column]}, "
                    "which cannot be compared for uniqueness."
                )

        total_rows = int(
            cancellable_streaming_collect(
                frame.select(pl.len().alias("rows")), execution_context=context
            ).item(0, "rows")
        )
        used_rows = 0
        relationships: list[ExploreRelationship] = []
        if target is not None:
            y = pl.col(target).cast(pl.Float64)
            w = pl.col(weight).cast(pl.Float64) if weight is not None else pl.lit(1.0)
            used_filter = y.is_finite()
            if weight is not None:
                used_filter = used_filter & w.is_finite() & (w >= 0)
            # Only the rows a target and weight can be read from are related:
            # the features are analysed over exactly these.
            used = frame.filter(used_filter.fill_null(False)).with_columns(w.alias("__w"))
            totals = cancellable_streaming_collect(
                used.select(
                    pl.len().alias("rows"),
                    pl.col("__w").sum().alias("W"),
                    (pl.col("__w") * y).sum().alias("S"),
                ),
                execution_context=context,
            )
            used_rows = int(totals.item(0, "rows"))
            total_weight = float(totals.item(0, "W") or 0.0)
            centre = float(totals.item(0, "S") or 0.0) / total_weight if total_weight else 0.0
            # Centring before squaring keeps the sums at the scale of the
            # target's spread: a target of 1,000,000 +/- 1 would otherwise lose
            # its whole variance to rounding.
            centred = y - centre
            used = used.with_columns((pl.col("__w") * centred).alias("__wy"))
            moments = cancellable_streaming_collect(
                used.select(
                    pl.col("__wy").sum().alias("S"),
                    (pl.col("__wy") * centred).sum().alias("Q"),
                ),
                execution_context=context,
            )
            s = float(moments.item(0, "S") or 0.0)
            q = float(moments.item(0, "Q") or 0.0)
            for feature in question.features:
                dtype = schema[feature]
                kind: Literal["numeric", "categorical"]
                if dtype.is_numeric():
                    levels, between = self._numeric_levels(used, feature, dtype, centre, context)
                    truncated = False
                    kind = "numeric"
                else:
                    levels, between, truncated = self._categorical_levels(
                        used, feature, dtype, question.level_limit, centre, context
                    )
                    kind = "categorical"
                relationships.append(
                    ExploreRelationship(
                        feature=feature,
                        kind=kind,
                        strength=_strength(between, total_weight, s, q, centre),
                        levels=levels,
                        levels_truncated=truncated,
                    )
                )
            relationships.sort(key=lambda r: (-r.strength, r.feature))

        key_check = (
            self._key_check(frame, question.key_columns, total_rows, context)
            if question.key_columns
            else None
        )
        return _ExploreRelationships(
            data_version=leased.data_version,
            total_rows=total_rows,
            used_rows=used_rows,
            relationships=tuple(relationships),
            key_check=key_check,
        )

    @staticmethod
    def _between(grouped: pl.LazyFrame | pl.DataFrame) -> float:
        """Σ S_l²/W_l over every group with weight, before any truncation."""
        value = (
            grouped.lazy()
            .filter(pl.col("W") > 0)
            .select((pl.col("S") ** 2 / pl.col("W")).sum().alias("between"))
            .collect()
            .item(0, "between")
        )
        return float(value or 0.0)

    def _numeric_levels(
        self,
        used: pl.LazyFrame,
        feature: str,
        dtype: pl.DataType,
        centre: float,
        context: ExecutionContext,
    ) -> tuple[list[ExploreRelationshipLevel], float]:
        # The profile's exact bins: extrema in the column's own dtype and integer
        # boundaries for integers, so large identifiers are not merged together.
        finite = numeric_finite_mask(pl.col(feature), dtype)
        bounds = cancellable_streaming_collect(
            used.filter(finite).select(
                pl.col(feature).min().alias("lo"), pl.col(feature).max().alias("hi")
            ),
            execution_context=context,
        )
        lo, hi = bounds.item(0, "lo"), bounds.item(0, "hi")
        scale = (
            numeric_bin_scale(feature, dtype, lo, hi, NUMERIC_RELATIONSHIP_BINS)
            if lo is not None and lo != hi
            else None
        )
        if lo is None:
            bin_expr = pl.lit(None, dtype=pl.Int64)
        elif scale is None:
            bin_expr = pl.when(finite).then(pl.lit(0, dtype=pl.Int64))
        else:
            bin_expr = pl.when(finite).then(scale.index().cast(pl.Int64))
        grouped = cancellable_streaming_collect(
            used.group_by(bin_expr.alias("bin"))
            .agg(
                pl.len().alias("rows"),
                pl.col("__w").sum().alias("W"),
                pl.col("__wy").sum().alias("S"),
            )
            .sort("bin", nulls_last=True),
            execution_context=context,
        )
        between = self._between(grouped)
        levels: list[ExploreRelationshipLevel] = []
        for row in grouped.iter_rows(named=True):
            index = row["bin"]
            kind: LevelKind
            if index is None:
                label, kind = _MISSING_LABEL, "missing"
            elif scale is None:
                label, kind = _edge_text(lo), "bin"
            else:
                label = f"{_edge_text(scale.starts[index])} to {_edge_text(scale.end(index))}"
                kind = "bin"
            levels.append(
                _level(
                    label,
                    kind,
                    int(row["rows"]),
                    float(row["W"] or 0.0),
                    float(row["S"] or 0.0),
                    centre,
                )
            )
        return levels, between

    def _categorical_levels(
        self,
        used: pl.LazyFrame,
        feature: str,
        dtype: pl.DataType,
        level_limit: int,
        centre: float,
        context: ExecutionContext,
    ) -> tuple[list[ExploreRelationshipLevel], float, bool]:
        # The profile's labels: Binary decoded leniently and Duration formatted,
        # where a plain String cast would fail the whole query.
        grouped = used.group_by(categorical_label_expr(feature, dtype).alias("label")).agg(
            pl.len().alias("rows"),
            pl.col("__w").sum().alias("W"),
            pl.col("__wy").sum().alias("S"),
        )
        is_value = pl.col("label").is_not_null()
        # Only the heaviest levels and a handful of sums are collected, so a
        # feature with millions of distinct values never materialises them all.
        sums = cancellable_streaming_collect(
            grouped.select(
                (pl.col("S") ** 2 / pl.col("W")).filter(pl.col("W") > 0).sum().alias("between"),
                is_value.sum().alias("value_levels"),
                pl.col("rows").filter(is_value).sum().alias("value_rows"),
                pl.col("W").filter(is_value).sum().alias("value_W"),
                pl.col("S").filter(is_value).sum().alias("value_S"),
            ),
            execution_context=context,
        )
        top = cancellable_streaming_collect(
            grouped.filter(is_value)
            .sort(["W", "label"], descending=[True, False])
            .head(level_limit),
            execution_context=context,
        )
        missing = cancellable_streaming_collect(
            grouped.filter(pl.col("label").is_null()), execution_context=context
        )
        levels = [
            _level(
                row["label"], "value", int(row["rows"]), float(row["W"]), float(row["S"]), centre
            )
            for row in top.iter_rows(named=True)
        ]
        truncated = int(sums.item(0, "value_levels")) > top.height
        if truncated:
            # The folded levels are whatever the value totals hold beyond the top ones.
            levels.append(
                _level(
                    _OTHER_LABEL,
                    "other",
                    int(sums.item(0, "value_rows") or 0) - int(top["rows"].sum()),
                    float(sums.item(0, "value_W") or 0.0) - float(top["W"].sum()),
                    float(sums.item(0, "value_S") or 0.0) - float(top["S"].sum()),
                    centre,
                )
            )
        if missing.height:
            levels.append(
                _level(
                    _MISSING_LABEL,
                    "missing",
                    int(missing.item(0, "rows")),
                    float(missing.item(0, "W")),
                    float(missing.item(0, "S")),
                    centre,
                )
            )
        return levels, float(sums.item(0, "between") or 0.0), truncated

    @staticmethod
    def _key_check(
        frame: pl.LazyFrame,
        key_columns: tuple[str, ...],
        rows: int,
        context: ExecutionContext,
    ) -> ExploreKeyCheck:
        # Over every row, not only the ones a target could be read from: a key
        # identifies rows whatever their target says.
        distinct = (
            pl.col(key_columns[0]).n_unique()
            if len(key_columns) == 1
            else pl.struct(list(key_columns)).n_unique()
        )
        counts = cancellable_streaming_collect(
            frame.select(
                distinct.alias("distinct"),
                pl.any_horizontal([pl.col(c).is_null() for c in key_columns])
                .sum()
                .alias("null_rows"),
            ),
            execution_context=context,
        )
        distinct_keys = int(counts.item(0, "distinct"))
        null_key_rows = int(counts.item(0, "null_rows"))
        duplicate_rows = rows - distinct_keys
        return ExploreKeyCheck(
            columns=list(key_columns),
            rows=rows,
            distinct_keys=distinct_keys,
            duplicate_rows=duplicate_rows,
            null_key_rows=null_key_rows,
            unique=duplicate_rows == 0 and null_key_rows == 0,
        )
