"""PRD-19905 Gap3: ninjaone_get_organization_custom_fields.

Covers TC-CB-004/005 (AC4/AC5). mocked NinjaOneClient, no real NinjaOne API
calls (per design doc's test strategy) — the upstream endpoint path itself
(/api/v2/organization/{id}/custom-fields) is unverified against a live
tenant, flagged in the design doc's Open Questions as non-blocking; these
tests pin down this server's own behavior given whatever that endpoint
returns, not the endpoint's real shape.
"""

import pytest

from conftest import call_tool, mocked_client


@pytest.mark.asyncio
async def test_custom_fields_returns_configured_value():
    # TC-CB-004 / AC4: a configured field's name and value must be readable.
    with mocked_client(get_result={"ninja_run_as_user": "CONTOSO\\svc-rewst"}):
        result = await call_tool("ninjaone_get_organization_custom_fields", {"organization_id": 5})
    assert result["fields"]["ninja_run_as_user"] == "CONTOSO\\svc-rewst"
    assert "note" not in result


@pytest.mark.asyncio
async def test_custom_fields_absent_is_explicit_not_silent():
    # TC-CB-005 / AC5: an org with zero configured custom fields must come
    # back with an explicit marker, not an empty/ambiguous response that a
    # caller could mistake for "the call didn't really happen".
    with mocked_client(get_result={}):
        result = await call_tool("ninjaone_get_organization_custom_fields", {"organization_id": 7})
    assert result["fields"] == {}
    assert result["note"] == "no custom fields configured for this organization"


@pytest.mark.asyncio
async def test_custom_fields_with_other_fields_but_not_the_one_asked_about():
    # Dev-added (source: dev, not in the confirmed ac.json): the org has other
    # custom fields configured, just not ninja_run_as_user specifically — the
    # caller must be able to tell this apart from "no fields at all" by
    # checking for the key, same mechanism as the all-absent case, just
    # without the explicit note (the dict itself isn't empty).
    with mocked_client(get_result={"some_other_field": "value"}):
        result = await call_tool("ninjaone_get_organization_custom_fields", {"organization_id": 9})
    assert "ninja_run_as_user" not in result["fields"]
    assert result["fields"]["some_other_field"] == "value"
