from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import NinjaOneClient, NinjaOneError
from ._common import NO_TOKEN, _MAX_PAGE_SIZE


def register(mcp: FastMCP, client_factory: Callable[[], NinjaOneClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_devices(
        df: Annotated[
            str | None,
            Field(
                description=(
                    'NinjaOne device filter expression, e.g. "org = 5", '
                    '"class = WINDOWS_SERVER", "status = OFFLINE", or combined with '
                    'AND (e.g. "org = 5 AND status = OFFLINE"). Written in NinjaOne\'s '
                    "own device-filter syntax (the same one used in the NinjaOne web "
                    "UI's device filter box) — pass it through verbatim; this tool "
                    "does not validate or translate it. Omit for no filter. NOTE: "
                    "NinjaOne has reportedly dropped this filter silently on some "
                    "tenants when scoping by organization — for an org-scoped list, "
                    "prefer ninjaone_get_organization_devices instead."
                )
            ),
        ] = None,
        limit: Annotated[
            int | None, Field(description="Max devices to return (default 50, max 200).")
        ] = None,
        after: Annotated[
            int | None, Field(description="Pagination cursor: the last device id from a previous page.")
        ] = None,
    ) -> str:
        """List devices across the whole NinjaOne instance, optionally filtered by `df`."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_size = min(limit or 50, _MAX_PAGE_SIZE)
        try:
            result = await client.get("/api/v2/devices", {"df": df, "pageSize": page_size, "after": after})
            return dump_json_capped({"devices": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device(
        device_id: Annotated[int, Field(description="Device identifier.")],
    ) -> str:
        """Get a single device's details by id."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/device/{device_id}")
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device_alerts(
        device_id: Annotated[int, Field(description="Device identifier.")],
    ) -> str:
        """List a device's active alerts (triggered monitoring conditions)."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/device/{device_id}/alerts")
            return dump_json_capped({"alerts": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device_activities(
        device_id: Annotated[int, Field(description="Device identifier.")],
        activity_type: Annotated[str | None, Field(description="Filter by activity type.")] = None,
        status: Annotated[str | None, Field(description="Filter by activity status.")] = None,
        older_than: Annotated[
            int | None, Field(description="Return activities recorded newer than this activity id.")
        ] = None,
        newer_than: Annotated[
            int | None, Field(description="Return activities recorded older than this activity id.")
        ] = None,
        limit: Annotated[
            int | None,
            Field(description="Max activities to return (default 200, vendor min 10, max 1000)."),
        ] = None,
    ) -> str:
        """Get a device's activity log (audit trail of actions/events on it)."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_size = max(10, min(limit or 200, 1000))
        try:
            result = await client.get(
                f"/api/v2/device/{device_id}/activities",
                {
                    "activityType": activity_type,
                    "status": status,
                    "olderThan": older_than,
                    "newerThan": newer_than,
                    "pageSize": page_size,
                },
            )
            return dump_json_capped({"activities": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device_services(
        device_id: Annotated[int, Field(description="Device identifier.")],
        name: Annotated[str | None, Field(description="Filter by service name.")] = None,
        state: Annotated[
            str | None,
            Field(
                description=(
                    "Filter by service state: UNKNOWN, STOPPED, START_PENDING, RUNNING, "
                    "STOP_PENDING, PAUSE_PENDING, PAUSED, or CONTINUE_PENDING."
                )
            ),
        ] = None,
    ) -> str:
        """List Windows services installed on a device."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                f"/api/v2/device/{device_id}/windows-services", {"name": name, "state": state}
            )
            return dump_json_capped({"services": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def ninjaone_reboot_device(
        device_id: Annotated[int, Field(description="Device identifier.")],
        mode: Annotated[
            str,
            Field(description="Reboot mode: NORMAL (graceful) or FORCED (immediate)."),
        ] = "NORMAL",
        reason: Annotated[str | None, Field(description="Stated reboot reason, for the audit log.")] = None,
    ) -> str:
        """Schedule a device reboot. Destructive: interrupts whatever is running on the device."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body = {"reason": reason} if reason is not None else {}
        try:
            result = await client.post(f"/api/v2/device/{device_id}/reboot/{mode}", json_body=body)
            return dump_json_capped({"success": True, "result": result})
        except NinjaOneError as e:
            return e.to_envelope()
