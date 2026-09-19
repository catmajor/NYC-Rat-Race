from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="NYC Rat Race API",
        description="API + static frontend for the NYC Rat Race project.",
        version="0.1.0",
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health_check() -> HealthResponse:
        """Report whether the API is ready to receive requests."""
        return HealthResponse(status="ok", service="nyc-rat-race-api")

    if frontend_dist is None:
        frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    if frontend_dist.is_dir():
        # Registered last so /health, /docs, /openapi.json still win.
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        @app.get("/", tags=["system"])
        def read_root() -> dict[str, str]:
            """Return basic service information."""
            return {
                "name": "NYC Rat Race API",
                "docs": "/docs",
                "hint": "frontend/dist not built; run `npm run build` in frontend/ and restart.",
            }

    return app


app = create_app()