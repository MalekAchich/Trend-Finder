import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime settings; read from the environment and `.env` in the app root (run commands from there)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://tf:tf@localhost:5433/trendfinder"
    config_dir: Path = Path("config")
    claude_bin: str = "claude"
    claude_runtime_dir: Path = Path(tempfile.gettempdir()) / "tf-claude"
    chatgpt_auth_file: Path = Path("secrets/chatgpt-auth.json")
    chatgpt_models_cache: Path = Path("secrets/chatgpt-models.json")
    per_provider_concurrency: int = 4
