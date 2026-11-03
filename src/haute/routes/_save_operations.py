"""The pipeline saves this process committed, by the client's operation.

A tab whose save lost its answer (a dropped connection, a timeout) sends the same
save again under the same ``operation``. The save route answers that repeat with the
first save's response when the first committed, so the save writes once and the tab
learns the revision it wrote, even after another writer changed the file. Every read
and write happens under ``save_lock``.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

from haute.schemas import SavePipelineRequest, SavePipelineResponse

MAX_REMEMBERED_SAVES = 256


class SaveOperationReusedError(Exception):
    """An operation names a committed save whose request was different."""


def save_request_digest(body: SavePipelineRequest) -> str:
    """The request as a repeat must send it again: without the operation, and without
    the connection, which a tab may have reconnected before repeating."""
    payload = body.model_dump_json(exclude={"operation", "connection_id"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SaveOperations:
    """The newest committed saves, by operation, with their request digest and response."""

    def __init__(self, capacity: int = MAX_REMEMBERED_SAVES) -> None:
        self._capacity = capacity
        self._saves: OrderedDict[str, tuple[str, SavePipelineResponse]] = OrderedDict()

    def replay(self, operation: str, digest: str) -> SavePipelineResponse | None:
        """The response of the committed save *operation* names, or None when none is
        remembered; ``SaveOperationReusedError`` when that save's request differed."""
        remembered = self._saves.get(operation)
        if remembered is None:
            return None
        recorded_digest, response = remembered
        if recorded_digest != digest:
            raise SaveOperationReusedError(
                "The operation names a different save. Send a new save with a new operation."
            )
        return response.model_copy(deep=True)

    def record(self, operation: str, digest: str, response: SavePipelineResponse) -> None:
        self._saves[operation] = (digest, response.model_copy(deep=True))
        self._saves.move_to_end(operation)
        while len(self._saves) > self._capacity:
            self._saves.popitem(last=False)


committed_saves = SaveOperations()
