from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import NinjaOneClient, NinjaOneError
from ._common import NO_TOKEN, _MAX_PAGE_SIZE

# These 5 endpoints were pulled from NinjaOne's official OpenAPI 3.0.1 spec
# (per the request that specified this module): GET /v2/automation/scripts
# (getAutomationScripts), GET /v2/device/{id}/scripting/options
# (requestScriptingOptions), POST /v2/device/{id}/script/run
# (runScriptOnDevice), GET /v2/jobs (getActiveJobs), GET /v2/device/{id}/jobs
# (getDeviceActiveJobs). The other 4 of the 5 were independently
# cross-checked against a separately obtained copy of NinjaOne's public API
# spec, which encodes them as /api/v2/... (server root with no /api, path
# carrying it) rather than /v2/...-off-an-/api-rooted-server — same absolute
# URL, different split. getAutomationScripts wasn't present in that
# secondary copy (it's newer than that spec revision), so its exact /api
# placement is inferred from the other 4's confirmed pattern, not verified
# against a live spec — flagging this rather than presenting it as equally
# certain.

# PRD-19905 (Gap2): whether GET /api/v2/automation/scripts accepts server-side
# pageSize/after is unverified against a live NinjaOne tenant (ticket design,
# Open Questions: "server-side first, client-side fallback"). To keep AC2's
# "no args => byte-identical to before" guarantee regardless of upstream
# support, pageSize/after are ONLY sent when the caller passes limit/after —
# the default no-args call stays exactly the old bare GET. name_filter is
# always applied client-side after the fetch, so filtering correctness never
# depends on whether the server honored the paging hint.
def _filter_by_name(items: list, name_filter: str | None) -> list:
    if not name_filter:
        return items
    needle = name_filter.lower()
    return [item for item in items if needle in str(item.get("name", "")).lower()]


# PRD-19905 (Gap2): the exact response shape of GET /device/{id}/scripting/
# options (which top-level keys hold the runnable scripts vs. built-in
# actions) is not pinned down anywhere in this repo or its README — the
# design doc flags this as "field name to be confirmed against the actual
# response shape at implementation time". Rather than guess a specific key
# name and risk silently filtering nothing on a live tenant, this filters
# every top-level list-of-dicts field whose items carry a "name" key. That
# makes the filter correct for "scripts"/"actions" (the two groups the
# tool's own docstring promises) under any of the shapes NinjaOne has been
# seen to use for similar endpoints, without hard-coding an unverified key.
def _filter_named_lists(data: dict, name_filter: str | None) -> dict:
    if not name_filter:
        return data
    filtered = dict(data)
    for key, value in data.items():
        if isinstance(value, list) and value and all(
            isinstance(item, dict) and "name" in item for item in value
        ):
            filtered[key] = _filter_by_name(value, name_filter)
    return filtered


def register(
    mcp: FastMCP,
    client_factory: Callable[[], NinjaOneClient | None],
    include_run_script: bool = True,
) -> None:
    """Register the automation tools.

    include_run_script=False omits ninjaone_run_script_on_device and registers only the
    four read-only tools here. See config.Settings.enable_script_execution for why the
    machine-identity deployment drops it.
    """

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_automation_scripts(
        name_filter: Annotated[
            str | None,
            Field(description="Case-insensitive substring match on script name, e.g. \"Rewst (Windows)\". Applied after fetch, so it finds scripts that would otherwise be cut off by the response size cap."),
        ] = None,
        limit: Annotated[
            int | None, Field(description="Server-side page-size hint (max 200). Omit for the default, unpaginated call.")
        ] = None,
        after: Annotated[
            int | None, Field(description="Pagination cursor: the last script id from a previous page.")
        ] = None,
    ) -> str:
        """List the automation scripts available in this NinjaOne instance (id, name, language, ...).

        Pass name_filter to find a specific script by (sub)name without being cut off
        by truncation when the instance has hundreds of scripts — omit all three args
        for the original unfiltered/unpaginated behavior.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            params = None
            if limit is not None or after is not None:
                params = {"pageSize": min(limit, _MAX_PAGE_SIZE) if limit is not None else None, "after": after}
            result = await client.get("/api/v2/automation/scripts", params)
            result = _filter_by_name(result, name_filter)
            return dump_json_capped({"scripts": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device_scripting_options(
        device_id: Annotated[int, Field(description="Device identifier.")],
        name_filter: Annotated[
            str | None,
            Field(description="Case-insensitive substring match on script/action name. Applied after fetch to every named list in the response (scripts, actions)."),
        ] = None,
    ) -> str:
        """Get what's runnable on a device: available scripts, built-in actions, and run-as credential options.

        Call this before ninjaone_run_script_on_device to confirm a script/action
        id and runAs value are actually valid for that specific device. Pass
        name_filter to narrow a long list down to one candidate by name.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/device/{device_id}/scripting/options")
            result = _filter_named_lists(result, name_filter)
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    # Defined unconditionally, but registered as a tool only when include_run_script is
    # set — see the conditional mcp.tool() call right after the body.
    async def ninjaone_run_script_on_device(
        device_id: Annotated[int, Field(description="Device to run the script/action on.")],
        type: Annotated[str, Field(description="What to run: SCRIPT (a custom script) or ACTION (a built-in action).")],
        script_id: Annotated[
            int | None, Field(description="Script identifier — required when type=SCRIPT.")
        ] = None,
        action_uid: Annotated[
            str | None, Field(description="Built-in action identifier (UUID) — required when type=ACTION.")
        ] = None,
        parameters: Annotated[str | None, Field(description="Parameters string passed to the script/action.")] = None,
        run_as: Annotated[
            str | None,
            Field(description="Credential role/identifier to run as (from ninjaone_get_device_scripting_options)."),
        ] = None,
    ) -> str:
        """Run a script or built-in action on a device. Destructive: executes real code on the device.

        Queues a job rather than running synchronously — use ninjaone_get_
        device_active_jobs or ninjaone_get_active_jobs afterward to check
        progress/completion.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body: dict = {"type": type}
        if script_id is not None:
            body["id"] = script_id
        if action_uid is not None:
            body["uid"] = action_uid
        if parameters is not None:
            body["parameters"] = parameters
        if run_as is not None:
            body["runAs"] = run_as
        try:
            result = await client.post(f"/api/v2/device/{device_id}/script/run", json_body=body)
            return dump_json_capped({"success": True, "result": result})
        except NinjaOneError as e:
            return e.to_envelope()

    if include_run_script:
        mcp.tool(annotations=ToolAnnotations(destructiveHint=True))(
            ninjaone_run_script_on_device)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_active_jobs(
        job_type: Annotated[str | None, Field(description="Filter by job type.")] = None,
        df: Annotated[
            str | None,
            Field(description='NinjaOne device filter expression to scope jobs to a device or org, e.g. "org = 5".'),
        ] = None,
    ) -> str:
        """List currently active (running/queued) jobs across the instance, e.g. scripts in progress."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/api/v2/jobs", {"jobType": job_type, "df": df})
            return dump_json_capped({"jobs": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device_active_jobs(
        device_id: Annotated[int, Field(description="Device identifier.")],
    ) -> str:
        """List a specific device's currently active (running/queued) jobs."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/device/{device_id}/jobs")
            return dump_json_capped({"jobs": result})
        except NinjaOneError as e:
            return e.to_envelope()
