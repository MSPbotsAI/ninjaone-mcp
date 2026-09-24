"""Gateway credential middleware tests: missing-header 401, and header
values correctly reaching the per-request contextvar (no global-state
leakage across requests).
"""

from starlette.testclient import TestClient

from ninjaone_mcp.__main__ import _build_http_app
from ninjaone_mcp.config import Settings
from ninjaone_mcp.server import create_mcp_server, get_client_from_context


def _make_app():
    settings = Settings()
    mcp = create_mcp_server(settings)
    return _build_http_app(mcp, settings), settings


def test_health_is_local_and_does_not_require_credentials():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_missing_header_returns_401_with_required_headers_listed():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "X-Ninja-Token" in body["required_headers"]
        assert "X-Ninja-Region" in body["optional_headers"]
        assert "X-Ninja-Base-Url" in body["optional_headers"]


def test_header_present_reaches_request_context():
    # Directly exercises the middleware's contextvar plumbing without a full
    # MCP protocol round-trip: confirms the header values that arrive on
    # the request are exactly what get_client_from_context sees, and that
    # they're reset afterward (no leakage to the next request).
    import asyncio

    from ninjaone_mcp.server import GatewayTokenMiddleware, _gateway_creds_var

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-ninja-token", b"test-token"),
                (b"x-ninja-region", b"eu"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["creds"] == ("test-token", "eu", "")
    # After the request completes, the contextvar must be reset — a fresh
    # get() outside any request context sees no leftover credential.
    assert _gateway_creds_var.get() is None


def test_client_factory_returns_none_without_context():
    settings = Settings()
    assert get_client_from_context(settings) is None


def test_base_url_header_reaches_request_context_and_wins_over_region():
    # The ninjaone-app (API Services) integration sends X-Ninja-Base-Url instead of a
    # region key, because there the region is the customer's own choice rather than a
    # property of a shared app. When both arrive, the per-tenant URL must win — otherwise
    # a stale static X-Ninja-Region would silently redirect one tenant's calls at another
    # region's host, which fails as an authorization error rather than anything legible.
    import asyncio

    from ninjaone_mcp.server import GatewayTokenMiddleware, _gateway_creds_var

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["creds"] = _gateway_creds_var.get()
        seen["client"] = get_client_from_context(settings)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-ninja-token", b"test-token"),
                (b"x-ninja-region", b"us2"),
                (b"x-ninja-base-url", b"https://eu.ninjarmm.com"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["creds"] == ("test-token", "us2", "https://eu.ninjarmm.com")
    # The resolved client dials the base_url host, NOT the us2 host the region names.
    assert seen["client"]._base_url == "https://eu.ninjarmm.com"
    assert _gateway_creds_var.get() is None


def test_region_still_resolves_when_no_base_url_sent():
    # The delegated `ninjaone` integration sends only X-Ninja-Region. Its behaviour must be
    # byte-identical to before X-Ninja-Base-Url existed.
    import asyncio

    from ninjaone_mcp.server import GatewayTokenMiddleware

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["client"] = get_client_from_context(settings)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-ninja-token", b"test-token"),
                (b"x-ninja-region", b"us2"),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["client"]._base_url == "https://us2.ninjarmm.com"


def test_run_script_tool_is_omitted_when_script_execution_disabled():
    # The whole point of the ninjaone-app split: that deployment must not advertise a tool
    # it cannot perform. Also asserts nothing ELSE was dropped along with it.
    import asyncio

    from ninjaone_mcp.server import create_mcp_server

    async def names(enabled):
        mcp = create_mcp_server(Settings(enable_script_execution=enabled))
        return {t.name for t in await mcp.list_tools()}

    full = asyncio.run(names(True))
    reduced = asyncio.run(names(False))
    assert full - reduced == {"ninjaone_run_script_on_device"}
    assert "ninjaone_get_automation_scripts" in reduced
    assert "ninjaone_get_device_active_jobs" in reduced


def test_instructions_do_not_name_the_script_tool_when_disabled():
    # An agent reads the server instructions before the tool list; naming a tool that is
    # not registered sends it after a capability this deployment does not have.
    from ninjaone_mcp.server import create_mcp_server

    enabled = create_mcp_server(Settings(enable_script_execution=True))
    disabled = create_mcp_server(Settings(enable_script_execution=False))
    assert "ninjaone_run_script_on_device" in enabled.instructions
    assert "ninjaone_run_script_on_device" not in disabled.instructions
