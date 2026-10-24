"""The one runtime import point for the external ``price_contour`` solver library.

Every runtime use of ``price_contour`` in ``src/haute`` goes through
:func:`price_contour`, which imports the package once per process and verifies
that the installed build is one haute can drive: the distribution metadata and
the module agree on a version, that version satisfies haute's pinned specifier
(prereleases rejected), every symbol haute calls exists, every keyword haute
passes to a Python wrapper is still accepted, the native extension loads, and a
one-row frame passes through it and back. The native module moves frames
through Polars' private Python methods (``Series._s._export`` into Rust,
``Series._import`` back), which no public API pins, so a Polars that renames
one fails here rather than mid-solve.
Any failure raises one :class:`PriceContourCompatibilityError` that lists
**every** problem together with where the package was installed from and how to
fix it, rather than an ``AttributeError`` or ``TypeError`` deep inside a solve.

Callers look symbols up on the returned module at call time
(``pc = price_contour(); pc.ApplyOptimiser(...)``), never bind them at import,
so ``unittest.mock.patch("price_contour.ApplyOptimiser")`` keeps working.
``if TYPE_CHECKING:`` imports of ``price_contour`` types remain allowed.
"""

from __future__ import annotations

import functools
import importlib
import inspect
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from haute._install_source import InstallKind, install_source
from haute._logging import get_logger

logger = get_logger(component="price_contour")

DISTRIBUTION = "price-contour"
MODULE = "price_contour"
NATIVE_MODULE = "price_contour._price_contour"

# Must equal the ``price-contour`` specifier in pyproject.toml exactly
# (``tests/test_dependency_contracts.py`` pins the equality).
REQUIRED_SPECIFIER = ">=0.7.0,<0.8"

# Prereleases (``0.4.2rc1``, ``0.4.2.dev0``) are rejected even inside the
# specifier: haute is only verified against released builds.
ALLOW_PRERELEASES = False

# Every attribute haute reads from the package, as dotted paths from the
# package root. Methods are listed so a renamed method fails here, not mid-solve.
REQUIRED_SYMBOLS: tuple[str, ...] = (
    "ApplyOptimiser",
    "ApplyOptimiser.apply",
    "ApplyOptimiser.with_explainer_columns",
    "apply_from_grid",
    "QuoteGrid",
    "build_grid_from_parquet_chunked",
    "build_ratebook_factor_contexts_from_parquet_chunked",
    "OnlineOptimiser",
    "OnlineOptimiser.solve",
    "OnlineOptimiser.frontier",
    "OnlineOptimiser.summary",
    "RatebookOptimiser",
    "RatebookOptimiser.solve",
    "RatebookOptimiser.frontier",
    "RatebookOptimiser.summary",
    # The canonical per-quote evaluation: a solve's frame, and a frontier point's
    # frame from the tables the frontier kept (OPT-V09C).
    "RatebookOptimiser.evaluate",
    "RatebookResult.quote_results",
    "RatebookFactorContexts",
    # A ratebook frontier keeps each point's factor tables; haute materialises
    # a selected point from them instead of re-solving.
    "RatebookFrontierResult.factor_tables",
    # The exact column set of a frontier's points, per mode; haute types the rows from it.
    "frontier_points_schema",
    # Cooperative cancellation of a frontier point's apply or evaluation (OPT-PC02).
    "CancelToken",
    "CancelToken.cancel",
    "Cancelled",
)

# Keyword arguments haute passes by name, per Python wrapper. PyO3 builtins
# (``QuoteGrid``, ``RatebookFactorContexts``) are not introspectable and are
# covered by REQUIRED_SYMBOLS only.
REQUIRED_PARAMETERS: dict[str, tuple[str, ...]] = {
    "ApplyOptimiser": (
        "lambdas",
        "objective",
        "constraints",
        "quote_id",
        "scenario_index",
        "scenario_value",
    ),
    "apply_from_grid": ("lambdas", "constraints", "cancel"),
    "build_grid_from_parquet_chunked": (
        "quote_id",
        "scenario_index",
        "scenario_value",
        "objective",
        "n_steps",
    ),
    "build_ratebook_factor_contexts_from_parquet_chunked": (
        "quote_id",
        "expected_quote_ids",
        "expected_n_quotes",
    ),
    "OnlineOptimiser": ("objective", "constraints", "max_iter", "tolerance", "record_history"),
    "OnlineOptimiser.frontier": ("threshold_ranges", "n_points_per_dim", "initial_lambdas"),
    "RatebookOptimiser": (
        "objective",
        "constraints",
        "factor_columns",
        "max_iter",
        "max_cd_iterations",
        "cd_tolerance",
        "tolerance",
    ),
    "RatebookOptimiser.solve": ("factor_columns", "lambdas"),
    "RatebookOptimiser.evaluate": ("cancel",),
    "RatebookOptimiser.frontier": (
        "threshold_ranges",
        "n_points_per_dim",
        "factor_columns",
        "initial_lambdas",
    ),
}

REMEDY = (
    "Install the locked build with `uv sync --locked`, or, when developing against a "
    "price-contour checkout, rebuild it with `uv run maturin develop --release` from that "
    "checkout."
)


class PriceContourCompatibilityError(RuntimeError):
    """The installed ``price_contour`` cannot be driven by this haute build."""


@dataclass(frozen=True, slots=True)
class PriceContourInstall:
    """Where the verified ``price-contour`` distribution came from."""

    version: str
    kind: InstallKind
    location: str | None

    @property
    def description(self) -> str:
        if self.kind == "editable":
            return f"editable checkout {self.location}"
        if self.kind == "direct_url":
            return f"direct URL {self.location}"
        return "wheel"


