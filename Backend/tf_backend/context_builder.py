"""Builds the production AppContext: both subscriptions, tools, lazily-started orchestrator + process pool."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from tf_agent.config import AppSettings
from tf_agent.curation.curate import Curator
from tf_agent.learning.learner import Learner
from tf_agent.orchestrator.run import Orchestrator
from tf_agent.pipeline.analyze import process_pool_runner
from tf_agent.roles.runners import Roles
from tf_agent.tools.factory import build_analyzer, build_tool_stack
from tf_backend.app_context import AppContext
from tf_backend.runs import RunManager
from tf_backend.services import Services, build_services, close_services


async def build_context(settings: AppSettings | None = None) -> tuple[AppContext, Services, Callable[[], Awaitable[None]]]:
    s = settings or AppSettings()
    services = await build_services(s)
    assert services.sessionmaker is not None
    sm = services.sessionmaker
    roles = Roles(services.client)
    stack = build_tool_stack(s, sm)
    pools: list[process_pool_runner] = []
    learner = Learner(sm, roles)

    def make_orchestrator() -> Orchestrator:  # built on first run: the process pool only starts when needed
        heavy = process_pool_runner(max_workers=2, whisper_model=s.whisper_model)
        pools.append(heavy)
        analyzer = build_analyzer(s, stack, heavy, max_parallel=2)
        return Orchestrator(sm, roles, stack, analyzer, stack.store, curator=Curator(sm, roles), learner=learner)

    runs = RunManager(sm, make_orchestrator)
    context = AppContext(sessionmaker=sm, runs=runs, learner=learner,
                         media_dir=s.media_dir, characters_dir=s.characters_dir,
                         sse_poll_s=1.0)

    async def cleanup() -> None:
        await runs.shutdown()
        for pool in pools:
            pool.close()
        await close_services(services)

    return context, services, cleanup
