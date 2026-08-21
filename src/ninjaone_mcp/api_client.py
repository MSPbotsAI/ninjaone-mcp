import asyncio
from typing import Any

import httpx

from ._json import error_envelope

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0
_VALID_SCOPES = {"monitoring", "management", "control"}
# Matches the community wyre-technology/ninjaone-mcp SDK's own default grant
# request — NOT "control", which many API Services apps are never granted
# (it's remote-access). Asking for a scope the app lacks 400s at the token
# endpoint rather than narrowing the grant, so defaulting to the two nearly
# every app has is safer than asking for everything and letting most callers
# fail. A caller whose app also has `control` (or only has one of the two)
# must say so explicitly via X-Ninja-Scopes.
_DEFAULT_SCOPES = "monitoring management"


def normalize_scopes(raw: str | None) -> str | None:
    """Parse a space/comma-separated scope string, dropping anything not in
    monitoring/management/control. Returns None for "not specified" (blank,
    or nothing recognized) so the caller falls back to _DEFAULT_SCOPES,
    rather than ever sending an empty `scope=` to NinjaOne.
    """
    if not raw or not raw.strip():
        return None
    tokens = [t.lower() for t in raw.replace(",", " ").split() if t]
    valid = [t for t in tokens if t in _VALID_SCOPES]
    return " ".join(valid) or None


# One shared connection pool for the process lifetime. No credentials are
# ever stored on it — client_id/client_secret are passed per-request (see
# server.py's contextvar-based credential isolation), so sharing the pool
# across tenants/requests is safe: it holds only TCP connections, never a
# request's auth material.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    return _http_client


# status_code -> (error code, retryable). status_code 0 means a network/
# connection-level failure (no response at all).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def _classify(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    return "invalid_argument", False


class NinjaOneError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"NinjaOne API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = _classify(self.status_code)
        return error_envelope(code, self.message, retryable)


class NinjaOneClient:
    """Async httpx client wrapping the NinjaOne Public API v2.

    NinjaOne authenticates via OAuth2 client_credentials (POST /oauth/token
    with client_id/client_secret/scope, returning a short-lived bearer
    token) rather than a single long-lived API key. This client exchanges
    a fresh token once per logical request (reused only across that one
    request's own retry attempts) and never caches a token across separate
    tool calls — the client_id/client_secret arrive per-request from the
    gateway header (see server.py) and are never persisted, so there is no
    module-level place a token could leak between tenants either.
    """

    def __init__(self, client_id: str, client_secret: str, base_url: str, scopes: str | None = None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._scopes = normalize_scopes(scopes) or _DEFAULT_SCOPES

    async def _get_access_token(self) -> str:
        client = _get_http_client()
        try:
            resp = await client.post(
                f"{self._base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scopes,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as e:
            raise NinjaOneError(0, f"Token request failed: {e or type(e).__name__}") from e
        if resp.status_code >= 400:
            raise NinjaOneError(resp.status_code, f"Token request rejected: {self._extract_error(resp)}")
        try:
            body = resp.json()
        except ValueError as e:
            raise NinjaOneError(0, "Token response was not valid JSON") from e
        token = body.get("access_token")
        if not token:
            raise NinjaOneError(401, "Token response missing access_token")
        return token

    def _extract_error(self, resp: httpx.Response) -> str:
        try:
            detail = resp.json()
            if isinstance(detail, dict):
                return (
                    detail.get("message")
                    or detail.get("error_description")
                    or detail.get("error")
                    or str(detail)
                )
            return str(detail)
        except ValueError:
            return resp.text

    def _clean_params(self, params: dict | None) -> dict:
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json_body: Any = None, params: dict | None = None) -> Any:
        return await self._request("POST", path, params=params, json_body=json_body)

    async def put(self, path: str, json_body: Any = None, params: dict | None = None) -> Any:
        return await self._request("PUT", path, params=params, json_body=json_body)

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: Any = None
    ) -> Any:
        client = _get_http_client()
        url = f"{self._base_url}{path}"
        params = self._clean_params(params)

        # One token for this logical request, reused across its own retries
        # below — a transient 429/5xx on the actual endpoint doesn't mean
        # the token itself is bad, so there's no reason to re-exchange it.
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
            except httpx.RequestError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                raise NinjaOneError(0, f"{e or type(e).__name__} (url={url})") from e

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                delay = self._retry_delay(resp, attempt)
                await asyncio.sleep(delay)
                continue

            self._raise_for_status(resp)
            return self._parse_body(resp)

        # Unreachable in practice (loop always returns or raises above), but
        # keeps type checkers happy and guards against future edits.
        if last_exc:
            raise NinjaOneError(0, f"{last_exc}") from last_exc
        raise NinjaOneError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _parse_body(self, resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return {"raw_response": resp.text}

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise NinjaOneError(resp.status_code, self._extract_error(resp))
