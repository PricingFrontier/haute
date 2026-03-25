"""Haute - Open-source pricing engine for insurance teams on Databricks."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("haute")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

from haute._types import HauteError
from haute.pipeline import Pipeline, Submodel
from haute.prepare import clean_columns

__all__ = ["HauteError", "Pipeline", "Submodel", "clean_columns"]
