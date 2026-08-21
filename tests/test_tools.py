"""tools/list snapshot + error-envelope mapping tests.

No network calls: tool enumeration goes through FastMCP's in-process
list_tools(), and the error-code mapping is tested directly against
NinjaOneError, independent of any real HTTP request.
"""

import pytest

from ninjaone_mcp.api_client import NinjaOneClient, NinjaOneError, normalize_scopes
from ninjaone_mcp.config import Settings
from ninjaone_mcp.server import create_mcp_server

EXPECTED_TOOLS = {
    # organizations
    "ninjaone_get_organizations": set(),
    "ninjaone_get_organization": {"organization_id"},
    "ninjaone_create_organization": {"name"},
    "ninjaone_get_organization_locations": {"organization_id"},
    "ninjaone_get_organization_devices": {"organization_id"},
    # devices
    "ninjaone_get_devices": set(),
    "ninjaone_get_device": {"device_id"},
    "ninjaone_get_device_alerts": {"device_id"},
    "ninjaone_get_device_activities": {"device_id"},
    "ninjaone_get_device_services": {"device_id"},
    "ninjaone_reboot_device": {"device_id"},
    # alerts
    "ninjaone_get_alerts": set(),
    "ninjaone_reset_alert": {"alert_uid"},
    # tickets
    "ninjaone_get_ticket_boards": set(),
    "ninjaone_get_tickets": {"board_id"},
    "ninjaone_create_ticket": {"summary", "organization_id"},
    "ninjaone_update_ticket": {"ticket_id"},
    "ninjaone_get_ticket_log_entries": {"ticket_id"},
    # automation / scripting / jobs
    "ninjaone_get_automation_scripts": set(),
    "ninjaone_get_device_scripting_options": {"device_id"},
    "ninjaone_run_script_on_device": {"device_id", "type"},
    "ninjaone_get_active_jobs": set(),
    "ninjaone_get_device_active_jobs": {"device_id"},
}

READ_ONLY_TOOLS = {
    "ninjaone_get_organizations",
    "ninjaone_get_organization",
    "ninjaone_get_organization_locations",
    "ninjaone_get_organization_devices",
    "ninjaone_get_devices",
    "ninjaone_get_device",
    "ninjaone_get_device_alerts",
    "ninjaone_get_device_activities",
    "ninjaone_get_device_services",
    "ninjaone_get_alerts",
    "ninjaone_get_ticket_boards",
    "ninjaone_get_tickets",
    "ninjaone_get_ticket_log_entries",
    "ninjaone_get_automation_scripts",
    "ninjaone_get_device_scripting_options",
    "ninjaone_get_active_jobs",
    "ninjaone_get_device_active_jobs",
}

DESTRUCTIVE_TOOLS = {"ninjaone_reboot_device", "ninjaone_reset_alert", "ninjaone_run_script_on_device"}


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"

    by_name = {t.name: t for t in tools}
    for name, expected_required in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"
        assert len(tool.description or "") <= 500, f"{name}: description too long"
        first_line = (tool.description or "").strip().splitlines()[0]
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"

        if name in READ_ONLY_TOOLS:
            assert tool.annotations is not None and tool.annotations.readOnlyHint is True, name
        if name in DESTRUCTIVE_TOOLS:
            assert tool.annotations is not None and tool.annotations.destructiveHint is True, name


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.parametrize(
    "status_code,expected_code,expected_retryable",
    [
        (0, "upstream_error", True),
        (400, "invalid_argument", False),
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_error_envelope_mapping(status_code, expected_code, expected_retryable):
    import json

    err = NinjaOneError(status_code, "boom")
    envelope = json.loads(err.to_envelope())
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["retryable"] is expected_retryable
    assert envelope["error"]["message"] == "boom"


def test_default_scope_excludes_control():
    # Regression test: a client that doesn't pass X-Ninja-Scopes must never
    # be sent to NinjaOne asking for `control` — many API Services apps are
    # never granted it, and NinjaOne 400s the whole token request rather
    # than narrowing the grant. See server.py's GatewayTokenMiddleware
    # docstring and api_client.py's _DEFAULT_SCOPES comment.
    client = NinjaOneClient("id", "secret", "https://app.ninjarmm.com")
    assert client._scopes == "monitoring management"
    assert "control" not in client._scopes


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("monitoring", "monitoring"),
        ("monitoring,management", "monitoring management"),
        ("MONITORING  Control", "monitoring control"),
        ("monitoring,bogus,control", "monitoring control"),
        ("bogus", None),
    ],
)
def test_normalize_scopes(raw, expected):
    assert normalize_scopes(raw) == expected
