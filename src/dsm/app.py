"""Dell Server Manager - FastAPI application entry point v2.

Manages app lifecycle, registers all API routers, serves the React SPA,
and seeds default data (roles, admin user) on startup.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dsm import __version__
from dsm.api.auth import router as auth_router
from dsm.api.fans import router as fans_router
from dsm.api.groups import router as groups_router
from dsm.api.health import router as health_router
from dsm.api.servers import router as servers_router
from dsm.api.settings import router as settings_router
from dsm.api.sensors import router as sensors_router, poller
from dsm.api.temp_profiles import router as temp_profiles_router
from dsm.api.users import router as users_router
from dsm.auth import seed_default_roles, seed_default_admin
from dsm.config import settings
from dsm.database import init_db, async_session

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"DSM v{__version__} starting...")

    # Initialize database
    await init_db()

    # Seed default data
    async with async_session() as session:
        await seed_default_roles(session)
        await seed_default_admin(
            session,
            username=settings.bootstrap_admin_username,
            password=settings.bootstrap_admin_password,
            email=settings.bootstrap_admin_email,
        )

    # Start sensor poller
    await poller.start()

    # Start MCP server (for AI agent integration) if enabled
    mcp_task = None
    if settings.mcp_enabled:
        try:
            from uvicorn import Config, Server as UvicornServer
            from dsm.mcp.server import mcp as mcp_server

            # Expose FastMCP's streamable HTTP endpoint at /mcp.
            mcp_app = mcp_server.streamable_http_app()
            mcp_config = Config(
                app=mcp_app,
                host="0.0.0.0",
                port=settings.mcp_port,
                log_level="warning",
                lifespan="off",
            )
            mcp_uvicorn = UvicornServer(mcp_config)
            mcp_task = asyncio.create_task(mcp_uvicorn.serve())
            logger.info(f"MCP server started on port {settings.mcp_port}")
        except Exception as exc:
            logger.warning(f"MCP server failed to start (AI agent tools unavailable): {exc}")

    logger.info("DSM started - database initialized, roles seeded, sensor polling active")

    yield

    logger.info("DSM shutting down...")
    await poller.stop()
    if mcp_task:
        mcp_task.cancel()
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass
    logger.info("DSM stopped")


app = FastAPI(
    title="Dell Server Manager",
    description="Unified management for Dell PowerEdge servers via iDRAC",
    version=__version__,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Public ──────────────────────────────────────────────────────────────────
app.include_router(health_router)

# ─── Auth ────────────────────────────────────────────────────────────────────
app.include_router(auth_router)

# ─── API Routers ─────────────────────────────────────────────────────────────
app.include_router(servers_router)
app.include_router(sensors_router)
app.include_router(fans_router)
app.include_router(users_router)
app.include_router(groups_router)
app.include_router(temp_profiles_router)
app.include_router(settings_router)

# ─── Frontend ────────────────────────────────────────────────────────────────
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    assets_dir = os.path.join(frontend_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_path = os.path.join(frontend_dir, "index.html")

    @app.get("/")
    async def serve_frontend():
        from fastapi.responses import FileResponse
        if os.path.exists(index_path):
            # Avoid stale SPA shells after rebuilding the frontend during live DSM
            # debugging. The hashed JS assets can change while the browser still
            # holds an older index.html that points at previous bundles.
            return FileResponse(index_path, headers={"Cache-Control": "no-store, max-age=0"})
        return {"error": "Frontend not built."}

    # Catch-all for SPA client-side routing
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for all non-API routes."""
        # Don't interfere with API routes or bundled frontend assets
        if full_path.startswith(("auth/", "servers/", "sensors/", "fans/",
                                 "users/", "groups/", "temp-profiles/",
                                 "settings/", "health", "info", "assets/")):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        from fastapi.responses import FileResponse
        if os.path.exists(index_path):
            # Same no-store rationale as `/`: always serve the latest SPA shell
            # for client-side routes after a rebuild.
            return FileResponse(index_path, headers={"Cache-Control": "no-store, max-age=0"})
        return {"error": "Frontend not built."}
