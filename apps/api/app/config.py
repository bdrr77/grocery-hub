from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://grocery:change-me@postgres:5432/grocery"
    redis_url: str = "redis://redis:6379/0"
    scanner_device_tokens: str = ""
    picnic_username: str = ""
    picnic_password: str = Field(default="", repr=False)
    picnic_country_code: str = "FR"
    picnic_api_version: str = "15"
    picnic_device_id: str = "grocery-hub"
    picnic_agent: str = "30100;1.246.1-15599;"
    chronodrive_username: str = ""
    chronodrive_email: str = ""
    chronodrive_password: str = Field(default="", repr=False)
    chronodrive_store_id: str = ""
    chronodrive_site_mode: str = "DRIVE"
    connector_poll_seconds: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def scanner_tokens(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for entry in self.scanner_device_tokens.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            device_id, token = entry.split(":", 1)
            if device_id.strip() and token.strip():
                result[device_id.strip()] = token.strip()
        return result


settings = Settings()
