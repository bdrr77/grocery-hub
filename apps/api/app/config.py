from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://grocery:change-me@postgres:5432/grocery"
    redis_url: str = "redis://redis:6379/0"
    scanner_device_tokens: str = ""

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
