import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Runtime settings; read from the environment and `.env` in the app root (run commands from there)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str  # from .env only (never committed): no default credentials
    config_dir: Path = Path("config")
    claude_bin: str = "claude"
    claude_runtime_dir: Path = Path(tempfile.gettempdir()) / "tf-claude"
    chatgpt_auth_file: Path = Path("secrets/chatgpt-auth.json")
    chatgpt_models_cache: Path = Path("secrets/chatgpt-models.json")
    per_provider_concurrency: int = 4
    searxng_url: str = "http://127.0.0.1:8888"
    characters_dir: Path = Path("../AI Influencers Characters")
    media_dir: Path = Path("media")
    whisper_model: str = "small"
    secrets_dir: Path = Path("secrets")  # API keys and scraping-account sessions (tf_agent/credentials.py)
    browser_pages: int = 2  # headless pages open at once for logged-in searches
    firefox_bin: str = "firefox"  # the owner's Firefox, for logins that need a real browser (TikTok One)
