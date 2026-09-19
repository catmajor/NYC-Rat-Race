from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


app = FastAPI(
    title="NYC Rat Race API",
    description="API starter for the NYC Rat Race project.",
    version="0.1.0",
)


@app.get("/", tags=["system"])
def read_root() -> dict[str, str]:
    """Return basic service information."""
    return {
        "name": "NYC Rat Race API",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check() -> HealthResponse:
    """Report whether the API is ready to receive requests."""
    return HealthResponse(status="ok", service="nyc-rat-race-api")
