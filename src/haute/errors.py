"""Typed error hierarchy for Haute.

All Haute-specific exceptions inherit from :class:`HauteError` so callers
can catch the whole family with a single ``except``. Each subclass also
accepts arbitrary ``**context`` kwargs that are rendered into ``str(err)``
so structured information (paths, node IDs, missing features) reaches
log lines and tracebacks without callers having to format it manually.
"""

from __future__ import annotations

from typing import Any


class HauteError(Exception):
    """Root of the Haute exception hierarchy."""

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


class ConfigError(HauteError):
    """Configuration loading or validation failure."""


class ParseError(HauteError):
    """Pipeline source parsing failure."""


class ExecutionError(HauteError):
    """Runtime execution failure."""


class DeployError(HauteError):
    """Deploy validation or bundling failure."""


class FeatureMismatchError(HauteError):
    """Feature or categorical train-vs-score contract mismatch."""


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
