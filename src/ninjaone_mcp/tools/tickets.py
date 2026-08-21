from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import NinjaOneClient, NinjaOneError
from ._common import NO_TOKEN, _MAX_PAGE_SIZE


def _extract_tickets(response: Any) -> list[dict]:
    """NinjaOne's board-run response isn't formally documented in its own
    OpenAPI spec — depending on tenant/API version it can arrive as a bare
    array, {"tickets": [...]}, or {"data": [...], "metadata": {...}}. Accept
    all three rather than assume one shape.
    """
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        if isinstance(response.get("tickets"), list):
            return response["tickets"]
        if isinstance(response.get("data"), list):
            return response["data"]
    return []


def _status_label(ticket: dict) -> str:
    status = ticket.get("status")
    if isinstance(status, str):
        return status
    if isinstance(status, dict):
        return str(status.get("displayName") or status.get("name") or "")
    return ""


def _ticket_matches(ticket: dict, status: str | None, organization_id: int | None, device_id: int | None) -> bool:
    if status is not None and _status_label(ticket).upper() != status.upper():
        return False
    if organization_id is not None and ticket.get("clientId") != organization_id:
        return False
    if device_id is not None and ticket.get("nodeId") != device_id:
        return False
    return True


def register(mcp: FastMCP, client_factory: Callable[[], NinjaOneClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_ticket_boards() -> str:
        """List the tenant's ticket boards. Use this to discover board_id values for ninjaone_get_tickets."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/api/v2/ticketing/trigger/boards")
            return dump_json_capped({"boards": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_tickets(
        board_id: Annotated[
            int,
            Field(
                description=(
                    "Ticket board to query — board ids vary by tenant, board 1 is NOT "
                    "always the default board. Discover ids with ninjaone_get_ticket_boards."
                )
            ),
        ],
        status: Annotated[
            str | None,
            Field(description="Filter by status: NEW, OPEN, WAITING, PAUSED, RESOLVED, or CLOSED."),
        ] = None,
        organization_id: Annotated[int | None, Field(description="Filter by organization.")] = None,
        device_id: Annotated[int | None, Field(description="Filter by linked device.")] = None,
        limit: Annotated[
            int | None, Field(description="Board page size (default 50, max 200).")
        ] = None,
        cursor: Annotated[
            int | None, Field(description="Pagination cursor from a previous response's cursor field.")
        ] = None,
    ) -> str:
        """List tickets from one board, filterable by status/organization/device.

        Sending server-side filters to NinjaOne's board-run endpoint has
        been reported to 400 in practice, so this tool filters client-side
        instead: `count` (matches) is separate from `scanned` (rows
        fetched) — page with `cursor` until `hasMore` is false.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_size = min(limit or 50, _MAX_PAGE_SIZE)
        body = {"pageSize": page_size}
        if cursor is not None:
            body["lastCursorId"] = cursor
        try:
            response = await client.post(f"/api/v2/ticketing/trigger/board/{board_id}/run", json_body=body)
        except NinjaOneError as e:
            return e.to_envelope()

        rows = _extract_tickets(response)
        matched = [t for t in rows if _ticket_matches(t, status, organization_id, device_id)]
        has_more = len(rows) >= page_size
        next_cursor = max((t.get("id") for t in rows if isinstance(t.get("id"), int)), default=None) if has_more else None
        return dump_json_capped(
            {
                "tickets": matched,
                "count": len(matched),
                "scanned": len(rows),
                "hasMore": has_more,
                "cursor": next_cursor,
            }
        )

    @mcp.tool()
    async def ninjaone_create_ticket(
        summary: Annotated[str, Field(description="Ticket summary/subject (max 200 chars).")],
        organization_id: Annotated[int, Field(description="Organization this ticket belongs to.")],
        description: Annotated[str | None, Field(description="Ticket description body text.")] = None,
        device_id: Annotated[int | None, Field(description="Device this ticket is about.")] = None,
        location_id: Annotated[int | None, Field(description="Organization location.")] = None,
        ticket_form_id: Annotated[int | None, Field(description="Ticket form template to use.")] = None,
        status: Annotated[
            str | None, Field(description="Initial status: NEW, OPEN, WAITING, PAUSED, RESOLVED, or CLOSED.")
        ] = None,
        priority: Annotated[str | None, Field(description="LOW, MEDIUM, or HIGH.")] = None,
        severity: Annotated[str | None, Field(description="MINOR, MODERATE, MAJOR, or CRITICAL.")] = None,
        type: Annotated[str | None, Field(description="PROBLEM, QUESTION, INCIDENT, or TASK.")] = None,
    ) -> str:
        """Create a new service-desk ticket."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body: dict = {"summary": summary, "clientId": organization_id}
        if description is not None:
            body["description"] = {"public": True, "body": description}
        if device_id is not None:
            body["nodeId"] = device_id
        if location_id is not None:
            body["locationId"] = location_id
        if ticket_form_id is not None:
            body["ticketFormId"] = ticket_form_id
        if status is not None:
            body["status"] = status
        if priority is not None:
            body["priority"] = priority
        if severity is not None:
            body["severity"] = severity
        if type is not None:
            body["type"] = type
        try:
            result = await client.post("/api/v2/ticketing/ticket", json_body=body)
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def ninjaone_update_ticket(
        ticket_id: Annotated[int, Field(description="Ticket to update.")],
        summary: Annotated[str | None, Field(description="New summary/subject.")] = None,
        status: Annotated[
            str | None, Field(description="New status: NEW, OPEN, WAITING, PAUSED, RESOLVED, or CLOSED.")
        ] = None,
        priority: Annotated[str | None, Field(description="LOW, MEDIUM, or HIGH.")] = None,
        assignee_id: Annotated[int | None, Field(description="App user id to assign the ticket to.")] = None,
        comment: Annotated[
            str | None,
            Field(description="Comment text to add alongside this update (NinjaOne bundles comments into the same update call)."),
        ] = None,
        comment_public: Annotated[
            bool | None, Field(description="Whether the added comment is customer-visible (default true).")
        ] = None,
    ) -> str:
        """Update an existing ticket's fields and/or add a comment in the same call."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        ticket_body: dict = {}
        if summary is not None:
            ticket_body["summary"] = summary
        if status is not None:
            ticket_body["status"] = status
        if priority is not None:
            ticket_body["priority"] = priority
        if assignee_id is not None:
            ticket_body["assignedAppUserId"] = assignee_id
        body: dict = {"ticket": ticket_body} if ticket_body else {}
        if comment is not None:
            body["comment"] = {"public": comment_public if comment_public is not None else True, "body": comment}
        try:
            result = await client.put(f"/api/v2/ticketing/ticket/{ticket_id}", json_body=body)
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_ticket_log_entries(
        ticket_id: Annotated[int, Field(description="Ticket identifier.")],
        entry_type: Annotated[
            str | None,
            Field(description="Filter by entry type: DESCRIPTION, COMMENT, CONDITION, SAVE, or DELETE."),
        ] = None,
    ) -> str:
        """Get a ticket's log entries — its description, comments, and change history."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                f"/api/v2/ticketing/ticket/{ticket_id}/log-entry", {"type": entry_type}
            )
            return dump_json_capped({"entries": result})
        except NinjaOneError as e:
            return e.to_envelope()
