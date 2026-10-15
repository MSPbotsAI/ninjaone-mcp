"""Callback URL pairs (PRD-19905): store semantics, public /cb routes, and the MCP tools.

No network: the public routes go through Starlette's TestClient, the tools through
FastMCP's in-process call_tool.
"""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from ninjaone_mcp import callbacks
from ninjaone_mcp.__main__ import _build_http_app
from ninjaone_mcp.callbacks import CallbackStore
from ninjaone_mcp.config import Settings
from ninjaone_mcp.server import create_mcp_server

BASE = "https://edge.example/mcp-edge/ninjaone"


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(monkeypatch):
    """A fresh store with a controllable clock, swapped in for the module singleton."""
    clock = FakeClock()
    fresh = CallbackStore(clock=clock)
    fresh.clock = clock
    monkeypatch.setattr(callbacks, "STORE", fresh)
    return fresh


def _app(settings: Settings):
    return _build_http_app(create_mcp_server(settings), settings)


async def _call(settings: Settings, name: str, args: dict) -> dict:
    mcp = create_mcp_server(settings)
    _, structured = await mcp.call_tool(name, args)
    return json.loads(structured["result"])


def _token(url: str) -> str:
    return url.rsplit("/", 1)[1]


# --- store ---------------------------------------------------------------------------

def test_ttl_is_clamped(store):
    assert store.create("x", 5).expires_at == store.clock.t + callbacks.MIN_TTL_SECONDS
    assert store.create("x", 99999).expires_at == store.clock.t + callbacks.MAX_TTL_SECONDS


def test_get_and_post_tokens_are_not_interchangeable(store):
    pair = store.create("payload", 600)
    assert store.get_body(pair.get_token) == "payload"
    assert store.get_body(pair.post_token) is None
    assert store.receive(pair.get_token, b"{}", "application/json") is False
    assert store.status(pair.pair_id)["status"] == "pending"


def test_json_is_received_and_non_json_is_malformed(store):
    ok, bad = store.create("", 600), store.create("", 600)
    assert store.receive(ok.post_token, b'[{"VMName":"a","Health":1}]', "application/json")
    assert store.receive(bad.post_token, b"not json", "text/plain")
    assert store.status(ok.pair_id) == {
        "status": "received", "content_type": "application/json",
        "body": '[{"VMName":"a","Health":1}]'}
    assert store.status(bad.pair_id)["status"] == "malformed"


def test_expiry_then_tombstone_then_unknown(store):
    pair = store.create("payload", 60)
    store.clock.t += 61
    assert store.get_body(pair.get_token) is None
    assert store.receive(pair.post_token, b"{}", None) is False
    assert store.status(pair.pair_id)["status"] == "expired"
    store.clock.t += callbacks.TOMBSTONE_SECONDS
    store.create("other", 600)  # creating purges old tombstones
    assert store.status(pair.pair_id)["status"] == "unknown"


def test_result_that_arrived_in_time_survives_expiry(store):
    pair = store.create("", 60)
    store.receive(pair.post_token, b"{}", "application/json")
    store.clock.t += 61
    assert store.status(pair.pair_id)["status"] == "received"


def test_expire_invalidates_urls(store):
    pair = store.create("payload", 600)
    assert store.expire([pair.pair_id, "nope"]) == {pair.pair_id: "expired", "nope": "unknown"}
    assert store.get_body(pair.get_token) is None


@pytest.mark.asyncio
async def test_wait_returns_when_all_received_and_pairs_do_not_cross(store):
    pairs = [store.create("", 600) for _ in range(10)]

    async def post_all():
        await asyncio.sleep(0.01)
        for i, p in enumerate(pairs):
            store.receive(p.post_token, json.dumps({"host": i}).encode(), "application/json")

    poster = asyncio.create_task(post_all())
    results = await store.wait([p.pair_id for p in pairs], 5)
    await poster
    for i, p in enumerate(pairs):
        assert results[p.pair_id]["status"] == "received"
        assert json.loads(results[p.pair_id]["body"]) == {"host": i}


@pytest.mark.asyncio
async def test_wait_times_out_with_pending(store):
    pair = store.create("", 600)
    results = await store.wait([pair.pair_id, "nope"], 0.05)
    assert results[pair.pair_id]["status"] == "pending"
    assert results["nope"] == {"status": "unknown"}


# --- public routes -------------------------------------------------------------------

def test_routes_round_trip(store):
    pair = store.create("Write-Output hi", 600)
    with TestClient(_app(Settings(callback_public_base_url=BASE))) as client:
        got = client.get(f"/cb/{pair.get_token}")
        assert got.status_code == 200
        assert got.text == "Write-Output hi"
        assert got.headers["content-type"].startswith("text/plain")

        posted = client.post(f"/cb/{pair.post_token}", content=b'{"ok":1}',
                             headers={"content-type": "application/json"})
        assert posted.status_code == 200
        assert store.status(pair.pair_id)["status"] == "received"


def test_routes_hide_everything_that_is_not_live(store):
    pair = store.create("payload", 60)
    with TestClient(_app(Settings(callback_public_base_url=BASE))) as client:
        assert client.get("/cb/unknown").status_code == 404
        assert client.get(f"/cb/{pair.post_token}").status_code == 404   # wrong kind
        assert client.post(f"/cb/{pair.get_token}", content=b"{}").status_code == 404
        store.clock.t += 61
        assert client.get(f"/cb/{pair.get_token}").status_code == 404   # expired


def test_routes_need_no_gateway_credentials(store):
    # GatewayTokenMiddleware guards /mcp only; the caller here is a script on a device.
    pair = store.create("payload", 600)
    with TestClient(_app(Settings(callback_public_base_url=BASE))) as client:
        assert client.get(f"/cb/{pair.get_token}").status_code == 200


def test_routes_absent_when_script_execution_disabled(store):
    pair = store.create("payload", 600)
    settings = Settings(enable_script_execution=False, callback_public_base_url=BASE)
    with TestClient(_app(settings)) as client:
        assert client.get(f"/cb/{pair.get_token}").status_code != 200


# --- tools ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_end_to_end(store):
    settings = Settings(callback_public_base_url=BASE + "/")
    created = await _call(settings, "ninjaone_callback_create_pair",
                          {"body": "payload", "ttl_seconds": 600})
    assert created["get_url"].startswith(BASE + "/cb/")
    assert created["post_url"].startswith(BASE + "/cb/")
    assert created["get_url"] != created["post_url"]

    assert store.get_body(_token(created["get_url"])) == "payload"
    store.receive(_token(created["post_url"]), b'{"Health":3}', "application/json")

    waited = await _call(settings, "ninjaone_callback_wait",
                         {"pair_ids": [created["pair_id"]], "wait_seconds": 1})
    assert waited["results"][0]["status"] == "received"
    assert json.loads(waited["results"][0]["body"]) == {"Health": 3}

    expired = await _call(settings, "ninjaone_callback_expire",
                          {"pair_ids": [created["pair_id"]]})
    assert expired["results"] == [{"pair_id": created["pair_id"], "status": "expired"}]


@pytest.mark.asyncio
async def test_create_pair_without_public_base_url_is_not_configured(store):
    out = await _call(Settings(), "ninjaone_callback_create_pair", {"body": "x"})
    assert out["error"]["code"] == "not_configured"
