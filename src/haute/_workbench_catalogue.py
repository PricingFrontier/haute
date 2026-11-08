"""Durable, project-local database schema snapshots for the workbench (specs/workbench)."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from haute._cache import canonical_json
from haute._file_ops import atomic_write_text
from haute._quote_store._environment import StoreLocation
from haute._sandbox import contained_path
from haute._workbench_config import WorkbenchError
from haute.schemas import (
    WorkbenchCatalogueColumn,
    WorkbenchCatalogueTable,
    WorkbenchStoreCatalogueResponse,
)

_locks_guard = threading.Lock()
_locks: dict[Path, threading.Lock] = {}


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    target: str
    snapshot: WorkbenchStoreCatalogueResponse


def catalogue_target(location: StoreLocation) -> str:
    """Fingerprint the target and non-secret identity without persisting either."""
    sign_in = location.sign_in
    identity = (
        None
        if sign_in is None
        else (
            sign_in.auth,
            sign_in.username,
            sign_in.client_id,
            sign_in.tenant_id,
        )
    )
    return hashlib.sha256(
        canonical_json(
            [
                location.environment,
                location.url,
                location.sql_schema,
                identity,
            ]
        ).encode()
    ).hexdigest()


def _read(location: StoreLocation) -> WorkbenchStoreCatalogueResponse:
    from haute._quote_store._compare import read_store_catalogue

    catalogue = read_store_catalogue(location)
    return WorkbenchStoreCatalogueResponse(
        environment=location.environment,
        checked_at=time.time_ns() // 1_000_000,
        target=catalogue_target(location),
        tables=[
            WorkbenchCatalogueTable(
                name=table.name,
                columns=[
                    WorkbenchCatalogueColumn(
                        name=column.name,
                        kind=column.kind,
                        type=column.type,
                        nullable=column.nullable,
                        collation=column.collation,
                        quotes=column.quotes,
                    )
                    for column in table.columns
                ],
                primary_key=list(table.primary_key),
                refers=table.refers,
                quotes=table.quotes,
            )
            for table in catalogue.tables
        ],
    )


def catalogue_snapshot(
    root: Path,
    location: StoreLocation,
    *,
    refresh: bool,
) -> WorkbenchStoreCatalogueResponse:
    """Reuse a matching snapshot, or fetch and atomically replace it on explicit refresh.

    Configuration resolution precedes this call; loading a snapshot never signs in or
    connects to a database. A failed refresh leaves the previous file intact.
    """
    path = contained_path(
        root, root / ".haute_cache/workbench/catalogues" / f"{location.environment}.json"
    )
    target = catalogue_target(location)
    with _locks_guard:
        lock = _locks.setdefault(path, threading.Lock())
    with lock:
        if not refresh:
            try:
                held = _Snapshot.model_validate_json(path.read_bytes())
            except FileNotFoundError:
                held = None
            except (OSError, ValidationError) as exc:
                raise WorkbenchError(
                    f"Could not read the saved database schema for {location.environment}. "
                    "Use Refresh on the schema pane to replace it."
                ) from exc
            if held is not None and held.target == target:
                if held.snapshot.target != target:
                    raise WorkbenchError(
                        "The saved database schema has an inconsistent connection identity. "
                        "Use Refresh to replace it."
                    )
                if held.snapshot.environment != location.environment:
                    raise WorkbenchError(
                        "The saved database schema names another environment. "
                        "Use Refresh to replace it."
                    )
                return held.snapshot
        snapshot = _read(location)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, _Snapshot(target=target, snapshot=snapshot).model_dump_json())
        except OSError as exc:
            raise WorkbenchError(
                f"Could not save the database schema snapshot for {location.environment}. "
                "Check write access to .haute_cache/workbench/catalogues and use Refresh again."
            ) from exc
        return snapshot
