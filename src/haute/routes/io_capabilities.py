"""Canonical data input/output capability endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from haute._polars_io_registry import registry_capabilities
from haute.schemas import IoCapabilitiesResponse

router = APIRouter(prefix="/api", tags=["io-capabilities"])


@router.get("/io-capabilities", response_model=IoCapabilitiesResponse)
async def get_io_capabilities() -> IoCapabilitiesResponse:
    """Return the versioned, ordered editor contract derived from the registry."""
    return IoCapabilitiesResponse.model_validate(registry_capabilities())