_ABSENT = object()


def _resolve(module: ModuleType, dotted: str) -> Any:
    """Walk a dotted attribute path from *module*; ``_ABSENT`` when any part is missing."""
    target: Any = module
    for part in dotted.split("."):
        target = getattr(target, part, _ABSENT)
        if target is _ABSENT:
            return _ABSENT
    return target


def _missing_symbols(module: ModuleType) -> list[str]:
    return [dotted for dotted in REQUIRED_SYMBOLS if _resolve(module, dotted) is _ABSENT]


def _missing_parameters(module: ModuleType) -> list[str]:
    missing: list[str] = []
    for dotted, names in REQUIRED_PARAMETERS.items():
        target = _resolve(module, dotted)
        if target is _ABSENT:
            continue  # reported as a missing symbol
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            missing.append(f"{dotted}: signature is not introspectable")
            continue
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_kwargs:
            continue
        for name in names:
            parameter = signature.parameters.get(name)
            if parameter is None or parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                missing.append(f"{dotted}({name}=...)")
    return missing


def _round_trip_failure(module: ModuleType) -> str | None:
    """Pass a one-row frame through the native module and back; the problem if it fails.

    A Rust panic surfaces as ``PanicException``, a ``BaseException``, so it is caught
    as one; an interrupt still propagates.
    """
    import polars as pl

    frame = pl.DataFrame(
        {"quote_id": ["q"], "scenario_index": [0], "scenario_value": [1.0], "objective": [1.0]},
        schema={
            "quote_id": pl.String,
            "scenario_index": pl.Int32,
            "scenario_value": pl.Float32,
            "objective": pl.Float32,
        },
    )
    try:
        result = module.ApplyOptimiser(lambdas={}, objective="objective", constraints={}).apply(
            frame
        )
        returned = result.dataframe
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - a Rust panic is a BaseException
        return (
            f"a one-row frame round trip through the native module failed "
            f"({type(exc).__name__}: {exc}); it moves frames through Polars "
            f"{pl.__version__}'s private API, so it needs a build for this Polars"
        )
    if not isinstance(returned, pl.DataFrame) or returned.height != 1:
        return "a one-row frame round trip through the native module did not return one row"
    return None


def _module_path(module: ModuleType | None) -> str:
    if module is None:
        return "<not importable>"
    file = getattr(module, "__file__", None)
    return str(Path(file).parent) if file else "<unknown>"


def _error(
    problems: list[str],
    *,
    installed: str | None,
    module: ModuleType | None,
    source: str,
) -> PriceContourCompatibilityError:
    lines = [
        "haute cannot use the installed price-contour:",
        *(f"  - {problem}" for problem in problems),
        f"Installed version: {installed if installed is not None else '<no distribution>'}",
        f"Required: price-contour{REQUIRED_SPECIFIER} (prereleases rejected)",
        f"Module path: {_module_path(module)}",
        f"Installed from: {source}",
        REMEDY,
    ]
    return PriceContourCompatibilityError("\n".join(lines))


def _verify() -> tuple[ModuleType, PriceContourInstall]:
    problems: list[str] = []
    install: PriceContourInstall | None = None
    try:
        distribution = metadata.distribution(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        problems.append(f"no installed distribution named {DISTRIBUTION!r}")
    else:
        install = PriceContourInstall(distribution.version, *install_source(distribution))
    installed = install.version if install is not None else None
    source = install.description if install is not None else "<no distribution>"

    try:
        module = importlib.import_module(MODULE)
        importlib.import_module(NATIVE_MODULE)
    except ImportError as exc:
        problems.append(f"import failed: {exc}")
        raise _error(problems, installed=installed, module=None, source=source) from exc

    module_version = getattr(module, "__version__", None)
    if installed is not None and module_version != installed:
        problems.append(
            f"module __version__ {module_version!r} does not match the installed "
            f"distribution metadata {installed!r} (a stale or shadowing build)"
        )
    if installed is not None:
        try:
            parsed = Version(installed)
        except InvalidVersion:
            problems.append(f"version {installed!r} is not a valid PEP 440 version")
        else:
            if not SpecifierSet(REQUIRED_SPECIFIER).contains(parsed, prereleases=ALLOW_PRERELEASES):
                problems.append(
                    f"version {installed} does not satisfy {REQUIRED_SPECIFIER}"
                    + (" (prereleases are rejected)" if parsed.is_prerelease else "")
                )
    for symbol in _missing_symbols(module):
        problems.append(f"missing symbol price_contour.{symbol}")
    for parameter in _missing_parameters(module):
        problems.append(f"missing parameter {parameter}")
    if not problems:
        failure = _round_trip_failure(module)
        if failure is not None:
            problems.append(failure)

    if problems:
        raise _error(problems, installed=installed, module=module, source=source)
    assert install is not None  # a missing distribution is a reported problem
    logger.info(
        "price_contour_verified",
        version=install.version,
        source=install.description,
        path=_module_path(module),
    )
    return module, install


@functools.cache
def _verified() -> tuple[ModuleType, PriceContourInstall]:
    return _verify()


def price_contour() -> ModuleType:
    """Return the verified ``price_contour`` package, verifying it once per process."""
    return _verified()[0]


def price_contour_install() -> PriceContourInstall:
    """Return where the verified ``price-contour`` distribution was installed from."""
    return _verified()[1]


def clear_price_contour_cache() -> None:
    """Forget the verification so the next call re-imports and re-checks (tests only)."""
    _verified.cache_clear()
