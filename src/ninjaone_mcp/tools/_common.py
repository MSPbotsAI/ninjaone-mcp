from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No NinjaOne credentials. Send the X-Ninja-Token header (optionally X-Ninja-Region).",
    False,
)

_MAX_PAGE_SIZE = 200
