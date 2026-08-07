"""Typed error hierarchy for Haute.

The core error family defined in this module roots at :class:`HauteError`:
``ConfigError``, ``ParseError``, ``ExecutionError``, ``DeployError`` and the
other classes below all derive from it, so a single ``except HauteError``
catches any of them. Each subclass also accepts arbitrary ``**context`` kwargs
that are rendered into ``str(err)`` so structured information (paths, node IDs,
missing features) reaches log lines and tracebacks without callers having to
format it manually.

Not every Haute exception lives here. A number of domain-specific exceptions
are defined next to the code that raises them and deliberately derive from a
stdlib base instead of ``HauteError`` — for example resource-exhaustion errors
extend ``MemoryError``, deadline errors extend ``TimeoutError``, validation
errors extend ``ValueError`` (via :class:`HauteValidationError`, defined in
``_validation_error.py`` and re-exported here for convenience), and
missing-artifact errors extend ``FileNotFoundError`` so that existing
``except MemoryError`` / ``except TimeoutError`` / ``except ValueError`` /
``except FileNotFoundError`` handlers keep catching them. A single
``except HauteError`` therefore does not
catch the entire Haute error surface; catch the relevant stdlib base (or the
specific exception class) when you need those. The classes defined in this
module are the ones the ``HauteError`` promise applies to.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, TypeGuard

from haute._validation_error import HauteValidationError as HauteValidationError


class HauteError(Exception):
    """Root of the Haute exception hierarchy."""

    error_code: ClassVar[str | None] = None
    public_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, message: str = "", **context: Any) -> None:
        self.message = message
        self.context: dict[str, Any] = dict(context)
        super().__init__(self._render())

    def _render(self) -> str:
        if not self.context:
            return self.message
        rendered_ctx = "(" + ", ".join(f"{k}={v}" for k, v in self.context.items()) + ")"
        if not self.message:
            return rendered_ctx
        return f"{self.message} {rendered_ctx}"

    def __str__(self) -> str:
        return self._render()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._render()!r})"

    def to_payload(self) -> dict[str, Any]:
        """Return the stable public payload for a typed contract error.

        Ordinary Haute errors deliberately have no public code or fields.  The
        small set of versioned contract errors below opts in explicitly so a
        route or background-job adapter never has to scrape ``str(exc)``.
        """
        if self.error_code is None:
            return {"message": str(self)}
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
        }
        for field_name in self.public_fields:
            payload[field_name] = getattr(self, field_name)
        return payload


def is_public_contract_error(exc: BaseException) -> TypeGuard[HauteError]:
    """Return whether *exc* opts into the versioned public error contract.

    A stable ``error_code`` is the explicit opt-in.  Keeping the predicate in
    the core error module lets execution code fail these errors loudly without
    importing the HTTP adapter (and its FastAPI dependency).
    """

    return isinstance(exc, HauteError) and exc.error_code is not None


class ConfigError(HauteError):
    """Configuration loading or validation failure."""


class ParseError(HauteError):
    """Pipeline source parsing failure."""


class ExecutionError(HauteError):
    """Runtime execution failure."""


class PreambleError(ExecutionError):
    """Raised when the pipeline preamble fails to compile or execute."""

    error_code = "preamble_failed"
    public_fields = ("source_line",)

    def __init__(self, message: str, source_line: int | None = None) -> None:
        self.source_line = source_line
        super().__init__(message)


class ContractResolutionError(ExecutionError):
    """Raised when profiled production execution cannot resolve a node contract."""

    error_code = "contract_resolution_failed"
    public_fields = ("node_id", "node_type", "failure_kind")

    def __init__(
        self,
        message: str,
        *,
        node_id: str,
        node_type: str,
        failure_kind: str,
    ) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.failure_kind = failure_kind
        super().__init__(
            message,
            node_id=node_id,
            node_type=node_type,
            failure_kind=failure_kind,
        )


class BoundedMemoryUnsupportedError(ExecutionError):
    """Raised when a bounded-memory execution path cannot stay bounded."""


class ChunkPlanUnsupportedError(BoundedMemoryUnsupportedError):
    """Raised when a graph cannot prove a safe chunked execution plan."""


class ChunkMemoryRiskError(BoundedMemoryUnsupportedError):
    """Raised when the minimum executable chunk exceeds its byte budget."""

    error_code = "chunk_memory_risk"
    public_fields = (
        "target_node_id",
        "reason_code",
        "estimated_target_row_bytes",
        "estimated_minimum_chunk_bytes",
        "row_expansion_factor",
        "target_chunk_bytes",
    )

    def __init__(
        self,
        message: str,
        *,
        target_node_id: str,
        estimated_target_row_bytes: int,
        target_chunk_bytes: int,
        reason_code: str = "single_row_exceeds_budget",
        estimated_minimum_chunk_bytes: int | None = None,
        row_expansion_factor: int = 1,
    ) -> None:
        self.target_node_id = target_node_id
        self.reason_code = reason_code
        self.estimated_target_row_bytes = estimated_target_row_bytes
        self.estimated_minimum_chunk_bytes = (
            estimated_target_row_bytes
            if estimated_minimum_chunk_bytes is None
            else estimated_minimum_chunk_bytes
        )
        self.row_expansion_factor = row_expansion_factor
        self.target_chunk_bytes = target_chunk_bytes
        super().__init__(
            message,
            target_node_id=target_node_id,
            reason_code=self.reason_code,
            estimated_target_row_bytes=estimated_target_row_bytes,
            estimated_minimum_chunk_bytes=self.estimated_minimum_chunk_bytes,
            row_expansion_factor=row_expansion_factor,
            target_chunk_bytes=target_chunk_bytes,
        )


class GroupByExecutionUnsupportedError(BoundedMemoryUnsupportedError):
    """Raised before a group-by that cannot honour the active profile."""

    error_code = "group_by_execution_unsupported"
    public_fields = (
        "node_id",
        "operator",
        "profile",
        "reason_code",
        "remediation",
        "estimated_peak_bytes",
        "headroom_bytes",
    )

    def __init__(
        self,
        message: str,
        *,
        node_id: str,
        operator: str,
        profile: str,
        reason_code: str,
        remediation: str,
        estimated_peak_bytes: int | None,
        headroom_bytes: int | None,
    ) -> None:
        self.node_id = node_id
        self.operator = operator
        self.profile = profile
        self.reason_code = reason_code
        self.remediation = remediation[:512]
        self.estimated_peak_bytes = estimated_peak_bytes
        self.headroom_bytes = headroom_bytes
        super().__init__(
            message,
            node_id=node_id,
            operator=operator,
            profile=profile,
            reason_code=reason_code,
            remediation=self.remediation,
            estimated_peak_bytes=estimated_peak_bytes,
            headroom_bytes=headroom_bytes,
        )


class DeployError(HauteError):
    """Deploy validation or bundling failure."""


class FeatureMismatchError(HauteError):
    """Feature or categorical train-vs-score contract mismatch."""


class SchemaMismatchError(HauteError):
    """Raised when a source or node schema boundary is incompatible."""


class RatingFactorMissingError(SchemaMismatchError):
    """Raised when a configured rating factor is absent from the input schema."""

    error_code = "rating_factor_missing"
    public_fields = ("table", "factor")

    def __init__(self, message: str, *, table: str, factor: str) -> None:
        self.table = table
        self.factor = factor
        super().__init__(message, table=table, factor=factor)


class RatingFactorDtypeContractError(SchemaMismatchError):
    """Raised when a saved ratebook dtype contract cannot be applied safely."""

    error_code = "rating_factor_dtype_contract"
    public_fields = ("table", "factor", "saved_dtype", "input_dtype")

    def __init__(
        self,
        message: str,
        *,
        table: str,
        factor: str,
        saved_dtype: dict[str, Any] | None,
        input_dtype: dict[str, Any] | None,
    ) -> None:
        self.table = table
        self.factor = factor
        self.saved_dtype = saved_dtype
        self.input_dtype = input_dtype
        super().__init__(
            message,
            table=table,
            factor=factor,
            saved_dtype=saved_dtype,
            input_dtype=input_dtype,
        )


class RatingExtremaUndefinedError(ExecutionError):
    """Raised when a min/max rating combination has no defined value."""

    error_code = "rating_extrema_undefined"
    public_fields = ("output_column", "operation")

    def __init__(self, message: str, *, output_column: str, operation: str) -> None:
        self.output_column = output_column
        self.operation = operation
        super().__init__(message, output_column=output_column, operation=operation)


class LiveSwitchScenarioError(ExecutionError):
    """Raised when a configured live switch has no mapping for a scenario."""

    error_code = "live_switch_scenario_missing"
    public_fields = ("switch", "scenario", "available_mappings")

    def __init__(
        self,
        message: str,
        *,
        switch: str,
        scenario: str,
        available_mappings: Sequence[str],
    ) -> None:
        self.switch = switch
        self.scenario = scenario
        self.available_mappings = tuple(sorted(str(item) for item in available_mappings))
        super().__init__(
            message,
            switch=switch,
            scenario=scenario,
            available_mappings=self.available_mappings,
        )


class TraceCorrelationUnsupportedError(ExecutionError):
    """Raised when trace row relocation cannot compare its identity columns."""

    error_code = "trace_correlation_unsupported"
    public_fields = ("node_id", "key_columns", "dtypes", "reason_code")

    def __init__(
        self,
        message: str,
        *,
        node_id: str,
        key_columns: Sequence[str],
        dtypes: Sequence[str],
        reason_code: str,
    ) -> None:
        if len(key_columns) != len(dtypes):
            raise ValueError("key_columns and dtypes must be positionally aligned")
        self.node_id = node_id
        self.key_columns = tuple(str(item) for item in key_columns[:16])
        self.dtypes = tuple(str(item) for item in dtypes[:16])
        self.reason_code = reason_code
        super().__init__(
            message,
            node_id=node_id,
            key_columns=self.key_columns,
            dtypes=self.dtypes,
            reason_code=reason_code,
        )


class ContractMismatchError(HauteError):
    """Raised when a declared column contract does not match observed columns.

    Surfaces in three places:

    * **Parser** — an explicit ``contract=...`` kwarg in a pipeline source
      file disagrees with the contract the builder derives from the
      configured factors/tables/etc.
    * **Executor (input side)** — an upstream frame is missing columns
      that the current node's contract says it will read.  Without this
      check, Polars raises a cryptic ``ColumnNotFound`` deep in a lazy
      plan; with it, Haute names the exact missing column up-front.
    * **Executor (output side)** — a node's observed output is missing
      columns its contract promised to produce, or contains columns
      outside what its contract declared.

    The error always names the offending node id and the symmetric
    column diff so a user can fix a typo'd contract in one edit.
    """


class ProjectionImpossibleError(ContractMismatchError, BoundedMemoryUnsupportedError):
    """Raised when bounded projection cannot determine a safe column subset."""
