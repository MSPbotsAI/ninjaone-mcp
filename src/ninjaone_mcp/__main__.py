import sys

from .config import get_settings
from .server import GatewayTokenMiddleware, create_mcp_server


def _build_http_app(mcp, settings):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    from starlette.responses import PlainTextResponse, Response

    from .callbacks import STORE

    async def callback(request: Request) -> Response:
        # Public and unauthenticated by design (the caller is a script on a customer
        # device): the token itself is the credential. Anything not live is a bare 404 —
        # unknown, expired and wrong-method tokens look the same from outside.
        token = request.path_params["token"]
        if request.method == "GET":
            body = STORE.get_body(token)
            if body is not None:
                return PlainTextResponse(body)
        elif STORE.receive(token, await request.body(), request.headers.get("content-type")):
            return JSONResponse({"ok": True})
        print(f"callback: no live {request.method} token {token[:6]}…", file=sys.stderr)
        return Response(status_code=404)

    mcp_app = mcp.streamable_http_app()  # Starlette app owning the session-manager lifespan
    mounted = GatewayTokenMiddleware(mcp_app, settings)

    # Mount() does NOT run a sub-app's lifespan, so the streamable-http session
    # manager's task group would never start ("Task group is not initialized").
    # Propagate the MCP app's lifespan to the outer app explicitly.
    routes = [Route("/health", health)]
    if settings.enable_script_execution:
        # Must precede the catch-all Mount. GatewayTokenMiddleware only guards /mcp anyway.
        routes.append(Route("/cb/{token}", callback, methods=["GET", "POST"]))
    routes.append(Mount("/", app=mounted))
    return Starlette(
        routes=routes,
        lifespan=lambda app: mcp_app.router.lifespan_context(app),
    )


def main() -> None:
    settings = get_settings()
    mcp = create_mcp_server(settings)

    import uvicorn

    app = _build_http_app(mcp, settings)
    print(
        f"NinjaOne MCP server listening on http://{settings.mcp_http_host}:{settings.mcp_http_port}/mcp",
        file=sys.stderr,
    )
    uvicorn.run(app, host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
