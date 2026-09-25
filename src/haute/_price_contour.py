"""The one runtime import point for the external ``price_contour`` solver library.

Every runtime use of ``price_contour`` in ``src/haute`` goes through
:func:`price_contour`, which imports the package once per process and verifies
that the installed build is one haute can drive: the distribution metadata and
the module agree on a version, that version satisfies haute's pinned specifier
(prereleases rejected), every symbol haute calls exists, every keyword haute
passes to a Python wrapper is still accepted, and the native extension loads.
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
import json
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, TypeAlias
from urllib.parse import unquote, urlparse

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from haute._logging import get_logger

logger = get_logger(component="price_contour")

DISTRIBUTION = "price-contour"
MODULE = "price_contour"
NATIVE_MODULE = "price_contour._price_contour"

# Must equal the ``price-contour`` specifier in pyproject.toml exactly
# (``tests/test_dependency_contracts.py`` pins the equality).
REQUIRED_SPECIFIER = ">=0.5.0,<0.6"

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
    "RatebookFactorContexts",
    # A ratebook frontier keeps each point's factor tables; haute materialises
    # a selected point from them instead of re-solving.
    "RatebookFrontierResult.factor_tables",
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
    "apply_from_grid": ("lambdas", "constraints"),
    "build_grid_from_parquet_chunked": (
        "quote_id",
        "scenario_index",
        "scenario_value",
        "objective",
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


InstallKind: TypeAlias = Literal["wheel", "editable", "direct_url"]


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


def _install_source(distribution: metadata.Distribution) -> tuple[InstallKind, str | None]:
    """Classify an installed distribution by its PEP 610 ``direct_url.json``."""
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        return "wheel", None
    record = json.loads(raw)
    url = record["url"]
    parsed = urlparse(url)
    location = unquote(parsed.path) if parsed.scheme == "file" else url
    if record.get("dir_info", {}).get("editable") is True:
        return "editable", location
    return "direct_url", location


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
        install = PriceContourInstall(distribution.version, *_install_source(distribution))
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
