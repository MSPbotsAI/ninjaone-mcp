from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import NinjaOneClient, NinjaOneError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], NinjaOneClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_alerts(
        source_type: Annotated[
            str | None, Field(description="Alert source filter, e.g. CONDITION or ACTIVITY.")
        ] = None,
        df: Annotated[
            str | None,
            Field(
                description=(
                    'NinjaOne device filter expression to scope alerts to a device or org, '
                    'e.g. "org = 5" or "id = 123". Same syntax as ninjaone_get_devices\' `df`.'
                )
            ),
        ] = None,
    ) -> str:
        """List active alerts (triggered monitoring conditions) across the instance.

        This endpoint has no pagination — for a single device's alerts, prefer
        ninjaone_get_device_alerts, which is cheaper and doesn't require a `df` filter.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/api/v2/alerts", {"sourceType": source_type, "df": df})
            return dump_json_capped({"alerts": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def ninjaone_reset_alert(
        alert_uid: Annotated[str, Field(description="Alert UID (from ninjaone_get_alerts/ninjaone_get_device_alerts).")],
        activity_note: Annotated[
            str | None,
            Field(description="Optional note recorded on the alert's activity when it's reset."),
        ] = None,
    ) -> str:
        """Reset (dismiss) an alert — acknowledges it and marks it handled."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body = {"note": activity_note} if activity_note is not None else {}
        try:
            result = await client.post(f"/api/v2/alert/{alert_uid}/reset", json_body=body)
            return dump_json_capped({"success": True, "result": result})
        except NinjaOneError as e:
            return e.to_envelope()
