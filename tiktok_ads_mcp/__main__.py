"""Entry point for `python -m tiktok_ads_mcp`.

Runs the Talk-to-TikTok MCP server over Streamable HTTP (default), SSE, or stdio.

Auth model: AUTHLESS. No OAuth is advertised, so Claude connects without a login
step. If MCP_AUTH_TOKEN is set, an ASGI gate requires that token on every MCP
request — supplied either as a URL query param (?access_token=...) baked into the
connector URL (Parker-style) or as an `Authorization: Bearer <token>` header.
The /health endpoint is always open so Railway health checks pass.

Environment:
  MCP_TRANSPORT   streamable-http (default) | sse | stdio
  MCP_HOST        bind address (default 0.0.0.0)
  MCP_PORT        bind port fallback (default 8000); Railway injects PORT
  SERVER_URL      public base URL, e.g. https://xxx.up.railway.app (no trailing slash)
  MCP_AUTH_TOKEN  optional shared token; when set, gates every MCP request
  TIKTOK_ACCESS_TOKEN     required for real calls (shared team token)
  TIKTOK_ADVERTISER_ID    optional default advertiser
"""
import logging
import os
import secrets
from urllib.parse import parse_qs

from tiktok_ads_mcp.server import mcp, AUTH_TOKEN

logger = logging.getLogger("talk-to-tiktok")


class TokenGateMiddleware:
    """Authless-with-a-shared-secret ASGI gate.

    When a token is configured, require it on every HTTP request reaching the
    wrapped MCP app — from `?access_token=` in the URL or `Authorization: Bearer`.
    Requests that carry the correct token pass straight through (Claude never sees
    an auth challenge, because the token lives in the connector URL). Requests
    without it get a plain 401. No OAuth metadata is emitted.
    """

    def __init__(self, app, token: str):
        self._app = app
        self._token = token

    def _provided(self, scope) -> str:
        # 1) query string: ?access_token=... (also accept ?token=)
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        val = (qs.get("access_token") or qs.get("token") or [""])[0]
        if val:
            return val
        # 2) Authorization: Bearer <token>
        for k, v in scope.get("headers") or []:
            if k == b"authorization":
                auth = v.decode("latin-1")
                if auth.lower().startswith("bearer "):
                    return auth[7:].strip()
        return ""

    # Well-known prefixes a client probes to discover an OAuth/OIDC sign-in service.
    _OAUTH_DISCOVERY_PREFIXES = (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/.well-known/openid-configuration",
    )

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            # Authless: no OAuth is advertised. 404 the discovery probes so the
            # connector never attempts OAuth/DCR (which would fail — there is no
            # auth server). Applies whether or not a shared-secret token is set.
            if any(path.startswith(p) for p in self._OAUTH_DISCOVERY_PREFIXES):
                from starlette.responses import PlainTextResponse
                await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
                return
            if self._token and not secrets.compare_digest(self._provided(scope), self._token):
                from starlette.responses import JSONResponse
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_app(transport: str = "streamable-http"):
    """Build the Starlette ASGI app: open /health + token-gated MCP mount."""
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    raw_app = mcp.streamable_http_app() if transport == "streamable-http" else mcp.sse_app()
    gated_app = TokenGateMiddleware(raw_app, token=AUTH_TOKEN)

    @asynccontextmanager
    async def lifespan(_app):
        # The StreamableHTTP session manager must run inside its own lifespan.
        async with raw_app.router.lifespan_context(_app):
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "transport": transport,
            "auth_gate": bool(AUTH_TOKEN),
            "tiktok_token_configured": bool(os.environ.get("TIKTOK_ACCESS_TOKEN")),
        })

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health),
            Mount("/", app=gated_app),
        ],
    )


def run_server() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")

    if transport == "stdio":
        logger.info("Starting Talk-to-TikTok MCP (stdio)")
        mcp.run(transport="stdio")
        return

    import uvicorn

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    # Railway injects PORT; MCP_PORT is the local/Docker fallback.
    port = int(os.environ.get("PORT") or os.environ.get("MCP_PORT", "8000"))

    app = build_app(transport)
    logger.info(
        "Starting Talk-to-TikTok MCP (%s) on %s:%s — auth_gate=%s",
        transport, host, port, bool(AUTH_TOKEN),
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
