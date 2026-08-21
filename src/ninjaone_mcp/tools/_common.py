from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No NinjaOne credentials. Send the X-Ninja-Client-Id and X-Ninja-Client-Secret headers "
    "(optionally X-Ninja-Region).",
    False,
)

# Running a script needs a *user*-context token (NinjaOne ties script
# execution to a real user for its audit trail), which the machine
# credentials above can't provide regardless of scope. See api_client.py's
# NinjaOneClient docstring.
NO_USER_TOKEN = error_envelope(
    "not_configured",
    "No NinjaOne user credentials for running scripts. Send the X-Ninja-User-Client-Id, "
    "X-Ninja-User-Client-Secret, and X-Ninja-Refresh-Token headers (a Web Application app's "
    "credentials plus a refresh token from that app's one-time browser authorization) — the "
    "X-Ninja-Client-Id/Secret machine credentials cannot run scripts.",
    False,
)

_MAX_PAGE_SIZE = 200
