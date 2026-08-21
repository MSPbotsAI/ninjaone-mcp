import contextvars
from collections.abc import Callable
from typing import NamedTuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import NinjaOneClient
from .config import Settings, region_base_url


class _GatewayCreds(NamedTuple):
    client_id: str
    client_secret: str
    region: str
    scopes: str
    # Optional second identity for actions that need a real user's own
    # NinjaOne permissions rather than this app's scope grant (confirmed:
    # running a script) — see api_client.py's NinjaOneClient docstring.
    # Empty string means "not configured", same convention as region/scopes
    # above.
    user_client_id: str
    user_client_secret: str
    refresh_token: str


# Per-request credential isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
# Nothing here is ever cached outside the request's own contextvar frame —
# see api_client.py's docstring on why the derived OAuth2 token isn't
# cached across requests either.
_gateway_creds_var: contextvars.ContextVar[_GatewayCreds | None] = contextvars.ContextVar(
    "ninjaone_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> NinjaOneClient | None:
    """Resolve the active machine-identity NinjaOneClient (client_credentials)."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    return NinjaOneClient(
        creds.client_id, creds.client_secret, region_base_url(creds.region), creds.scopes or None
    )


def get_user_client_from_context(settings: Settings) -> NinjaOneClient | None:
    """Resolve the active user-identity NinjaOneClient (refresh_token), or
    None if the caller didn't send the three X-Ninja-User-*/X-Ninja-Refresh-
    Token headers — that's a normal, expected state for any tenant that
    hasn't set up a Web Application app, not an error by itself.
    """
    creds = _gateway_creds_var.get()
    if not creds or not (creds.user_client_id and creds.user_client_secret and creds.refresh_token):
        return None
    return NinjaOneClient(
        creds.user_client_id,
        creds.user_client_secret,
        region_base_url(creds.region),
        refresh_token=creds.refresh_token,
    )


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Ninja-Client-Id and X-Ninja-Client-Secret (both required),
    X-Ninja-Region (optional, defaults to "us"), and X-Ninja-Scopes
    (optional, space/comma-separated subset of monitoring/management/
    control — defaults to "monitoring management" if omitted) from
    request headers and stores them in the contextvar. NinjaOne rejects a
    client_credentials request asking for a scope the app was never
    granted (400 invalid_scope) rather than narrowing it, so a caller whose
    app lacks e.g. `control` MUST send X-Ninja-Scopes naming only what it
    actually has. Returns 401 if a required header is missing on /mcp
    requests.

    Also reads three independent, optional headers for a second, *user*-
    context identity: X-Ninja-User-Client-Id, X-Ninja-User-Client-Secret,
    X-Ninja-Refresh-Token (a Web Application app's credentials plus a
    refresh token from that app's one-time browser authorization). These
    are not required — most tools never use them — but ninjaone_run_script_
    on_device needs them because NinjaOne rejects script execution from a
    machine identity regardless of scope.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        client_id = request.headers.get("x-ninja-client-id")
        client_secret = request.headers.get("x-ninja-client-secret")
        region = request.headers.get("x-ninja-region")
        scopes = request.headers.get("x-ninja-scopes")
        user_client_id = request.headers.get("x-ninja-user-client-id")
        user_client_secret = request.headers.get("x-ninja-user-client-secret")
        refresh_token = request.headers.get("x-ninja-refresh-token")
        if not client_id or not client_secret:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": (
                        "This server requires the X-Ninja-Client-Id and "
                        "X-Ninja-Client-Secret headers (a NinjaOne API "
                        "Services OAuth2 app's client credentials)"
                    ),
                    "required_headers": ["X-Ninja-Client-Id", "X-Ninja-Client-Secret"],
                    "optional_headers": [
                        "X-Ninja-Region",
                        "X-Ninja-Scopes",
                        "X-Ninja-User-Client-Id",
                        "X-Ninja-User-Client-Secret",
                        "X-Ninja-Refresh-Token",
                    ],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set(
            _GatewayCreds(
                client_id,
                client_secret,
                region or "",
                scopes or "",
                user_client_id or "",
                user_client_secret or "",
                refresh_token or "",
            )
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_creds_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all NinjaOne tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disable it so the server works
    # correctly behind a reverse proxy or docker network.
    mcp = FastMCP(
        name="ninjaone-mcp",
        instructions=(
            "NinjaOne is an RMM (remote monitoring and management) platform MSPs "
            "use to manage clients' IT fleets — organizations (customer accounts), "
            "devices, monitoring alerts, service-desk ticketing, and running scripts "
            "or built-in actions on devices. Tool groups: ninjaone_get_organizations/"
            "ninjaone_get_organization/ninjaone_create_organization/"
            "ninjaone_get_organization_locations/ninjaone_get_organization_devices "
            "manage customer accounts; ninjaone_get_devices/ninjaone_get_device/"
            "ninjaone_get_device_alerts/ninjaone_get_device_activities/"
            "ninjaone_get_device_services/ninjaone_reboot_device cover device "
            "inventory and control (reboot is destructive); ninjaone_get_alerts/"
            "ninjaone_reset_alert manage active monitoring alerts; "
            "ninjaone_get_ticket_boards/ninjaone_get_tickets/ninjaone_create_ticket/"
            "ninjaone_update_ticket/ninjaone_get_ticket_log_entries cover the "
            "service-desk; ninjaone_get_automation_scripts/"
            "ninjaone_get_device_scripting_options/ninjaone_run_script_on_device/"
            "ninjaone_get_active_jobs/ninjaone_get_device_active_jobs cover scripting "
            "and the jobs it queues (running a script is destructive). Typical flow: "
            "ninjaone_get_organizations or ninjaone_get_devices to find an id, then "
            "a device/org-scoped tool; for scripting, ninjaone_get_device_scripting_"
            "options to see what's runnable on a device before ninjaone_run_script_"
            "on_device, then ninjaone_get_device_active_jobs to watch it run. "
            "ninjaone_run_script_on_device alone needs a second, user-context "
            "credential — see its own description."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    client_factory: Callable[[], NinjaOneClient | None] = lambda: get_client_from_context(settings)
    user_client_factory: Callable[[], NinjaOneClient | None] = lambda: get_user_client_from_context(
        settings
    )

    from .tools import alerts, automation, devices, organizations, tickets

    organizations.register(mcp, client_factory)
    devices.register(mcp, client_factory)
    alerts.register(mcp, client_factory)
    tickets.register(mcp, client_factory)
    automation.register(mcp, client_factory, user_client_factory)

    return mcp
