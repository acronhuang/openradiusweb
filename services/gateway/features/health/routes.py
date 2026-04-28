"""Health check endpoint (Layer 3 only).

This feature has no service, repository, or schemas slots — there is no
business logic to compose, no DB to read, and the response model lives
in `orw_common.models.common` because it is shared with other services.
This is the canonical shape for a feature that is purely a thin shell.
"""
from fastapi import APIRouter

from orw_common import __version__
from orw_common.models.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Service health check."""
    return HealthResponse(
        status="ok",
        service="orw-gateway",
        version=__version__,
    )
