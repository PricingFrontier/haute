"""Bounded pivot calculations over an existing Explore dataframe cache."""

from __future__ import annotations

import ast
import math
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from functools import cmp_to_key, reduce
from operator import or_
from typing import Any, cast

import polars as pl
from fastapi import HTTPException

from haute._cache import canonical_json
from haute._column_summary import is_unhashable_dtype
from haute._execution_admission import ExecutionAdmissionError, create_admitted_execution_context
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._explore_pivots import validate_explore_pivots
from haute._hashing import content_hash_bytes
from haute._lru_cache import LRUCache
from haute._polars_utils import cancellable_streaming_collect
from haute._sandbox import safe_globals, validate_user_code
from haute._types import (
    ExplorePivotConfig,
    ExplorePivotFormula,
    ExplorePivotMember,
    ExplorePivotRowPlacement,
    ExplorePivotValuePlacement,
)
from haute.routes._background_jobs import CancellableJobRegistry, JobCancellation
from haute.routes._explore_service import ExploreCacheSpec, ExploreService
from haute.routes._job_lifecycle import JobLifecycle, bind_running_execution_metrics_publisher
from haute.routes._job_store import JobStore
from haute.schemas import (
    ExecutionMetricsPayload,
    ExplorePivotCell,
    ExplorePivotFailure,
    ExplorePivotMemberKey,
    ExplorePivotMemberKind,
    ExplorePivotMemberOption,
    ExplorePivotMembersRequest,
    ExplorePivotMembersResponse,
    ExplorePivotPath,
    ExplorePivotResult,
    ExplorePivotRunRequest,
    ExplorePivotRunResponse,
    ExplorePivotStatusResponse,
    ExplorePivotValueIdentity,
    ExploreRunRequest,
)

EXPLORE_PIVOT_RESULT_VERSION = 1
MAX_ROW_GROUPS = 500
MAX_COLUMN_GROUPS = 100
MAX_DISPLAY_CELLS = 50_000
MAX_FILTER_MEMBERS = 500

_COUNT_AGGREGATIONS = frozenset({"count", "distinct_count"})
_FLOAT_BASE_TYPES = (pl.Float32, pl.Float64)
_TEXT_BASE_TYPES = (pl.String, pl.Categorical, pl.Enum)
_MEMBER_KIND_ORDER = {
    "boolean": 0,
    "integer": 1,
    "float": 2,
    "decimal": 3,
    "string": 4,
    "date": 5,
    "datetime": 6,
    "time": 7,
    "null": 8,
    "nan": 9,
}
_MISSING_MEMBER_KINDS = frozenset({"null", "nan"})
_NON_FINITE_WARNING = "Non-finite aggregate results were rendered as blank."


