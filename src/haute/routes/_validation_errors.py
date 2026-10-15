"""FastAPI's 422 for a request its models refuse, without the request repeated in it.

The editor's server and the underwriter app both answer with it, so a refused form or quote
reads as the place and the reason, never as the body sent.
"""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response

#: What each error leaves out: the value it refused, the whole body for a model's own
#: check, and the context its message already says.
_LEFT_OUT = frozenset({"input", "ctx"})


async def request_validation_error_handler(request: Request, exc: Exception) -> Response:
    """FastAPI's list of what a request's models refused, each error's ``loc``, ``msg`` and
    ``type`` as FastAPI gives them, without its ``input`` or ``ctx``: a form refused by its
    own check would otherwise be repeated whole in the answer."""
    errors = [
        {key: value for key, value in error.items() if key not in _LEFT_OUT}
        for error in cast(RequestValidationError, exc).errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
