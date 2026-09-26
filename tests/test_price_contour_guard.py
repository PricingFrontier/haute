"""The fail-loud ``price_contour`` compatibility guard (``haute._price_contour``).

Every runtime use of the solver library goes through ``price_contour()``, which
verifies the installed build once per process and reports every problem in one
error. These tests drive the guard against fake installs: a fake distribution
record (version and PEP 610 ``direct_url.json``) and a fake package module.
"""

from __future__ import annotations

import importlib
import json
import types
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

import haute._price_contour as guard
from haute._price_contour import (
    REQUIRED_SPECIFIER,
    REQUIRED_SYMBOLS,
    PriceContourCompatibilityError,
    clear_price_contour_cache,
    price_contour,
    price_contour_install,
)

CHECKOUT = "/work/price-contour"


class _FakeDistribution:
    def __init__(self, version: str, direct_url: dict[str, Any] | None = None) -> None:
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json" and self._direct_url is not None:
            return json.dumps(self._direct_url)
        return None


def _real_module() -> types.ModuleType:
    import price_contour as real

    return real


# The guard reports the module's directory in the platform's own form.
_FAKE_MODULE_DIR = str(Path("/site-packages/price_contour"))


def _fake_package(version: str = "0.5.0", *, drop: tuple[str, ...] = ()) -> types.ModuleType:
    """A package exposing every required top-level symbol of the real library."""
    real = _real_module()
    module = types.ModuleType("price_contour")
    module.__file__ = "/site-packages/price_contour/__init__.py"
    module.__version__ = version  # type: ignore[attr-defined]
    for dotted in REQUIRED_SYMBOLS:
        top = dotted.split(".")[0]
        if top not in drop:
            setattr(module, top, getattr(real, top))
    return module


@pytest.fixture(autouse=True)
def mp() -> Iterator[pytest.MonkeyPatch]:
    """A fresh guard and a patcher undone before the guard is re-warmed.

    The fixture's own patch context (not ``monkeypatch``, which other autouse
    fixtures may already hold) guarantees the fakes are gone when the guard
    re-verifies the real install for the tests that run after this module.
    """
    clear_price_contour_cache()
    with pytest.MonkeyPatch.context() as patcher:
        yield patcher
    clear_price_contour_cache()
    price_contour()


def _install(
    mp: pytest.MonkeyPatch,
    *,
    distribution: _FakeDistribution | None,
    module: types.ModuleType | None = None,
    native_error: ImportError | None = None,
) -> None:
    # ``guard.metadata`` and ``guard.importlib`` are the stdlib modules, so the
    # fakes answer for price-contour only and delegate everything else.
    real_distribution = metadata.distribution
    real_import_module = importlib.import_module

    def fake_distribution(name: str) -> Any:
        if name != "price-contour":
            return real_distribution(name)
        if distribution is None:
            raise metadata.PackageNotFoundError(name)
        return distribution

    def fake_import(name: str, package: str | None = None) -> types.ModuleType:
        if name == "price_contour._price_contour":
            if native_error is not None:
                raise native_error
            return types.ModuleType(name)
        if name != "price_contour":
            return real_import_module(name, package)
        assert module is not None
        return module

    mp.setattr(guard.metadata, "distribution", fake_distribution)
    mp.setattr(guard.importlib, "import_module", fake_import)


def _error(mp: pytest.MonkeyPatch, **install: Any) -> str:
    _install(mp, **install)
    with pytest.raises(PriceContourCompatibilityError) as caught:
        price_contour()
    return str(caught.value)


def test_the_real_install_is_verified_and_cached(mp: pytest.MonkeyPatch) -> None:
    module = price_contour()

    assert module is _real_module()
    calls: list[str] = []
    mp.setattr(guard.metadata, "distribution", lambda name: calls.append(name))
    assert price_contour() is module
    assert calls == []


