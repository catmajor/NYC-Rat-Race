"""Vercel ASGI entrypoint for the FastAPI application."""

from pathlib import Path
import sys
from urllib.parse import parse_qs


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.main import app as fastapi_app



class RewrittenPathApp:
    """Restore the original route after Vercel's internal rewrite."""

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            route = parse_qs(scope.get("query_string", b"").decode()).get("route", [None])[0]
            if route:
                scope = dict(scope)
                scope["path"] = route
                scope["raw_path"] = route.encode()
        await self.application(scope, receive, send)


# Vercel's Python runtime supports ASGI applications directly.
app = RewrittenPathApp(fastapi_app)
