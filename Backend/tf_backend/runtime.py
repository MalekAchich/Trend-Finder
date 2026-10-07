"""Wires the real multi-agent stack: both subscriptions, tools, process-pool analysis, curator, orchestrator."""
from __future__ import annotations

from dataclasses import dataclass

from tf_agent.config import AppSettings
from tf_agent.curation.curate import Curator
from tf_agent.learning.learner import Learner
from tf_agent.orchestrator.run import Orchestrator
from tf_agent.pipeline.analyze import process_pool_runner
from tf_agent.roles.runners import Roles
from tf_agent.tools.factory import ToolStack, build_analyzer, build_tool_stack
from tf_backend.services import Services, build_services, close_services


@dataclass
class Runtime:
    services: Services
    stack: ToolStack
    orchestrator: Orchestrator
    heavy: process_pool_runner

    async def close(self) -> None:
        self.heavy.close()
        if self.stack.browser is not None:
            await self.stack.browser.close()
        await close_services(self.services)


async def build_runtime(settings: AppSettings | None = None, heavy_workers: int = 2) -> Runtime:
    s = settings or AppSettings()
    services = await build_services(s)
    await services.registry.refresh()
    assert services.sessionmaker is not None
    stack = build_tool_stack(s, services.sessionmaker)
    heavy = process_pool_runner(max_workers=heavy_workers, whisper_model=s.whisper_model)
    analyzer = build_analyzer(s, stack, heavy, max_parallel=heavy_workers)
    roles = Roles(services.client)
    orchestrator = Orchestrator(services.sessionmaker, roles, stack, analyzer, stack.store,
                                curator=Curator(services.sessionmaker, roles),
                                learner=Learner(services.sessionmaker, roles))
    return Runtime(services, stack, orchestrator, heavy)
