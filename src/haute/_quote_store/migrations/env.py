"""Alembic's environment for the quote store (specs/workbench).

``haute._quote_store.migrate_store`` runs Alembic on one connection it opens and hands over
in the config's attributes; nothing here reads a file or the environment.
"""

from __future__ import annotations

from alembic import context

from haute._quote_store._tables import METADATA, VERSION_TABLE

connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=METADATA,
    version_table=VERSION_TABLE,
    # SQLite changes a column or a constraint only by copying the table. This shapes only the
    # scripts `--autogenerate` writes: a migration written by hand that changes one does so
    # inside `op.batch_alter_table` itself, or it fails on SQLite.
    render_as_batch=True,
)
with context.begin_transaction():
    context.run_migrations()
