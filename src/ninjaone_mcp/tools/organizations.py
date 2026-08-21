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
    async def ninjaone_get_organizations(
        limit: Annotated[
            int | None, Field(description="Max organizations to return (default 50, max 200).")
        ] = None,
        after: Annotated[
            int | None,
            Field(description="Pagination cursor: the last organization id from a previous page."),
        ] = None,
    ) -> str:
        """List organizations (customer accounts) in this NinjaOne instance.

        A full page (limit reached) means there may be more — pass the
        highest `id` seen as `after` to fetch the next page.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_size = min(limit or 50, _MAX_PAGE_SIZE)
        try:
            result = await client.get("/api/v2/organizations", {"pageSize": page_size, "after": after})
            return dump_json_capped({"organizations": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_organization(
        organization_id: Annotated[int, Field(description="Organization identifier.")],
    ) -> str:
        """Get a single organization's details by id."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/organization/{organization_id}")
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool()
    async def ninjaone_create_organization(
        name: Annotated[str, Field(description="Organization full name.")],
        description: Annotated[str | None, Field(description="Organization description.")] = None,
        node_approval_mode: Annotated[
            str | None,
            Field(
                description=(
                    "How new device registrations under this org are handled: "
                    "AUTOMATIC, MANUAL, or REJECT."
                )
            ),
        ] = None,
        tags: Annotated[list[str] | None, Field(description="Tags to attach to the organization.")] = None,
        template_organization_id: Annotated[
            int | None,
            Field(description="Existing organization to copy settings/policies from."),
        ] = None,
    ) -> str:
        """Create a new organization (customer account)."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body = {
            "name": name,
            "description": description,
            "nodeApprovalMode": node_approval_mode,
            "tags": tags,
        }
        body = {k: v for k, v in body.items() if v is not None}
        try:
            result = await client.post(
                "/api/v2/organizations",
                json_body=body,
                params={"templateOrganizationId": template_organization_id},
            )
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_organization_locations(
        organization_id: Annotated[int, Field(description="Organization identifier.")],
    ) -> str:
        """List an organization's locations (sites)."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/organization/{organization_id}/locations")
            return dump_json_capped({"locations": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_organization_devices(
        organization_id: Annotated[int, Field(description="Organization identifier.")],
        limit: Annotated[
            int | None, Field(description="Max devices to return (default 50, max 200).")
        ] = None,
        after: Annotated[
            int | None, Field(description="Pagination cursor: the last device id from a previous page.")
        ] = None,
    ) -> str:
        """List the devices belonging to one organization.

        Scopes by org through the URL path (unlike ninjaone_get_devices'
        `df` filter, which NinjaOne can silently drop on some tenants).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        page_size = min(limit or 50, _MAX_PAGE_SIZE)
        try:
            result = await client.get(
                f"/api/v2/organization/{organization_id}/devices",
                {"pageSize": page_size, "after": after},
            )
            return dump_json_capped({"devices": result})
        except NinjaOneError as e:
            return e.to_envelope()
