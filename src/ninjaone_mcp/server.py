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
    token: str
    region: str
    # Optional second identity for actions that need a real user's own
    # NinjaOne permissions rather than this token's grant (confirmed:
    # running a script) — see api_client.py's NinjaOneClient docstring.
    # Empty string means "not configured".
    user_token: str


# Per-request credential isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
# Nothing here is ever cached outside the request's own contextvar frame —
# the gateway is responsible for exchanging/refreshing these tokens; this
# server only ever holds one for the lifetime of one request.
_gateway_creds_var: contextvars.ContextVar[_GatewayCreds | None] = contextvars.ContextVar(
    "ninjaone_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> NinjaOneClient | None:
    """Resolve the active machine-identity NinjaOneClient."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    return NinjaOneClient(creds.token, region_base_url(creds.region))


def get_user_client_from_context(settings: Settings) -> NinjaOneClient | None:
    """Resolve the active user-identity NinjaOneClient, or None if the
    caller didn't send X-Ninja-User-Token — that's a normal, expected state
    for any tenant that hasn't set up a Web Application app, not an error
    by itself.
    """
    creds = _gateway_creds_var.get()
    if not creds or not creds.user_token:
        return None
    return NinjaOneClient(creds.user_token, region_base_url(creds.region))


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Ninja-Token (required) — an already-exchanged OAuth2 bearer
    access token for a machine identity — and X-Ninja-Region (optional,
    defaults to "us") from request headers and stores them in the
    contextvar. The gateway is responsible for the OAuth2 exchange
    (client_credentials) and for refreshing the token before it expires;
    this server only ever uses whatever token it's handed, per request.
    Returns 401 if X-Ninja-Token is missing on /mcp requests.

    Also reads one independent, optional header for a second, *user*-
    context identity: X-Ninja-User-Token (an already-exchanged refresh_
    token-grant access token). Most tools never use it, but ninjaone_run_
    script_on_device needs it because NinjaOne rejects script execution
    from a machine identity regardless of scope.
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
        token = request.headers.get("x-ninja-token")
        region = request.headers.get("x-ninja-region")
        user_token = request.headers.get("x-ninja-user-token")
        if not token:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": (
                        "This server requires the X-Ninja-Token header (an "
                        "already-exchanged OAuth2 bearer access token)"
                    ),
                    "required_headers": ["X-Ninja-Token"],
                    "optional_headers": ["X-Ninja-Region", "X-Ninja-User-Token"],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set(_GatewayCreds(token, region or "", user_token or ""))
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