def test_a_verified_install_logs_one_info_line(mp: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    mp.setattr(guard.logger, "info", lambda event, **fields: events.append((event, fields)))
    _install(
        mp,
        distribution=_FakeDistribution("0.5.0"),
        module=_fake_package(),
    )

    price_contour()
    price_contour()

    assert events == [
        (
            "price_contour_verified",
            {"version": "0.5.0", "source": "wheel", "path": _FAKE_MODULE_DIR},
        )
    ]


def test_an_old_version_names_version_specifier_path_source_and_remedy(
    mp: pytest.MonkeyPatch,
) -> None:
    message = _error(
        mp,
        distribution=_FakeDistribution(
            "0.2.7", {"url": f"file://{CHECKOUT}", "dir_info": {"editable": True}}
        ),
        module=_fake_package("0.2.7"),
    )

    assert "version 0.2.7 does not satisfy >=0.5.0,<0.6" in message
    assert "Installed version: 0.2.7" in message
    assert f"Required: price-contour{REQUIRED_SPECIFIER}" in message
    assert f"Module path: {_FAKE_MODULE_DIR}" in message
    assert f"Installed from: editable checkout {CHECKOUT}" in message
    assert "uv sync --locked" in message
    assert "uv run maturin develop --release" in message


def test_the_next_minor_version_is_rejected(mp: pytest.MonkeyPatch) -> None:
    message = _error(
        mp,
        distribution=_FakeDistribution("0.6.0"),
        module=_fake_package("0.6.0"),
    )

    assert "version 0.6.0 does not satisfy >=0.5.0,<0.6" in message
    assert "Installed from: wheel" in message


@pytest.mark.parametrize("prerelease", ["0.5.2rc1", "0.5.2.dev0", "0.5.2a1"])
def test_prereleases_inside_the_range_are_rejected(mp: pytest.MonkeyPatch, prerelease: str) -> None:
    message = _error(
        mp,
        distribution=_FakeDistribution(prerelease),
        module=_fake_package(prerelease),
    )

    assert f"version {prerelease} does not satisfy" in message
    assert "(prereleases are rejected)" in message


def test_a_later_patch_release_is_accepted(mp: pytest.MonkeyPatch) -> None:
    _install(
        mp,
        distribution=_FakeDistribution("0.5.9"),
        module=_fake_package("0.5.9"),
    )

    assert price_contour_install().version == "0.5.9"


def test_an_importable_module_without_distribution_metadata_is_rejected(
    mp: pytest.MonkeyPatch,
) -> None:
    message = _error(mp, distribution=None, module=_fake_package("0.0.0+local"))

    assert "no installed distribution named 'price-contour'" in message
    assert "Installed version: <no distribution>" in message
    assert "Installed from: <no distribution>" in message


def test_a_module_version_that_disagrees_with_the_metadata_is_rejected(
    mp: pytest.MonkeyPatch,
) -> None:
    message = _error(
        mp,
        distribution=_FakeDistribution("0.5.0"),
        module=_fake_package("0.0.0+local"),
    )

    assert "module __version__ '0.0.0+local' does not match" in message
    assert "'0.5.0'" in message


def test_every_missing_symbol_is_listed_in_one_error(mp: pytest.MonkeyPatch) -> None:
    message = _error(
        mp,
        distribution=_FakeDistribution("0.5.0"),
        module=_fake_package(drop=("apply_from_grid", "RatebookFactorContexts")),
    )

    assert "missing symbol price_contour.apply_from_grid" in message
    assert "missing symbol price_contour.RatebookFactorContexts" in message


def test_a_missing_method_is_a_missing_symbol(mp: pytest.MonkeyPatch) -> None:
    module = _fake_package(drop=("ApplyOptimiser",))

    class ApplyOptimiser:
        def __init__(
            self,
            lambdas: Any,
            objective: Any = None,
            constraints: Any = None,
            *,
            quote_id: Any = None,
            scenario_index: Any = None,
            scenario_value: Any = None,
        ) -> None: ...

        def apply(self, df: Any) -> Any: ...

    module.ApplyOptimiser = ApplyOptimiser  # type: ignore[attr-defined]
    message = _error(mp, distribution=_FakeDistribution("0.5.0"), module=module)

    assert "missing symbol price_contour.ApplyOptimiser.with_explainer_columns" in message


def test_a_removed_keyword_parameter_is_listed(mp: pytest.MonkeyPatch) -> None:
    module = _fake_package(drop=("apply_from_grid", "build_grid_from_parquet_chunked"))

    def apply_from_grid(grid: Any, lambdas: Any) -> Any: ...

    def build_grid_from_parquet_chunked(
        path: Any, constraint_columns: Any, chunk_size: Any, *, quote_id: Any = None
    ) -> Any: ...

    module.apply_from_grid = apply_from_grid  # type: ignore[attr-defined]
    module.build_grid_from_parquet_chunked = build_grid_from_parquet_chunked  # type: ignore[attr-defined]
    message = _error(mp, distribution=_FakeDistribution("0.5.0"), module=module)

    assert "missing parameter apply_from_grid(constraints=...)" in message
    assert "missing parameter build_grid_from_parquet_chunked(scenario_index=...)" in message
    assert "missing parameter build_grid_from_parquet_chunked(objective=...)" in message
    assert "apply_from_grid(lambdas=...)" not in message


def test_a_positional_only_parameter_cannot_take_a_keyword(
    mp: pytest.MonkeyPatch,
) -> None:
    module = _fake_package(drop=("apply_from_grid",))

    def apply_from_grid(grid: Any, lambdas: Any, constraints: Any, /) -> Any: ...

    module.apply_from_grid = apply_from_grid  # type: ignore[attr-defined]
    message = _error(mp, distribution=_FakeDistribution("0.5.0"), module=module)

    assert "missing parameter apply_from_grid(lambdas=...)" in message
    assert "missing parameter apply_from_grid(constraints=...)" in message


def test_version_and_symbol_problems_are_reported_together(
    mp: pytest.MonkeyPatch,
) -> None:
    message = _error(
        mp,
        distribution=_FakeDistribution("0.2.7"),
        module=_fake_package(
            "0.2.7", drop=("build_ratebook_factor_contexts_from_parquet_chunked",)
        ),
    )

    assert "version 0.2.7 does not satisfy" in message
    assert "missing symbol price_contour.build_ratebook_factor_contexts_from_parquet_chunked" in (
        message
    )


def test_a_native_import_failure_is_chained(mp: pytest.MonkeyPatch) -> None:
    native = ImportError("undefined symbol: PyInit__price_contour")
    _install(
        mp,
        distribution=_FakeDistribution(
            "0.5.0", {"url": f"file://{CHECKOUT}", "dir_info": {"editable": True}}
        ),
        module=_fake_package(),
        native_error=native,
    )

    with pytest.raises(PriceContourCompatibilityError) as caught:
        price_contour()

    assert caught.value.__cause__ is native
    message = str(caught.value)
    assert "import failed: undefined symbol: PyInit__price_contour" in message
    assert "Module path: <not importable>" in message
    assert f"Installed from: editable checkout {CHECKOUT}" in message
    assert "uv run maturin develop --release" in message


def test_a_failed_verification_is_not_cached(mp: pytest.MonkeyPatch) -> None:
    _install(mp, distribution=_FakeDistribution("0.2.7"), module=_fake_package("0.2.7"))
    with pytest.raises(PriceContourCompatibilityError):
        price_contour()

    _install(mp, distribution=_FakeDistribution("0.5.0"), module=_fake_package())

    assert price_contour_install().version == "0.5.0"


@pytest.mark.parametrize(
    ("direct_url", "kind", "location", "description"),
    [
        (None, "wheel", None, "wheel"),
        (
            {"url": f"file://{CHECKOUT}", "dir_info": {"editable": True}},
            "editable",
            CHECKOUT,
            f"editable checkout {CHECKOUT}",
        ),
        (
            {"url": "file:///wheels/price_contour-0.5.0.whl", "archive_info": {}},
            "direct_url",
            "/wheels/price_contour-0.5.0.whl",
            "direct URL /wheels/price_contour-0.5.0.whl",
        ),
        (
            {"url": "https://example.com/pc.git", "vcs_info": {"vcs": "git"}},
            "direct_url",
            "https://example.com/pc.git",
            "direct URL https://example.com/pc.git",
        ),
    ],
)
def test_the_install_source_is_classified(
    mp: pytest.MonkeyPatch,
    direct_url: dict[str, Any] | None,
    kind: str,
    location: str | None,
    description: str,
) -> None:
    _install(
        mp,
        distribution=_FakeDistribution("0.5.0", direct_url),
        module=_fake_package(),
    )

    install = price_contour_install()

    assert (install.kind, install.location, install.description) == (kind, location, description)


# ---------------------------------------------------------------------------
# Deploy: the container reinstalls price-contour from the index by version
# ---------------------------------------------------------------------------


def _pinned_container_dependencies() -> list[str]:
    from haute.deploy._container import _pinned_dockerfile_deps
    from tests._deploy_helpers import make_resolved_deploy

    return _pinned_dockerfile_deps(make_resolved_deploy(), haute_requirement="haute==0")


def test_a_wheel_install_pins_the_container_to_its_exact_version(
    mp: pytest.MonkeyPatch,
) -> None:
    _install(mp, distribution=_FakeDistribution("0.5.3"), module=_fake_package("0.5.3"))

    deps = _pinned_container_dependencies()

    assert [dep for dep in deps if dep.startswith("price-contour")] == ["price-contour==0.5.3"]


@pytest.mark.parametrize(
    ("direct_url", "described"),
    [
        (
            {"url": f"file://{CHECKOUT}", "dir_info": {"editable": True}},
            f"editable checkout {CHECKOUT}",
        ),
        (
            {"url": "file:///wheels/price_contour-0.5.0.whl", "archive_info": {}},
            "direct URL /wheels/price_contour-0.5.0.whl",
        ),
    ],
)
def test_a_build_the_index_cannot_reproduce_refuses_to_deploy(
    mp: pytest.MonkeyPatch, direct_url: dict[str, Any], described: str
) -> None:
    from haute.errors import DeployError

    _install(mp, distribution=_FakeDistribution("0.5.0", direct_url), module=_fake_package())

    with pytest.raises(DeployError) as caught:
        _pinned_container_dependencies()

    message = str(caught.value)
    assert f"version 0.5.0 is installed from {described}" in message
    assert "uv sync --locked" in message


def test_an_incompatible_install_refuses_to_deploy_with_the_guard_diagnosis(
    mp: pytest.MonkeyPatch,
) -> None:
    from haute.errors import DeployError

    _install(mp, distribution=_FakeDistribution("0.2.7"), module=_fake_package("0.2.7"))

    with pytest.raises(DeployError) as caught:
        _pinned_container_dependencies()

    assert isinstance(caught.value.__cause__, PriceContourCompatibilityError)
    assert "version 0.2.7 does not satisfy >=0.5.0,<0.6" in str(caught.value)
