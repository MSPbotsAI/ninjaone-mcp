from pydantic_settings import BaseSettings, SettingsConfigDict

# NinjaOne is multi-region: each region is a fully separate deployment with
# its own base URL. Confirmed against the community wyre-technology/ninjaone-mcp
# project's region table, which matches NinjaOne's own OAuth app setup docs.
REGION_BASE_URLS: dict[str, str] = {
    "us": "https://app.ninjarmm.com",
    "eu": "https://eu.ninjarmm.com",
    "oc": "https://oc.ninjarmm.com",
    "ca": "https://ca.ninjarmm.com",
    "us2": "https://us2.ninjarmm.com",
    "fed": "https://fed.ninjarmm.com",
}
DEFAULT_REGION = "us"


def region_base_url(region: str | None) -> str:
    """Resolve a NinjaOne region code to its base URL, defaulting to `us`
    for an unset or unrecognized value rather than failing — the region
    header is optional and most tenants are on the US instance.
    """
    key = (region or DEFAULT_REGION).strip().lower()
    return REGION_BASE_URLS.get(key, REGION_BASE_URLS[DEFAULT_REGION])


def resolve_base_url(base_url: str | None, region: str | None) -> str:
    """Resolve the API host from the two headers the gateway may send, base_url winning.

    Two credential shapes reach this server and they identify the region differently:

      X-Ninja-Region    a region KEY ("us2"), sent by the delegated `ninjaone` integration.
                        The region there follows the SHARED MSPbots app and is the same for
                        every tenant, so the manager injects it as a static header.
      X-Ninja-Base-Url  a full host, sent by the `ninjaone-app` (API Services) integration.
                        There the customer supplies their own credentials AND picks their
                        own region, so the value is per-tenant and travels as a URL.

    base_url wins when both are present: it is the more specific, per-tenant answer. Neither
    is required — falling back to region_base_url() keeps the pre-existing behaviour (and its
    `us` default) exactly as it was for every caller that sends only the region.
    """
    if base_url and base_url.strip():
        return base_url.strip().rstrip("/")
    return region_base_url(region)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mcp_http_port: int = 8080
    mcp_http_host: str = "0.0.0.0"

    # Whether to register ninjaone_run_script_on_device. The same image serves two
    # integrations and this is the ONLY tool that differs between them:
    #
    #   ninjaone      (delegated, authorization_code) leaves this True. Its token carries a
    #                 real user identity, which is what NinjaOne binds script execution to
    #                 for its audit trail.
    #   ninjaone-app  (API Services, client_credentials) sets ENABLE_SCRIPT_EXECUTION=false.
    #                 That token is a machine identity with no user behind it, so script
    #                 execution is expected to be rejected upstream.
    #
    # Registering the tool and letting it fail would be worse than not offering it: an agent
    # would keep retrying a capability that cannot work, and the upstream refusal arrives as
    # an opaque authorization error rather than "this deployment has no such tool". The other
    # four automation tools (scripts/options/jobs) stay in both — they are read-only and work
    # under either identity.
    enable_script_execution: bool = True


def get_settings() -> Settings:
    return Settings()
