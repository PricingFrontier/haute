"""Haute - Open-source pricing engine for insurance teams on Databricks."""

from typing import TYPE_CHECKING, Any

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("haute")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

from haute.errors import HauteError

if TYPE_CHECKING:
    from haute.pipeline import Pipeline, Submodel

__all__ = ["HauteError", "Pipeline", "Submodel"]


def __getattr__(name: str) -> Any:
    if name in {"Pipeline", "Submodel"}:
        from haute.pipeline import Pipeline, Submodel

        return {"Pipeline": Pipeline, "Submodel": Submodel}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), "Pipeline", "Submodel"})
