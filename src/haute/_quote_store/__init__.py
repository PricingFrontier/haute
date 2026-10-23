"""The workbench's quote store (specs/workbench): saved quotes and their revisions, and the
underwriter app's book of the quotes it keeps.

One schema, Haute's, on a SQLite file for an analyst's machine and CI, and on the server
database the project's IT team provides for the underwriter app. Imported where a store is
used, never on the scorer's import path, so a deployed pipeline loads no database client.
"""

from haute._quote_store._book import (
    AppQuote,
    QuoteBook,
    QuoteEvent,
    QuoteLockedError,
    QuotePage,
    QuoteTimeline,
    RelatedQuote,
    RevisionSummary,
    Source,
    details_of,
    with_details,
)
from haute._quote_store._store import (
    CarriedValues,
    QuoteNotFoundError,
    QuoteRevision,
    QuoteStore,
    QuoteStoreError,
    QuoteStoreNotMigratedError,
    QuoteSummary,
    StaleQuoteError,
    carry_values,
    migrate_store,
    open_store,
    store_url,
)

__all__ = [
    "AppQuote",
    "CarriedValues",
    "QuoteBook",
    "QuoteEvent",
    "QuoteLockedError",
    "QuotePage",
    "QuoteTimeline",
    "RelatedQuote",
    "RevisionSummary",
    "Source",
    "QuoteNotFoundError",
    "QuoteRevision",
    "QuoteStore",
    "QuoteStoreError",
    "QuoteStoreNotMigratedError",
    "QuoteSummary",
    "StaleQuoteError",
    "carry_values",
    "details_of",
    "migrate_store",
    "open_store",
    "store_url",
    "with_details",
]
