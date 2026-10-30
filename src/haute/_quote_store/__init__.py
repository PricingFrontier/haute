"""The workbench's quote store (specs/workbench): the form's own tables in the user's
database, each quote's current state in them, and Haute's two tables beside them.

Imported where a store is used, never on the scorer's import path, so a deployed pipeline
loads no database client.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from haute._quote_store._book import (
    ActivityEntry,
    AppQuote,
    OpenQuote,
    PricedOutputs,
    QuoteActivity,
    QuoteBook,
    QuotePage,
    RelatedQuote,
    Source,
    as_written,
    check_reference,
    details_of,
    identity_label,
    with_details,
    without_app_details,
)
from haute._quote_store._environment import StoreLocation, resolve_store
from haute._quote_store._rows import input_rows
from haute._quote_store._store import (
    DEV,
    MisfitCell,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteNotReadyError,
    QuoteStoreEnvironmentError,
    QuoteStoreError,
    QuoteStoreMismatchError,
    QuoteValuesError,
    ReferenceTakenError,
    StaleQuoteError,
    WriteSupersededError,
    open_engine,
    prepare_store,
    store_url,
)
from haute._workbench_form import FormSpec


def open_book(
    url: str,
    form: FormSpec,
    *,
    project: str,
    environment: str = DEV,
    haute_version: str | None,
    clock: Callable[[], datetime] | None = None,
) -> QuoteBook:
    """The book of quotes on the database at *url*, which must hold *form*'s tables for
    *project* in *environment* (:func:`open_engine`), written by an app running Haute
    *haute_version*."""
    engine, schema = open_engine(url, form, project=project, environment=environment)
    return QuoteBook(engine, schema, form, haute_version=haute_version, clock=clock)


__all__ = [
    "DEV",
    "ActivityEntry",
    "AppQuote",
    "MisfitCell",
    "OpenQuote",
    "PricedOutputs",
    "QuoteActivity",
    "QuoteBook",
    "QuoteLockedError",
    "QuoteNotFoundError",
    "QuoteNotReadyError",
    "QuotePage",
    "QuoteStoreEnvironmentError",
    "QuoteStoreError",
    "QuoteStoreMismatchError",
    "QuoteValuesError",
    "ReferenceTakenError",
    "RelatedQuote",
    "Source",
    "StoreLocation",
    "StaleQuoteError",
    "WriteSupersededError",
    "as_written",
    "check_reference",
    "details_of",
    "identity_label",
    "input_rows",
    "open_book",
    "open_engine",
    "prepare_store",
    "resolve_store",
    "store_url",
    "with_details",
    "without_app_details",
]
