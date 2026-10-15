"""Short-lived callback URL pairs for scripts run on devices (PRD-19905).

NinjaOne's API queues a script and never hands back its output, so a script that has
to report results does it itself: it downloads its payload from a temporary GET URL and
POSTs its JSON result to a temporary POST URL. This module holds those pairs.

The store is in-process memory, deliberately: this makes ninjaone-mcp STATEFUL, the one
exception to the stateless vendor rule. It therefore only works with a single uvicorn
process (__main__ runs exactly one), and a restart or blue/green switch drops the pairs
that are still waiting — the agent then sees them as `unknown` and treats the host as
timed out. Pairs live at most MAX_TTL_SECONDS, so that window is small.

Isolation is by capability: pair ids and both tokens are 256-bit random values, a pair
id is only ever returned to the call that created it, and the public routes only accept
a token, never a pair id.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field

MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 600
# How long an expired pair is remembered, so a late wait reports `expired` rather than
# `unknown` (the two mean different things to an agent: "too late" vs "wrong id").
TOMBSTONE_SECONDS = 600

PENDING = "pending"
RECEIVED = "received"
MALFORMED = "malformed"
EXPIRED = "expired"
UNKNOWN = "unknown"


@dataclass
class Pair:
    pair_id: str
    get_token: str
    post_token: str
    body: str
    expires_at: float
    status: str = PENDING
    received_body: str | None = None
    received_content_type: str | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


class CallbackStore:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._pairs: dict[str, Pair] = {}
        self._by_token: dict[str, tuple[str, str]] = {}  # token -> (pair_id, "get"|"post")

    def _purge(self) -> None:
        now = self._clock()
        for pid in [p for p, pair in self._pairs.items()
                    if now >= pair.expires_at + TOMBSTONE_SECONDS]:
            pair = self._pairs.pop(pid)
            self._by_token.pop(pair.get_token, None)
            self._by_token.pop(pair.post_token, None)

    def create(self, body: str, ttl_seconds: int) -> Pair:
        self._purge()
        ttl = max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, int(ttl_seconds)))
        pair = Pair(
            pair_id=secrets.token_urlsafe(32),
            get_token=secrets.token_urlsafe(32),
            post_token=secrets.token_urlsafe(32),
            body=body,
            expires_at=self._clock() + ttl,
        )
        self._pairs[pair.pair_id] = pair
        self._by_token[pair.get_token] = (pair.pair_id, "get")
        self._by_token[pair.post_token] = (pair.pair_id, "post")
        return pair

    def _live_pair_for(self, token: str, kind: str) -> Pair | None:
        hit = self._by_token.get(token)
        if hit is None or hit[1] != kind:
            return None
        pair = self._pairs.get(hit[0])
        if pair is None or pair.expired(self._clock()):
            return None
        return pair

    def get_body(self, token: str) -> str | None:
        """Payload for a live GET token, else None (unknown, wrong kind or expired)."""
        pair = self._live_pair_for(token, "get")
        return None if pair is None else pair.body

    def receive(self, token: str, raw: bytes, content_type: str | None) -> bool:
        """Record a POST for a live POST token. Returns False when the token is not live.

        A later POST overwrites an earlier one (single-acceptance is out of scope for now).
        """
        pair = self._live_pair_for(token, "post")
        if pair is None:
            return False
        text = raw.decode("utf-8", errors="replace")
        try:
            json.loads(text)
            pair.status = RECEIVED
        except ValueError:
            pair.status = MALFORMED
        pair.received_body = text
        pair.received_content_type = content_type
        pair.done.set()
        return True

    def status(self, pair_id: str) -> dict:
        pair = self._pairs.get(pair_id)
        if pair is None:
            return {"status": UNKNOWN}
        if pair.status in (RECEIVED, MALFORMED):
            # A result that arrived in time stays readable until the tombstone goes.
            return {"status": pair.status, "content_type": pair.received_content_type,
                    "body": pair.received_body}
        if pair.expired(self._clock()):
            return {"status": EXPIRED}
        return {"status": PENDING, "expires_at": int(pair.expires_at)}

    async def wait(self, pair_ids: list[str], wait_seconds: float) -> dict[str, dict]:
        """Wait until every live pending pair has a result, or wait_seconds pass."""
        self._purge()
        now = self._clock()
        waiting = [self._pairs[p].done for p in pair_ids
                   if p in self._pairs and self._pairs[p].status == PENDING
                   and not self._pairs[p].expired(now)]
        if waiting and wait_seconds > 0:
            tasks = [asyncio.create_task(e.wait()) for e in waiting]
            try:
                await asyncio.wait(tasks, timeout=wait_seconds,
                                   return_when=asyncio.ALL_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
        return {p: self.status(p) for p in pair_ids}

    def expire(self, pair_ids: list[str]) -> dict[str, str]:
        now = self._clock()
        out = {}
        for p in pair_ids:
            pair = self._pairs.get(p)
            if pair is None:
                out[p] = UNKNOWN
            else:
                pair.expires_at = min(pair.expires_at, now)
                out[p] = EXPIRED
        return out


STORE = CallbackStore()
