"""Health check and system info endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter

from dsm import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {
        "status": "healthy",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/info")
async def system_info():
    """DSM system information."""
    import platform
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
