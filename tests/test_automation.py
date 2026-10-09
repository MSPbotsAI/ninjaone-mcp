"""PRD-19905 Gap2: name_filter / pagination on the automation-scripts tools.

Covers TC-CB-001/002/003 (AC1/AC2/AC3). Root cause: ninjaone_get_automation_
scripts had no way to find one script by name once the instance has enough
scripts to hit dump_json_capped's 20,000-char truncation — the target script
could be truncated away before the caller ever saw it. mocked NinjaOneClient,
no real NinjaOne API calls (per design doc's test strategy).
"""

import pytest

from conftest import call_tool, mocked_client


def _scripts(n: int) -> list[dict]:
    # Long enough names/payload that n=600 clears the 20,000-char cap, matching
    # the ticket's real-world trigger (489 scripts, target script absent from
    # the truncated response).
    return [
        {
            "id": i,
            "name": f"Script Number {i:04d} - routine maintenance task",
            "language": "POWERSHELL",
            "description": "placeholder description padding out the payload size",
        }
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_name_filter_finds_script_past_truncation_threshold():
    # TC-CB-001 / AC1: target script sits past where truncation would cut the
    # unfiltered list off — name_filter must still surface it.
    scripts = _scripts(600)
    target = {"id": 9999, "name": "Rewst (Windows)", "language": "POWERSHELL"}
    scripts.insert(550, target)
    with mocked_client(get_result=scripts):
        result = await call_tool("ninjaone_get_automation_scripts", {"name_filter": "Rewst (Windows)"})
    assert result.get("truncated") is not True
    assert [s["id"] for s in result["scripts"]] == [9999]
    assert result["scripts"][0]["name"] == "Rewst (Windows)"


@pytest.mark.asyncio
async def test_name_filter_is_case_insensitive_substring():
    scripts = [{"id": 1, "name": "Rewst (Windows)"}, {"id": 2, "name": "Other Script"}]
    with mocked_client(get_result=scripts):
        result = await call_tool("ninjaone_get_automation_scripts", {"name_filter": "rewst"})
    assert [s["id"] for s in result["scripts"]] == [1]


@pytest.mark.asyncio
async def test_no_filter_preserves_existing_truncation_behavior():
    # TC-CB-002 / AC2: zero args must be byte-identical to the pre-change call —
    # same upstream call (no params), same truncation markers when the raw
    # response is still oversized.
    scripts = _scripts(600)
    with mocked_client(get_result=scripts) as mock_get:
        result = await call_tool("ninjaone_get_automation_scripts", {})
    mock_get.assert_awaited_once_with("/api/v2/automation/scripts", None)
    assert result.get("truncated") is True
    assert result.get("truncated_field") == "scripts"
    assert result.get("original_count") == 600


@pytest.mark.asyncio
async def test_limit_and_after_are_forwarded_as_server_side_hints():
    # Design: "server-side first" — when the caller supplies limit/after, they
    # must reach the upstream call as pageSize/after (best-effort; client-side
    # name_filter still applies on whatever comes back, see test above for the
    # "no args" byte-identical guarantee this must NOT break).
    with mocked_client(get_result=[]) as mock_get:
        await call_tool("ninjaone_get_automation_scripts", {"limit": 50, "after": 10})
    mock_get.assert_awaited_once_with("/api/v2/automation/scripts", {"pageSize": 50, "after": 10})


@pytest.mark.asyncio
async def test_device_scripting_options_name_filter():
    # TC-CB-003 / AC3: response has multiple named lists (scripts, actions);
    # only entries matching name_filter should remain, in every such list.
    response = {
        "scripts": [{"id": 1, "name": "Rewst (Windows)"}, {"id": 2, "name": "Disk Cleanup"}],
        "actions": [{"uid": "a1", "name": "Reboot"}],
    }
    with mocked_client(get_result=response):
        result = await call_tool(
            "ninjaone_get_device_scripting_options", {"device_id": 42, "name_filter": "Rewst"}
        )
    assert [s["id"] for s in result["scripts"]] == [1]
    assert result["actions"] == []


@pytest.mark.asyncio
async def test_device_scripting_options_no_filter_is_unchanged():
    response = {"scripts": [{"id": 1, "name": "A"}], "actions": [{"uid": "a1", "name": "B"}]}
    with mocked_client(get_result=response):
        result = await call_tool("ninjaone_get_device_scripting_options", {"device_id": 42})
    assert result == response