class PivotContractError(Exception):
    """A typed, user-remediable pivot calculation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        remediation: str,
        **dimensions: str | int,
    ) -> None:
        super().__init__(message)
        self.failure = ExplorePivotFailure(
            reason_code=code,
            message=message,
            remediation=remediation,
            dimensions=dimensions,
        )


@dataclass(frozen=True, slots=True)
class PivotCalculationSpec:
    """Resolved identities for one pivot run."""

    explore: ExploreCacheSpec
    pivot: ExplorePivotConfig
    calculation_key: str
    result_cache_key: str
    family_key: tuple[str, str, str, str, str]


TypedMemberTuple = tuple[ExplorePivotMemberKind, str | float | int | bool | None]
TypedPathTuple = tuple[TypedMemberTuple, ...]


def _member_key(value: Any) -> ExplorePivotMemberKey:
    """Encode one Polars scalar without collapsing typed missing values."""

    if value is None:
        return ExplorePivotMemberKey(kind="null", value=None)
    if isinstance(value, float):
        if math.isnan(value):
            return ExplorePivotMemberKey(kind="nan", value=None)
        if not math.isfinite(value):
            raise PivotContractError(
                "invalid_pivot_member",
                "Pivot dimensions contain a non-finite value.",
                "Clean infinite dimension values before building the pivot.",
            )
        return ExplorePivotMemberKey(kind="float", value=value)
    if isinstance(value, bool):
        return ExplorePivotMemberKey(kind="boolean", value=value)
    if isinstance(value, int):
        return ExplorePivotMemberKey(kind="integer", value=str(value))
    if isinstance(value, Decimal):
        return ExplorePivotMemberKey(kind="decimal", value=str(value))
    if isinstance(value, datetime):
        return ExplorePivotMemberKey(kind="datetime", value=value.isoformat())
    if isinstance(value, date):
        return ExplorePivotMemberKey(kind="date", value=value.isoformat())
    if isinstance(value, datetime_time):
        return ExplorePivotMemberKey(kind="time", value=value.isoformat())
    if isinstance(value, str):
        return ExplorePivotMemberKey(kind="string", value=value)
    raise PivotContractError(
        "invalid_pivot_field",
        "Pivot dimensions must contain supported scalar values.",
        "Choose a boolean, numeric, text, date, datetime, or time field.",
        actual_type=type(value).__name__,
    )


def _member_tuple(value: Any) -> TypedMemberTuple:
    key = _member_key(value)
    return key.kind, key.value


def _path_tuple(row: Mapping[str, Any], fields: Sequence[str]) -> TypedPathTuple:
    return tuple(_member_tuple(row[field]) for field in fields)


def _path_model(path: TypedPathTuple) -> ExplorePivotPath:
    return ExplorePivotPath(
        members=[ExplorePivotMemberKey(kind=kind, value=value) for kind, value in path]
    )


def _member_label(key: ExplorePivotMemberKey) -> str:
    if key.kind == "null":
        return "(blank)"
    if key.kind == "nan":
        return "(NaN)"
    if key.kind == "boolean":
        return str(key.value).lower()
    return str(key.value)


def _member_sort_key(member: TypedMemberTuple) -> tuple[int, Any]:
    kind, value = member
    if kind == "boolean":
        comparable: Any = int(cast(bool, value))
    elif kind in {"integer", "decimal"}:
        comparable = Decimal(cast(str, value))
    elif kind == "float":
        comparable = cast(float, value)
    else:
        comparable = "" if value is None else str(value)
    return _MEMBER_KIND_ORDER[kind], comparable


def _path_sort_key(path: TypedPathTuple) -> tuple[tuple[int, Any], ...]:
    return tuple(_member_sort_key(member) for member in path)


def _compare_member(
    left: TypedMemberTuple,
    right: TypedMemberTuple,
    direction: str,
) -> int:
    """Compare dimension members while keeping missing values at the end."""

    left_missing = left[0] in _MISSING_MEMBER_KINDS
    right_missing = right[0] in _MISSING_MEMBER_KINDS
    if left_missing or right_missing:
        if left_missing != right_missing:
            return 1 if left_missing else -1
        left_rank = _MEMBER_KIND_ORDER[left[0]]
        right_rank = _MEMBER_KIND_ORDER[right[0]]
        return (left_rank > right_rank) - (left_rank < right_rank)

    left_key = _member_sort_key(left)
    right_key = _member_sort_key(right)
    comparison = (left_key > right_key) - (left_key < right_key)
    return -comparison if direction == "descending" else comparison


def _compare_row_paths(
    left: TypedPathTuple,
    right: TypedPathTuple,
    placements: Sequence[ExplorePivotRowPlacement],
    sort_by: str | None,
) -> int:
    for left_member, right_member, placement in zip(left, right, placements, strict=True):
        direction = placement["sort"] if placement["id"] == sort_by else "ascending"
        comparison = _compare_member(left_member, right_member, direction)
        if comparison:
            return comparison
    return 0


def _aggregate_comparable(
    value: str | float | int | bool,
    *,
    dtype: pl.DataType,
    aggregation: str,
) -> Decimal | int | str:
    if aggregation in _COUNT_AGGREGATIONS or dtype.is_numeric():
        return Decimal(str(value))
    if dtype.base_type() == pl.Boolean:
        return int(cast(bool, value))
    return str(value)


def _compare_aggregate_values(
    left: str | float | int | bool | None,
    right: str | float | int | bool | None,
    *,
    dtype: pl.DataType,
    aggregation: str,
    direction: str,
) -> int:
    """Compare aggregate values while keeping blank results at the end."""

    if left is None or right is None:
        if left is right:
            return 0
        return 1 if left is None else -1
    # Both values come from the same validated placement, so their comparable
    # representation has the same runtime type even though the helper's union
    # return type cannot express that relationship.
    left_value = cast(Any, _aggregate_comparable(left, dtype=dtype, aggregation=aggregation))
    right_value = cast(Any, _aggregate_comparable(right, dtype=dtype, aggregation=aggregation))
    comparison = int((left_value > right_value) - (left_value < right_value))
    return -comparison if direction == "descending" else comparison


def _combined_path(
    row_fields: Sequence[str],
    row_path: TypedPathTuple,
    column_fields: Sequence[str],
    column_path: TypedPathTuple,
) -> TypedPathTuple | None:
    """Return the union path, or ``None`` for an impossible shared-field cell."""

    members_by_field = dict(zip(row_fields, row_path))
    for field, member in zip(column_fields, column_path):
        existing = members_by_field.get(field)
        if existing is not None and existing != member:
            return None
        members_by_field[field] = member
    return tuple(members_by_field[field] for field in dict.fromkeys([*row_fields, *column_fields]))


def _is_supported_dimension_dtype(dtype: pl.DataType) -> bool:
    base = dtype.base_type()
    return (
        base in (*_TEXT_BASE_TYPES, pl.Boolean, pl.Decimal, pl.Date, pl.Datetime, pl.Time, pl.Null)
        or dtype.is_integer()
        or base in _FLOAT_BASE_TYPES
    )


def _member_kind_matches_dtype(kind: str, dtype: pl.DataType) -> bool:
    if kind == "null":
        return True
    base = dtype.base_type()
    if kind == "string":
        return base in _TEXT_BASE_TYPES
    if kind == "boolean":
        return base == pl.Boolean
    if kind == "integer":
        return dtype.is_integer()
    if kind in {"float", "nan"}:
        return base in _FLOAT_BASE_TYPES
    if kind == "decimal":
        return base == pl.Decimal
    if kind == "date":
        return base == pl.Date
    if kind == "datetime":
        return base == pl.Datetime
    if kind == "time":
        return base == pl.Time
    return False


def _member_literal(member: ExplorePivotMember) -> Any:
    kind = member["kind"]
    value = member["value"]
    if kind == "integer":
        return int(cast(str, value))
    if kind == "float":
        return float(cast(float | int, value))
    if kind == "decimal":
        return Decimal(cast(str, value))
    if kind == "date":
        return date.fromisoformat(cast(str, value))
    if kind == "datetime":
        return datetime.fromisoformat(cast(str, value))
    if kind == "time":
        return datetime_time.fromisoformat(cast(str, value))
    return value


def _filter_member_expression(
    field: str,
    dtype: pl.DataType,
    member: ExplorePivotMember,
) -> pl.Expr:
    kind = member["kind"]
    if not _member_kind_matches_dtype(kind, dtype):
        raise PivotContractError(
            "invalid_pivot_filter_member",
            f"Filter member kind '{kind}' does not match field '{field}'.",
            "Refresh the filter members and choose a value for this field.",
            field=field,
            member_kind=kind,
        )
    column = pl.col(field)
    if kind == "null":
        return column.is_null()
    if kind == "nan":
        return column.is_nan()
    return column.eq(pl.lit(_member_literal(member)))


def _unique_filter_members(members: Sequence[ExplorePivotMember]) -> list[ExplorePivotMember]:
    by_key = {
        canonical_json({"kind": member["kind"], "value": member["value"]}): member
        for member in members
    }
    return [by_key[key] for key in sorted(by_key)]


def _value_alias(value_id: str) -> str:
    digest = content_hash_bytes(value_id.encode())[:20]
    return f"__haute_pivot_value_{digest}"


def _group_alias(field: str) -> str:
    digest = content_hash_bytes(field.encode())[:20]
    return f"__haute_pivot_group_{digest}"


def _formula_expression(formula: ExplorePivotFormula) -> pl.Expr:
    """Compile one configured expression under the normal user-code sandbox."""

    try:
        tree = ast.parse(formula["expression"], mode="eval")
        validate_user_code(formula["expression"])
        expression = eval(compile(tree, "<pivot formula>", "eval"), safe_globals(pl=pl), {})  # noqa: S307
        if not isinstance(expression, pl.Expr):
            raise TypeError("formula must return one Polars expression")
        return expression.alias(_value_alias(formula["id"]))
    except Exception as exc:
        raise PivotContractError(
            "invalid_pivot_formula",
            "Pivot formula is invalid.",
            "Use a safe Python expression that returns one Polars expression.",
            formula_id=formula["id"],
        ) from exc


def _compile_formulas(
    formulas: Sequence[ExplorePivotFormula],
    schema: Mapping[str, pl.DataType],
) -> list[tuple[ExplorePivotFormula, pl.Expr]]:
    """Compile once and validate grouped expressions against the source schema."""

    compiled: list[tuple[ExplorePivotFormula, pl.Expr]] = []
    validation_frame = pl.LazyFrame(schema=schema)
    for formula in formulas:
        expression = _formula_expression(formula)
        try:
            source_fields = set(expression.meta.root_names())
        except Exception as exc:
            raise PivotContractError(
                "invalid_pivot_formula",
                "Pivot formula is invalid.",
                "Use a safe Python expression that returns one Polars expression.",
                formula_id=formula["id"],
            ) from exc
        missing = sorted(source_fields - schema.keys())
        if missing:
            missing_text = ", ".join(missing)
            raise PivotContractError(
                "invalid_pivot_formula",
                f"Pivot formula uses unavailable source fields: {missing_text}.",
                f"Use fields from the Explore dataset instead of: {missing_text}.",
                formula_id=formula["id"],
                missing_fields=missing_text,
            )
        try:
            formula_dtype = (
                validation_frame.group_by(pl.lit(0).alias("__haute_pivot_formula_validation_group"))
                .agg(expression)
                .collect_schema()[_value_alias(formula["id"])]
            )
            if formula_dtype.is_nested() or formula_dtype.base_type() == pl.Object:
                raise TypeError("formula must produce one supported scalar per group")
        except Exception as exc:
            raise PivotContractError(
                "invalid_pivot_formula",
                "Pivot formula must produce one scalar aggregate per group.",
                "Aggregate source fields in the Polars expression, for example "
                'pl.col("amount").sum() * 2.',
                formula_id=formula["id"],
            ) from exc
        compiled.append((formula, expression))
    return compiled


def _valid_value_expression(field: str, dtype: pl.DataType) -> pl.Expr:
    expression = pl.col(field)
    if dtype.base_type() in _FLOAT_BASE_TYPES:
        expression = expression.fill_nan(None)
    return expression


def _aggregation_expression(
    value: ExplorePivotValuePlacement,
    dtype: pl.DataType,
) -> pl.Expr:
    valid = _valid_value_expression(value["field"], dtype)
    aggregation = value["aggregation"]
    alias = _value_alias(value["id"])
    if aggregation == "count":
        return valid.count().alias(alias)
    if aggregation == "distinct_count":
        return valid.drop_nulls().n_unique().alias(alias)
    if aggregation == "sum":
        return pl.when(valid.count() > 0).then(valid.sum()).otherwise(None).alias(alias)
    if aggregation == "average":
        return valid.mean().alias(alias)
    if aggregation == "min":
        return valid.min().alias(alias)
    if aggregation == "max":
        return valid.max().alias(alias)
    if aggregation == "median":
        return valid.median().alias(alias)
    raise AssertionError(f"Unsupported validated pivot aggregation: {aggregation}")


_JS_SAFE_INTEGER_LIMIT = 2**53 - 1


def _normalise_cell(value: Any, warnings: set[str]) -> str | float | int | bool | None:
    if isinstance(value, int) and not isinstance(value, bool):
        # An i64 aggregate can exceed JavaScript's exact-integer range; the
        # canonical decimal string keeps it precise, matching integer members.
        return value if abs(value) <= _JS_SAFE_INTEGER_LIMIT else str(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            warnings.add(_NON_FINITE_WARNING)
            return None
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    raise PivotContractError(
        "invalid_pivot_result",
        "A pivot aggregation produced an unsupported value.",
        "Choose a scalar value field and supported aggregation.",
        actual_type=type(value).__name__,
    )


class PivotService:
    """Calculate pivots from cached Explore data without executing the graph."""

    def __init__(self, store: JobStore, explore_service: ExploreService) -> None:
        self._store = store
        self._explore_service = explore_service
        self._lifecycle = JobLifecycle(store)
        self._jobs = CancellableJobRegistry()
        self._result_cache: LRUCache[str, ExplorePivotResult] = LRUCache(max_size=32)
        self._completion_lock = threading.RLock()
        self._completion_events: dict[str, threading.Event] = {}

    def _explore_spec(
        self,
        body: ExplorePivotRunRequest | ExplorePivotMembersRequest,
    ) -> ExploreCacheSpec:
        values: dict[str, Any] = {
            "graph": body.graph,
            "node_id": body.node_id,
            "source": body.source,
        }
        if body.streaming_chunk_size is not None:
            values["streaming_chunk_size"] = body.streaming_chunk_size
        return self._explore_service.prepare_spec(ExploreRunRequest(**values))

    @staticmethod
    def _cache_required_failure() -> ExplorePivotFailure:
        return ExplorePivotFailure(
            reason_code="cache_required",
            message="The full Explore dataset is not materialised.",
            remediation="Process and cache full data, then update the pivot.",
        )

    @classmethod
    def _cache_required_response(cls) -> ExplorePivotRunResponse:
        return ExplorePivotRunResponse(
            status="cache_required",
            message="Cache the Explore dataset before updating this pivot.",
            failure=cls._cache_required_failure(),
        )

    @staticmethod
    def _calculation_key(pivot: ExplorePivotConfig) -> str:
        sort_by = pivot["options"]["sort_by"]
        payload = {
            "filters": [
                {
                    "field": placement["field"],
                    "members": [
                        {"kind": member["kind"], "value": member["value"]}
                        for member in _unique_filter_members(placement["members"])
                    ],
                }
                for placement in pivot["filters"]
            ],
            "columns": [placement["field"] for placement in pivot["columns"]],
            "rows": [
                {
                    "field": placement["field"],
                    "sort": placement["sort"] if placement["id"] == sort_by else "ascending",
                }
                for placement in pivot["rows"]
            ],
            "values": [
                {
                    "id": placement["id"],
                    "field": placement["field"],
                    "aggregation": placement["aggregation"],
                    "reference": placement["reference"],
                    "sort_rows": placement["sort_rows"] if placement["id"] == sort_by else "none",
                }
                for placement in pivot["values"]
            ],
            "formulas": [
                {
                    "id": formula["id"],
                    "reference": formula["reference"],
                    "expression": formula["expression"],
                }
                for formula in pivot["formulas"]
            ],
            "value_order": pivot["value_order"],
            "options": {
                "row_grand_totals": pivot["options"]["row_grand_totals"],
                "column_grand_totals": pivot["options"]["column_grand_totals"],
            },
            "version": EXPLORE_PIVOT_RESULT_VERSION,
        }
        return content_hash_bytes(canonical_json(payload).encode())

    def _calculation_spec(
        self,
        body: ExplorePivotRunRequest,
        explore: ExploreCacheSpec,
        pivot: ExplorePivotConfig,
    ) -> PivotCalculationSpec:
        calculation_key = self._calculation_key(pivot)
        result_identity = content_hash_bytes(
            canonical_json(
                {
                    "pivot_id": pivot["id"],
                    "calculation_key": calculation_key,
                    "version": EXPLORE_PIVOT_RESULT_VERSION,
                }
            ).encode()
        )
        result_cache_key = (
            f"explore-pivot:v{EXPLORE_PIVOT_RESULT_VERSION}:"
            f"{explore.dataframe_cache_key}:{result_identity}"
        )
        return PivotCalculationSpec(
            explore=explore,
            pivot=pivot,
            calculation_key=calculation_key,
            result_cache_key=result_cache_key,
            family_key=(
                "explore_pivot",
                body.graph.source_file or "",
                body.node_id,
                body.source,
                pivot["id"],
            ),
        )

    def start(self, body: ExplorePivotRunRequest) -> ExplorePivotRunResponse:
        explore = self._explore_spec(body)
        cache_key = explore.dataframe_cache_request.keys_by_node[explore.node_id]
        if explore.dataframe_cache_request.cache.get(cache_key) is None:
            return self._cache_required_response()
        pivot = cast(
            ExplorePivotConfig,
            validate_explore_pivots([body.pivot], context="Explore pivot request")[0],
        )
        spec = self._calculation_spec(body, explore, pivot)
        cached = self._result_cache.get(spec.result_cache_key)
        if cached is not None:
            return ExplorePivotRunResponse(
                status="completed",
                cached=True,
                message="Pivot cache hit",
                result=cached,
            )

        job_id = self._store.create_job(
            {
                "kind": "pivot",
                "status": "running",
                "progress": 0.0,
                "message": "Starting pivot calculation",
                "analysis_key": spec.result_cache_key,
            }
        )
        completion_event = threading.Event()
        with self._completion_lock:
            self._completion_events[job_id] = completion_event
        token, previous = self._jobs.register_latest(spec.family_key, job_id)
        with self._completion_lock:
            predecessor_finished = (
                self._completion_events.get(previous) if previous is not None else None
            )
        if previous is not None:
            self._lifecycle.transition(
                previous,
                to="superseded",
                message="Superseded by a newer pivot request.",
                expected_status="running",
            )
        threading.Thread(
            target=self._run,
            args=(job_id, spec, token, predecessor_finished, completion_event),
            daemon=True,
        ).start()
        return ExplorePivotRunResponse(
            status="started",
            job_id=job_id,
            message="Pivot calculation started",
        )

    def status(self, job_id: str) -> ExplorePivotStatusResponse:
        job = self._store.require_job(job_id)
        if job.get("kind") != "pivot":
            raise HTTPException(status_code=404, detail="Pivot job not found")
        return ExplorePivotStatusResponse(
            status=job["status"],
            progress=job.get("progress", 0),
            message=job.get("message", ""),
            result=job.get("result"),
            failure=job.get("failure"),
            terminal_reason=job.get("terminal_reason"),
            execution_metrics=job.get("execution_metrics"),
        )

    def cancel(self, job_id: str) -> ExplorePivotStatusResponse:
        current = self.status(job_id)
        cancelled = self._jobs.cancel(job_id)
        if cancelled or current.status == "running":
            self._lifecycle.transition(
                job_id,
                to="cancelled",
                message="Pivot calculation cancelled",
                expected_status="running",
            )
            self._jobs.release(job_id)
        return self.status(job_id)

    def _run(
        self,
        job_id: str,
        spec: PivotCalculationSpec,
        token: JobCancellation,
        predecessor_finished: threading.Event | None,
        completion_event: threading.Event,
    ) -> None:
        started = time.monotonic()
        context: ExecutionContext | None = None
        try:
            while predecessor_finished is not None and not predecessor_finished.wait(0.01):
                token.execution_token.throw_if_cancelled("explore_pivot", job_id=job_id)
            context = create_admitted_execution_context(
                operation="explore_pivot",
                profile=ExecutionProfile.EXPLORE_ANALYSIS,
                job_id=job_id,
                cancellation_token=token.execution_token,
            )
            bind_running_execution_metrics_publisher(self._store, job_id, context)
            result = self._calculate(spec, context)
            context.checkpoint(label="pivot_before_publish")
            metrics = context.metrics_payload(status="completed")
            metrics["execution_strategy"] = {
                "schema_version": 1,
                "status": "not_planned",
                "strategy": "not-planned",
                "profile": "explore_analysis",
                "boundedness": "bounded",
                "reason_code": "cached_explore_dataframe",
                "detail_state": "available",
                "boundaries": {"state": "available", "total_count": 0, "items": []},
                "reasons": {"state": "available", "total_count": 0, "items": []},
                "provenance": {"state": "available", "total_count": 0, "items": []},
            }
            result = result.model_copy(
                update={"execution_metrics": ExecutionMetricsPayload.model_validate(metrics)}
            )
            transitioned = self._lifecycle.transition(
                job_id,
                to="completed",
                message="Pivot calculation complete",
                fields={
                    "progress": 1.0,
                    "result": result,
                    "execution_metrics": result.execution_metrics,
                },
                elapsed_seconds=time.monotonic() - started,
            )
            if transitioned is not None:
                self._result_cache.put(spec.result_cache_key, result)
        except PivotContractError as exc:
            self._lifecycle.transition(
                job_id,
                to="contract_error",
                message=exc.failure.message,
                fields={"failure": exc.failure},
                elapsed_seconds=time.monotonic() - started,
            )
        except ExecutionCancelledError:
            self._lifecycle.transition(
                job_id,
                to=token.terminal_reason or "cancelled",
                message="Pivot calculation cancelled",
                elapsed_seconds=time.monotonic() - started,
            )
        except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
            self._lifecycle.transition(
                job_id,
                to="memory_limited",
                message=str(exc),
                fields={"error": str(exc)},
                elapsed_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            self._lifecycle.transition(
                job_id,
                to="error",
                message=str(exc),
                fields={"error": str(exc)},
                elapsed_seconds=time.monotonic() - started,
            )
        finally:
            if context is not None:
                context.release_admission()
            self._jobs.release(job_id)
            completion_event.set()
            with self._completion_lock:
                if self._completion_events.get(job_id) is completion_event:
                    del self._completion_events[job_id]

    def _cached_lazy_frame(self, explore: ExploreCacheSpec) -> pl.LazyFrame:
        key = explore.dataframe_cache_request.keys_by_node[explore.node_id]
        lazy = explore.dataframe_cache_request.cache.scan(key)
        if lazy is None:
            raise PivotContractError(
                "cache_required",
                "The full Explore dataset is not materialised.",
                "Process and cache full data, then update the pivot.",
            )
        return lazy

    def _validate_schema(
        self,
        schema: Mapping[str, pl.DataType],
        pivot: ExplorePivotConfig,
    ) -> None:
        dimensions = [*pivot["filters"], *pivot["rows"], *pivot["columns"]]
        for placement in [*dimensions, *pivot["values"]]:
            field = placement["field"]
            if field not in schema:
                raise PivotContractError(
                    "invalid_pivot_field",
                    f"Pivot field '{field}' does not exist.",
                    "Choose a field from the Explore dataset.",
                    field=field,
                )
        for placement in dimensions:
            field = placement["field"]
            dtype = schema[field]
            if is_unhashable_dtype(dtype) or not _is_supported_dimension_dtype(dtype):
                raise PivotContractError(
                    "invalid_pivot_field",
                    f"Pivot field '{field}' is not groupable.",
                    "Choose a supported scalar field.",
                    field=field,
                )
        if not pivot["values"] and not pivot["formulas"]:
            raise PivotContractError(
                "pivot_unconfigured",
                "Configure at least one pivot value or formula.",
                "Add a Value or calculated field to the pivot.",
            )
        for value in pivot["values"]:
            dtype = schema[value["field"]]
            aggregation = value["aggregation"]
            if aggregation in {"sum", "average", "median"} and not dtype.is_numeric():
                raise PivotContractError(
                    "invalid_pivot_field",
                    "Aggregation requires a numeric field.",
                    "Choose a numeric value field.",
                    field=value["field"],
                )
            if aggregation in {"min", "max"} and (
                dtype.is_nested() or dtype.base_type() == pl.Object
            ):
                raise PivotContractError(
                    "invalid_pivot_field",
                    "Aggregation does not support nested fields.",
                    "Choose a scalar value field.",
                    field=value["field"],
                )
            if aggregation == "distinct_count" and is_unhashable_dtype(dtype):
                raise PivotContractError(
                    "invalid_pivot_field",
                    "Distinct count requires a hashable field.",
                    "Choose a scalar value field.",
                    field=value["field"],
                )

    def _apply_filters(
        self,
        lazy: pl.LazyFrame,
        schema: Mapping[str, pl.DataType],
        pivot: ExplorePivotConfig,
    ) -> pl.LazyFrame:
        filtered = lazy
        for placement in pivot["filters"]:
            members = _unique_filter_members(placement["members"])
            self._limit("filter_members", len(members), MAX_FILTER_MEMBERS)
            if not members:
                continue
            expressions = [
                _filter_member_expression(placement["field"], schema[placement["field"]], member)
                for member in members
            ]
            filtered = filtered.filter(reduce(or_, expressions))
        return filtered

    @staticmethod
    def _cardinality_expression(fields: Sequence[str], alias: str) -> pl.Expr:
        if not fields:
            return (pl.len() > 0).cast(pl.Int64).alias(alias)
        return pl.struct([pl.col(field) for field in fields]).n_unique().alias(alias)

    def _cardinalities(
        self,
        filtered: pl.LazyFrame,
        row_fields: Sequence[str],
        column_fields: Sequence[str],
        context: ExecutionContext,
    ) -> tuple[int, int]:
        query = filtered.select(
            self._cardinality_expression(row_fields, "__haute_row_groups"),
            self._cardinality_expression(column_fields, "__haute_column_groups"),
        )
        frame = cancellable_streaming_collect(query, execution_context=context)
        return int(frame.item(0, 0)), int(frame.item(0, 1))

    @staticmethod
    def _unique_fields(fields: Sequence[str]) -> list[str]:
        return list(dict.fromkeys(fields))

    def _aggregate_frame(
        self,
        filtered: pl.LazyFrame,
        group_fields: Sequence[str],
        values: Sequence[ExplorePivotValuePlacement],
        compiled_formulas: Sequence[tuple[ExplorePivotFormula, pl.Expr]],
        schema: Mapping[str, pl.DataType],
        context: ExecutionContext,
    ) -> pl.DataFrame:
        unique_group_fields = self._unique_fields(group_fields)
        group_aliases = {field: _group_alias(field) for field in unique_group_fields}
        expressions: list[pl.Expr] = []
        seen_aggregations: set[tuple[str, str]] = set()
        for value in values:
            key = (value["field"], value["aggregation"])
            if key in seen_aggregations:
                continue
            seen_aggregations.add(key)
            expressions.append(_aggregation_expression(value, schema[value["field"]]))
        expressions.extend(expression for _, expression in compiled_formulas)
        if unique_group_fields:
            query = filtered.group_by(unique_group_fields).agg(expressions)
        else:
            query = filtered.select(expressions)
        query = query.rename(group_aliases)
        # Each configured Value gets a public reference, even when its aggregate
        # was shared with an earlier identical (field, aggregation) Value.
        first_aliases: dict[tuple[str, str], str] = {}
        for value in values:
            first_aliases.setdefault(
                (value["field"], value["aggregation"]), _value_alias(value["id"])
            )
        if values:
            query = query.with_columns(
                [
                    pl.col(first_aliases[(value["field"], value["aggregation"])]).alias(
                        value["reference"]
                    )
                    for value in values
                ]
            )
        visible_columns = [
            *group_aliases.values(),
            *(value["reference"] for value in values),
            *(_value_alias(formula["id"]) for formula, _ in compiled_formulas),
        ]
        query = query.select(visible_columns)
        query = query.rename({output["reference"]: _value_alias(output["id"]) for output in values})
        query = query.rename({alias: field for field, alias in group_aliases.items()})
        try:
            return cancellable_streaming_collect(query, execution_context=context)
        except PivotContractError:
            raise
        except Exception as exc:
            if compiled_formulas:
                raise PivotContractError(
                    "invalid_pivot_formula",
                    "A pivot formula failed while evaluating the grouped data.",
                    "Check the selected formulas against the source field values and types.",
                    formula_ids=", ".join(formula["id"] for formula, _ in compiled_formulas),
                ) from exc
            raise

    @staticmethod
    def _aggregate_map(
        frame: pl.DataFrame,
        group_fields: Sequence[str],
        values: Sequence[ExplorePivotValuePlacement],
        formulas: Sequence[ExplorePivotFormula],
        warnings: set[str],
    ) -> dict[TypedPathTuple, tuple[str | float | int | bool | None, ...]]:
        result: dict[TypedPathTuple, tuple[str | float | int | bool | None, ...]] = {}
        for row in frame.iter_rows(named=True):
            path = _path_tuple(row, group_fields)
            result[path] = tuple(
                _normalise_cell(row[_value_alias(value["id"])], warnings)
                for value in [*values, *formulas]
            )
        return result

    def _calculate(
        self,
        spec: PivotCalculationSpec,
        context: ExecutionContext,
    ) -> ExplorePivotResult:
        lazy = self._cached_lazy_frame(spec.explore)
        schema = lazy.collect_schema()
        self._validate_schema(schema, spec.pivot)
        filtered = self._apply_filters(lazy, schema, spec.pivot)
        row_fields = [placement["field"] for placement in spec.pivot["rows"]]
        column_fields = [placement["field"] for placement in spec.pivot["columns"]]
        values = spec.pivot["values"]
        formulas = spec.pivot["formulas"]
        compiled_formulas = _compile_formulas(formulas, schema)
        aggregate_outputs: list[ExplorePivotValuePlacement | ExplorePivotFormula] = [
            *values,
            *formulas,
        ]
        outputs_by_id: dict[str, ExplorePivotValuePlacement | ExplorePivotFormula] = {
            output["id"]: output for output in aggregate_outputs
        }
        if len(outputs_by_id) != len(aggregate_outputs):
            raise PivotContractError(
                "invalid_pivot_output_order",
                "Pivot output ids must be globally unique.",
                "Correct duplicate Value or formula ids in the pivot configuration.",
            )
        try:
            output_values: list[ExplorePivotValuePlacement | ExplorePivotFormula] = [
                outputs_by_id[output_id] for output_id in spec.pivot["value_order"]
            ]
        except KeyError as exc:
            raise PivotContractError(
                "invalid_pivot_output_order",
                "Pivot value_order references an unknown output id.",
                "Refresh the pivot configuration and try again.",
                value_id=str(exc),
            ) from exc
        if len(output_values) != len(aggregate_outputs) or len(
            set(spec.pivot["value_order"])
        ) != len(aggregate_outputs):
            raise PivotContractError(
                "invalid_pivot_output_order",
                "Pivot value_order must contain every output id exactly once.",
                "Refresh the pivot configuration and try again.",
            )
        aggregate_index_by_id = {
            output["id"]: output_index for output_index, output in enumerate(aggregate_outputs)
        }

        row_count, column_count = self._cardinalities(
            filtered,
            row_fields,
            column_fields,
            context,
        )
        self._limit("row_groups", row_count, MAX_ROW_GROUPS)
        self._limit("column_groups", column_count, MAX_COLUMN_GROUPS)
        add_row_total = bool(row_fields and row_count and spec.pivot["options"]["row_grand_totals"])
        add_column_total = bool(
            column_fields and column_count and spec.pivot["options"]["column_grand_totals"]
        )
        display_rows = row_count + int(add_row_total)
        display_columns = column_count + int(add_column_total)
        self._limit(
            "display_cells",
            display_rows * display_columns * max(len(output_values), 1),
            MAX_DISPLAY_CELLS,
        )

        warnings: set[str] = set()
        base: dict[
            TypedPathTuple,
            tuple[str | float | int | bool | None, ...],
        ] = {}
        row_paths: list[TypedPathTuple] = []
        column_paths: list[TypedPathTuple] = []
        if row_count and column_count:
            base_frame = self._aggregate_frame(
                filtered,
                [*row_fields, *column_fields],
                values,
                compiled_formulas,
                schema,
                context,
            )
            base = self._aggregate_map(
                base_frame,
                self._unique_fields([*row_fields, *column_fields]),
                values,
                formulas,
                warnings,
            )
            row_members: set[TypedPathTuple] = set()
            column_members: set[TypedPathTuple] = set()
            for row in base_frame.iter_rows(named=True):
                row_members.add(_path_tuple(row, row_fields))
                column_members.add(_path_tuple(row, column_fields))
            row_paths = list(row_members)
            column_paths = sorted(column_members, key=_path_sort_key)

        row_total_values: dict[
            TypedPathTuple,
            tuple[str | float | int | bool | None, ...],
        ] = {}
        if add_row_total:
            frame = self._aggregate_frame(
                filtered, column_fields, values, compiled_formulas, schema, context
            )
            row_total_values = self._aggregate_map(
                frame,
                self._unique_fields(column_fields),
                values,
                formulas,
                warnings,
            )

        column_total_values: dict[
            TypedPathTuple,
            tuple[str | float | int | bool | None, ...],
        ] = {}
        sort_by = spec.pivot["options"]["sort_by"]
        active_sort_index = next(
            (value_index for value_index, value in enumerate(values) if value["id"] == sort_by),
            None,
        )
        if row_paths and (add_column_total or active_sort_index is not None):
            frame = self._aggregate_frame(
                filtered, row_fields, values, compiled_formulas, schema, context
            )
            column_total_values = self._aggregate_map(
                frame,
                self._unique_fields(row_fields),
                values,
                formulas,
                warnings,
            )

        def compare_row_paths(left: TypedPathTuple, right: TypedPathTuple) -> int:
            if active_sort_index is not None:
                active_value = values[active_sort_index]
                comparison = _compare_aggregate_values(
                    column_total_values[left][active_sort_index],
                    column_total_values[right][active_sort_index],
                    dtype=schema[active_value["field"]],
                    aggregation=active_value["aggregation"],
                    direction=active_value["sort_rows"],
                )
                if comparison:
                    return comparison
            return _compare_row_paths(left, right, spec.pivot["rows"], sort_by)

        row_paths.sort(key=cmp_to_key(compare_row_paths))

        grand_total_values: tuple[str | float | int | bool | None, ...] | None = None
        if add_row_total and add_column_total:
            frame = self._aggregate_frame(filtered, [], values, compiled_formulas, schema, context)
            grand_total_values = self._aggregate_map(frame, [], values, formulas, warnings)[()]

        result_row_paths = [_path_model(path) for path in row_paths]
        result_column_paths = [_path_model(path) for path in column_paths]
        if add_row_total:
            result_row_paths.append(ExplorePivotPath(is_grand_total=True))
        if add_column_total:
            result_column_paths.append(ExplorePivotPath(is_grand_total=True))

        cells: list[ExplorePivotCell] = []
        for row_index, row_path in enumerate(row_paths):
            for column_index, column_path in enumerate(column_paths):
                combined = _combined_path(
                    row_fields,
                    row_path,
                    column_fields,
                    column_path,
                )
                aggregated = None if combined is None else base.get(combined)
                self._append_cells(
                    cells,
                    row_index,
                    column_index,
                    output_values,
                    aggregate_index_by_id,
                    aggregated,
                )

        if add_row_total:
            row_index = len(row_paths)
            for column_index, column_path in enumerate(column_paths):
                self._append_cells(
                    cells,
                    row_index,
                    column_index,
                    output_values,
                    aggregate_index_by_id,
                    row_total_values.get(column_path),
                )
        if add_column_total:
            column_index = len(column_paths)
            for row_index, row_path in enumerate(row_paths):
                self._append_cells(
                    cells,
                    row_index,
                    column_index,
                    output_values,
                    aggregate_index_by_id,
                    column_total_values.get(row_path),
                )
        if add_row_total and add_column_total:
            self._append_cells(
                cells,
                len(row_paths),
                len(column_paths),
                output_values,
                aggregate_index_by_id,
                grand_total_values,
            )

        result_values: list[ExplorePivotValueIdentity] = []
        for output in output_values:
            if "aggregation" in output:
                value_output = cast(ExplorePivotValuePlacement, output)
                result_values.append(
                    ExplorePivotValueIdentity(
                        id=value_output["id"],
                        field=value_output["field"],
                        aggregation=value_output["aggregation"],
                    )
                )
            else:
                result_values.append(
                    ExplorePivotValueIdentity(
                        id=output["id"],
                        field=output["reference"],
                        aggregation="formula",
                    )
                )
        return ExplorePivotResult(
            node_id=spec.explore.node_id,
            pivot_id=spec.pivot["id"],
            source=spec.explore.source,
            dataframe_cache_key=spec.explore.dataframe_cache_key,
            calculation_key=spec.calculation_key,
            row_fields=row_fields,
            column_fields=column_fields,
            values=result_values,
            row_paths=result_row_paths,
            column_paths=result_column_paths,
            cells=cells,
            warnings=sorted(warnings),
            generated_at=time.time(),
        )

    @staticmethod
    def _append_cells(
        cells: list[ExplorePivotCell],
        row_index: int,
        column_index: int,
        output_values: Sequence[ExplorePivotValuePlacement | ExplorePivotFormula],
        aggregate_index_by_id: Mapping[str, int],
        aggregated: tuple[str | float | int | bool | None, ...] | None,
    ) -> None:
        for value in output_values:
            cell_value = (
                aggregated[aggregate_index_by_id[value["id"]]]
                if aggregated is not None
                else (0 if value.get("aggregation") in _COUNT_AGGREGATIONS else None)
            )
            cells.append(
                ExplorePivotCell(
                    row_index=row_index,
                    column_index=column_index,
                    value_id=value["id"],
                    value=cell_value,
                )
            )

    @staticmethod
    def _limit(dimension: str, actual: int, limit: int) -> None:
        if actual > limit:
            raise PivotContractError(
                "pivot_cardinality_limit",
                "Pivot cardinality exceeds the display limit.",
                "Reduce pivot dimensions or filter the dataset.",
                dimension=dimension,
                actual=actual,
                limit=limit,
            )

    @staticmethod
    def _member_search_expression(field: str, dtype: pl.DataType, search: str) -> pl.Expr:
        column = pl.col(field)
        if dtype.base_type() in _FLOAT_BASE_TYPES:
            label = (
                pl.when(column.is_null())
                .then(pl.lit("(blank)"))
                .when(column.is_nan())
                .then(pl.lit("(NaN)"))
                .otherwise(column.cast(pl.String))
            )
        else:
            label = (
                pl.when(column.is_null()).then(pl.lit("(blank)")).otherwise(column.cast(pl.String))
            )
        # ``str.lower()`` mirrors Polars ``str.to_lowercase`` on the column side;
        # ``casefold()`` would be stricter than the column transform and could
        # match queries the column expression never produces.
        return label.str.to_lowercase().str.contains(search.lower(), literal=True)

    def members(self, body: ExplorePivotMembersRequest) -> ExplorePivotMembersResponse:
        explore = self._explore_spec(body)
        key = explore.dataframe_cache_request.keys_by_node[explore.node_id]
        if explore.dataframe_cache_request.cache.get(key) is None:
            return ExplorePivotMembersResponse(
                status="cache_required",
                failure=self._cache_required_failure(),
            )
        context: ExecutionContext | None = None
        try:
            context = create_admitted_execution_context(
                operation="explore_pivot_members",
                profile=ExecutionProfile.EXPLORE_ANALYSIS,
                job_id=None,
            )
            lazy = self._cached_lazy_frame(explore)
            schema = lazy.collect_schema()
            if (
                body.field not in schema
                or is_unhashable_dtype(schema[body.field])
                or not _is_supported_dimension_dtype(schema[body.field])
            ):
                raise PivotContractError(
                    "invalid_pivot_field",
                    "Pivot member field is not groupable.",
                    "Choose a supported scalar field.",
                    field=body.field,
                )
            filtered = lazy
            if body.search:
                filtered = filtered.filter(
                    self._member_search_expression(body.field, schema[body.field], body.search)
                )
            cardinality = cancellable_streaming_collect(
                filtered.select(pl.col(body.field).n_unique().alias("__haute_member_count")),
                execution_context=context,
            ).item(0, 0)
            self._limit("filter_members", int(cardinality), MAX_FILTER_MEMBERS)
            grouped = cancellable_streaming_collect(
                filtered.group_by(body.field).agg(pl.len().alias("__haute_count")),
                execution_context=context,
            )
            options = []
            for value, count in grouped.iter_rows():
                member_key = _member_key(value)
                options.append(
                    ExplorePivotMemberOption(
                        key=member_key,
                        label=_member_label(member_key),
                        count=int(count),
                    )
                )
            options.sort(
                key=lambda option: (
                    -option.count,
                    _member_sort_key((option.key.kind, option.key.value)),
                )
            )
            return ExplorePivotMembersResponse(
                status="ok",
                field=body.field,
                members=options,
            )
        except PivotContractError as exc:
            return ExplorePivotMembersResponse(
                status="error",
                field=body.field,
                failure=exc.failure,
            )
        finally:
            if context is not None:
                context.release_admission()
