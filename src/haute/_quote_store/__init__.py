"""The workbench's quote store (specs/workbench): the form's own tables in the user's
database, each quote's current state in them, and Haute's activity beside them.

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
from haute._quote_store._environment import StoreLocation, connected_store, resolve_store
from haute._quote_store._migrate import Plan, Step, offline_plan, render_sql
from haute._quote_store._providers import SignIn
from haute._quote_store._rows import input_rows
from haute._quote_store._store import (
    HOST_VARIABLES,
    MisfitCell,
    QuoteLockedError,
    QuoteNotFoundError,
    QuoteNotReadyError,
    QuoteStoreEnvironmentError,
    QuoteStoreError,
    QuoteStoreMismatchError,
    QuoteStoreUnreachableError,
    QuoteValuesError,
    ReferenceTakenError,
    StaleQuoteError,
    WriteSupersededError,
    open_engine,
    store_url,
)
from haute._workbench_form import FormSpec


def open_book(
    url: str,
    form: FormSpec,
    *,
    environment: str,
    haute_version: str | None,
    clock: Callable[[], datetime] | None = None,
    sql_schema: str | None = None,
    sign_in: SignIn | None = None,
    named_by: str = HOST_VARIABLES,
) -> QuoteBook:
    """The book of quotes on *environment*'s database at *url*, signed in as *sign_in* says,
    whose catalogue must hold *form*'s tables in the SQL schema *sql_schema*
    (:func:`open_engine`, a database not reached naming *named_by*), written by an app
    running Haute *haute_version*."""
    engine, schema = open_engine(
        url,
        form,
        environment=environment,
        sql_schema=sql_schema,
        sign_in=sign_in,
        named_by=named_by,
    )
    return QuoteBook(engine, schema, form, haute_version=haute_version, clock=clock)


__all__ = [
    "HOST_VARIABLES",
    "ActivityEntry",
    "AppQuote",
    "MisfitCell",
    "OpenQuote",
    "Plan",
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
    "QuoteStoreUnreachableError",
    "QuoteValuesError",
    "ReferenceTakenError",
    "RelatedQuote",
    "SignIn",
    "Source",
    "Step",
    "StoreLocation",
    "StaleQuoteError",
    "WriteSupersededError",
    "as_written",
    "check_reference",
    "connected_store",
    "details_of",
    "identity_label",
    "input_rows",
    "open_book",
    "offline_plan",
    "open_engine",
    "render_sql",
    "resolve_store",
    "store_url",
    "with_details",
    "without_app_details",
]
