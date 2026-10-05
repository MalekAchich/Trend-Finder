from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://tf:tf@localhost:5433/trendfinder"
    database_url_test: str = "postgresql+asyncpg://tf:tf@localhost:5433/trendfinder_test"
