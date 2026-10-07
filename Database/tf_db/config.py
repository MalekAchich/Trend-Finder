from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """Connection URLs come only from the environment / the app's `.env` (never committed): no default credentials."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(description="postgresql+asyncpg://<user>:<password>@localhost:5433/trendfinder")
    database_url_test: str = Field(description="the same server, database trendfinder_test")
