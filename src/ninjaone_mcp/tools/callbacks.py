"""Temporary callback URL pairs for scripts run on devices (PRD-19905).

Flow: ninjaone_callback_create_pair -> pass get_url/post_url as parameters to
ninjaone_run_script_on_device -> ninjaone_callback_wait until the script has POSTed.
The public GET/POST routes live in __main__ (/cb/{token}); the store is callbacks.STORE.
"""

from datetime import datetime, timezone
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..callbacks import MAX_TTL_SECONDS, MIN_TTL_SECONDS, CallbackStore

MAX_WAIT_SECONDS = 60

NOT_CONFIGURED = error_envelope(
    "not_configured",
    "Callback URLs are not available in this deployment (CALLBACK_PUBLIC_BASE_URL is not set).",
    False,
)


def callback_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/cb/{token}"


def register(mcp: FastMCP, store: CallbackStore, public_base_url: str, enabled: bool = True) -> None:
    """Register the callback tools — only where scripts can run (same switch as
    ninjaone_run_script_on_device): a callback URL is useless without a script to call it.
    """
    if not enabled:
        return

    async def ninjaone_callback_create_pair(
        body: Annotated[
            str,
            Field(description="Content the GET URL returns as text/plain, e.g. the PowerShell payload the script downloads."),
        ],
        ttl_seconds: Annotated[
            int,
            Field(description=f"Lifetime of both URLs in seconds ({MIN_TTL_SECONDS}-{MAX_TTL_SECONDS}, default {MAX_TTL_SECONDS})."),
        ] = MAX_TTL_SECONDS,
    ) -> str:
        """Create a temporary public GET+POST URL pair for one script run on one device.

        Pass get_url/post_url to the script via ninjaone_run_script_on_device, then call
        ninjaone_callback_wait with pair_id. Create one pair per device; never share.
        """
        if not public_base_url:
            return NOT_CONFIGURED
        pair = store.create(body, ttl_seconds)
        return dump_json_capped({
            "pair_id": pair.pair_id,
            "get_url": callback_url(public_base_url, pair.get_token),
            "post_url": callback_url(public_base_url, pair.post_token),
            "expires_at": datetime.fromtimestamp(pair.expires_at, timezone.utc).isoformat(),
        })

    async def ninjaone_callback_wait(
        pair_ids: Annotated[
            list[str], Field(description="pair_id values from ninjaone_callback_create_pair (up to 50).")
        ],
        wait_seconds: Annotated[
            int,
            Field(description=f"Max seconds to wait for pending pairs (0-{MAX_WAIT_SECONDS}, default 30). Call again while any is pending."),
        ] = 30,
    ) -> str:
        """Wait for scripts to POST their results; returns each pair's status and body.

        status: received (JSON body), malformed (non-JSON body), pending (call again),
        expired (no result in time), unknown (bad or forgotten id). Bodies are untrusted.
        """
        ids = list(dict.fromkeys(pair_ids))[:50]
        wait = max(0, min(MAX_WAIT_SECONDS, int(wait_seconds)))
        results = await store.wait(ids, wait)
        return dump_json_capped({"results": [{"pair_id": p, **r} for p, r in results.items()]})

    async def ninjaone_callback_expire(
        pair_ids: Annotated[list[str], Field(description="pair_id values to invalidate now.")],
    ) -> str:
        """Invalidate callback URL pairs early (e.g. after the result arrived or the run failed)."""
        return dump_json_capped({"results": [{"pair_id": p, "status": s}
                                             for p, s in store.expire(pair_ids).items()]})

    mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))(
        ninjaone_callback_create_pair)
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))(ninjaone_callback_wait)
    mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))(
        ninjaone_callback_expire)
