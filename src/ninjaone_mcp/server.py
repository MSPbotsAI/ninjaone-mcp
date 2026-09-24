import contextvars
from collections.abc import Callable
from typing import NamedTuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import NinjaOneClient
from .config import Settings, resolve_base_url


class _GatewayCreds(NamedTuple):
    token: str
    region: str
    base_url: str = ""


# Per-request credential isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
# Nothing here is ever cached outside the request's own contextvar frame —
# the gateway is responsible for exchanging/refreshing this token; this
# server only ever holds one for the lifetime of one request.
_gateway_creds_var: contextvars.ContextVar[_GatewayCreds | None] = contextvars.ContextVar(
    "ninjaone_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> NinjaOneClient | None:
    """Resolve the active NinjaOneClient for the current request context."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    return NinjaOneClient(creds.token, resolve_base_url(creds.base_url, creds.region))


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Ninja-Token (required) — an already-exchanged OAuth2 bearer
    access token — plus ONE of X-Ninja-Base-Url or X-Ninja-Region (both
    optional; the region defaults to "us") from request headers and stores
    them in the contextvar. The two name the same thing in different
    shapes and X-Ninja-Base-Url wins; see config.resolve_base_url for
    which integration sends which. The gateway is
    responsible for the OAuth2 exchange and for refreshing the token
    before it expires; this server only ever uses whatever token it's
    handed, per request. One token covers every tool, including running a
    script — confirmed live: a NinjaOne Web Application app's user-context
    token successfully ran a script (NinjaOne recorded the job against
    that user's identity) and also worked for every read tool, so there is
    no reason to keep a separate machine-identity token for this service.
    Returns 401 if X-Ninja-Token is missing on /mcp requests.
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
        base_url = request.headers.get("x-ninja-base-url")
        if not token:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": (
                        "This server requires the X-Ninja-Token header (an "
                        "already-exchanged OAuth2 bearer access token)"
                    ),
                    "required_headers": ["X-Ninja-Token"],
                    "optional_headers": ["X-Ninja-Base-Url", "X-Ninja-Region"],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set(
            _GatewayCreds(token, region or "", base_url or ""))
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_creds_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all NinjaOne tools."""
    # The scripting sentences below must describe the tools ACTUALLY registered further
    # down: naming ninjaone_run_script_on_device in a deployment that does not register it
    # sends agents after a capability that isn't there, and the failure they get back is a
    # generic unknown-tool error rather than anything explaining why.
    if settings.enable_script_execution:
        scripting = (
            "ninjaone_get_automation_scripts/"
            "ninjaone_get_device_scripting_options/ninjaone_run_script_on_device/"
            "ninjaone_get_active_jobs/ninjaone_get_device_active_jobs cover scripting "
            "and the jobs it queues (running a script is destructive)."
        )
        scripting_flow = (
            " for scripting, ninjaone_get_device_scripting_options to see what's "
            "runnable on a device before ninjaone_run_script_on_device, then "
            "ninjaone_get_device_active_jobs to watch it run."
        )
    else:
        scripting = (
            "ninjaone_get_automation_scripts/"
            "ninjaone_get_device_scripting_options/ninjaone_get_active_jobs/"
            "ninjaone_get_device_active_jobs cover scripting VISIBILITY only. This "
            "deployment authenticates as a machine identity and has NO tool for running "
            "a script or action on a device — NinjaOne binds script execution to a real "
            "user. It can report which scripts exist and which jobs are running, but "
            "cannot start one; do not tell the user a script was run."
        )
        scripting_flow = ""
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
            "service-desk; " + scripting + " Typical flow: "
            "ninjaone_get_organizations or ninjaone_get_devices to find an id, then "
            "a device/org-scoped tool;" + (scripting_flow or "")
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        json_response=True,
    )

    client_factory: Callable[[], NinjaOneClient | None] = lambda: get_client_from_context(settings)

    from .tools import alerts, automation, devices, organizations, tickets

    organizations.register(mcp, client_factory)
    devices.register(mcp, client_factory)
    alerts.register(mcp, client_factory)
    tickets.register(mcp, client_factory)
    automation.register(mcp, client_factory,
                        include_run_script=settings.enable_script_execution)

    return mcp
