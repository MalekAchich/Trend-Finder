"""Builds the long-lived service graph shared by the API and the CLI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tf_agent.config import AppSettings
from tf_agent.models.base import ProviderAdapter
from tf_agent.models.chatgpt_adapter import ChatGPTOAuthAdapter
from tf_agent.models.chatgpt_auth import ChatGptAuth
from tf_agent.models.claude_cli import ClaudeCLIAdapter, ClaudeCliAuth
from tf_agent.models.client import ModelClient
from tf_agent.models.governor import UsageGovernor
from tf_agent.models.persistence import DbCallLedger, DbProviderStateStore, save_models
from tf_agent.models.registry import ModelRegistry, load_aliases
from tf_agent.models.router import RoleRouter, load_roles
from tf_db.session import make_engine, make_sessionmaker


@dataclass
class Services:
    settings: AppSettings | None
    chatgpt_auth: Any
    claude_auth: Any
    adapters: dict[str, ProviderAdapter]
    registry: ModelRegistry
    router: RoleRouter
    governor: UsageGovernor
    client: ModelClient
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def build_services(settings: AppSettings | None = None, *, with_db: bool = True) -> Services:
    s = settings or AppSettings()
    chatgpt_auth = ChatGptAuth(s.chatgpt_auth_file)
    claude_auth = ClaudeCliAuth(s.claude_bin, log_path=s.claude_runtime_dir / "login.log")
    adapters: dict[str, ProviderAdapter] = {
        "claude": ClaudeCLIAdapter(claude_auth, runtime_dir=s.claude_runtime_dir),
        "chatgpt": ChatGPTOAuthAdapter(chatgpt_auth, models_cache=s.chatgpt_models_cache),
    }
    engine = sm = ledger = store = on_models = None
    if with_db:
        engine = make_engine(s.database_url)
        sm = make_sessionmaker(engine)
        ledger = DbCallLedger(sm)
        store = DbProviderStateStore(sm)

        async def on_models(provider, models):
            await save_models(sm, provider, models)

    registry = ModelRegistry(adapters, load_aliases(s.config_dir / "model_aliases.yaml"), on_models=on_models)
    router = RoleRouter(load_roles(s.config_dir / "roles.yaml"), registry)
    governor = UsageGovernor(adapters.keys(), s.per_provider_concurrency, store=store)
    if store is not None:
        await governor.load()
    client = ModelClient(adapters, router, governor, ledger)
    client.refusal_log = Path(__file__).resolve().parents[2] / "logs" / "refusals.jsonl"
    return Services(s, chatgpt_auth, claude_auth, adapters, registry, router, governor, client, engine, sm)


async def close_services(sv: Services) -> None:
    if sv.engine is not None:
        await sv.engine.dispose()
