"""Health check endpoints."""

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
