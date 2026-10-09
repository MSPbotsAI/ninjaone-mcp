"""Shared test helpers.

PRD-19905: Gap2/Gap3 tests need to call a registered FastMCP tool with a
mocked NinjaOneClient and get back the parsed JSON the tool function
returned. There was no existing helper for that (test_tools.py only does
schema-snapshot assertions, test_middleware.py only exercises the ASGI
layer) — this is new infrastructure, not a copy of something already here.
"""

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from ninjaone_mcp.api_client import NinjaOneClient
from ninjaone_mcp.config import Settings
from ninjaone_mcp.server import _GatewayCreds, _gateway_creds_var, create_mcp_server


@contextmanager
def mocked_client(get_result: Any = None, get_side_effect: Any = None):
    """Make get_client_from_context() resolve to a real NinjaOneClient whose
    .get() is mocked, for the duration of the with-block — mirrors how a
    request arrives in production (gateway sets the contextvar, server.py's
    client_factory resolves a client from it), just with the HTTP call
    replaced by a canned value.
    """
    token = _gateway_creds_var.set(_GatewayCreds("test-token", "us", ""))
    try:
        mock_get = AsyncMock(return_value=get_result, side_effect=get_side_effect)
        with patch.object(NinjaOneClient, "get", new=mock_get):
            yield mock_get
    finally:
        _gateway_creds_var.reset(token)


async def call_tool(tool_name: str, arguments: dict) -> dict:
    """Call a tool through the real FastMCP server and parse its JSON string
    result back into a dict/list — tools always return dump_json_capped(...)
    output, never structured content, so parsing is required to assert on it.
    """
    mcp = create_mcp_server(Settings())
    _, structured = await mcp.call_tool(tool_name, arguments)
    return json.loads(structured["result"])
