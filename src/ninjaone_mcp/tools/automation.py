from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import NinjaOneClient, NinjaOneError
from ._common import NO_TOKEN, NO_USER_TOKEN

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


def register(
    mcp: FastMCP,
    client_factory: Callable[[], NinjaOneClient | None],
    user_client_factory: Callable[[], NinjaOneClient | None],
) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_automation_scripts() -> str:
        """List the automation scripts available in this NinjaOne instance (id, name, language, ...)."""
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/api/v2/automation/scripts")
            return dump_json_capped({"scripts": result})
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def ninjaone_get_device_scripting_options(
        device_id: Annotated[int, Field(description="Device identifier.")],
    ) -> str:
        """Get what's runnable on a device: available scripts, built-in actions, and run-as credential options.

        Call this before ninjaone_run_script_on_device to confirm a script/action
        id and runAs value are actually valid for that specific device.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/device/{device_id}/scripting/options")
            return dump_json_capped(result)
        except NinjaOneError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
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

        Needs the X-Ninja-User-Client-Id/-Secret/X-Ninja-Refresh-Token
        headers — NinjaOne rejects script execution from a machine identity
        regardless of scope, since it ties the action to a real user.
        Queues a job — check ninjaone_get_device_active_jobs afterward.
        """
        client = user_client_factory()
        if client is None:
            return NO_USER_TOKEN
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
