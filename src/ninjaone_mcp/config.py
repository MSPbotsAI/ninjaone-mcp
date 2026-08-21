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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mcp_http_port: int = 8080
    mcp_http_host: str = "0.0.0.0"


def get_settings() -> Settings:
    return Settings()
