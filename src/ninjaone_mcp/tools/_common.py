from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No NinjaOne credentials. Send the X-Ninja-Token header (optionally X-Ninja-Region).",
    False,
)

# Running a script needs a *user*-context token (NinjaOne ties script
# execution to a real user for its audit trail), which the machine token
# above can't provide regardless of scope. See api_client.py's
# NinjaOneClient docstring.
NO_USER_TOKEN = error_envelope(
    "not_configured",
    "No NinjaOne user credentials for running scripts. Send the X-Ninja-User-Token header "
    "(an OAuth2 refresh_token-grant access token) — the X-Ninja-Token machine credential "
    "cannot run scripts.",
    False,
)

_MAX_PAGE_SIZE = 200
