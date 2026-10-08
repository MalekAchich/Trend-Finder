"""ModelClient: role routing + usage governor + retries + provider fallback + call ledger."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from tf_agent.models.base import ProviderAdapter
from tf_agent.models.errors import (
    ContentRefused,
    AllProvidersUnavailable,
    AuthRequired,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.governor import UsageGovernor
from tf_agent.models.router import RoleRouter, RouteCandidate
from tf_agent.models.types import CompletionRequest, CompletionResponse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallContext:
    role: str
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


@dataclass(frozen=True)
class CallRecord:
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    role: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    images: int
    latency_ms: int
    status: str
    error_class: str | None


class CallLedger(Protocol):
    async def record(self, rec: CallRecord) -> None: ...


class ModelClient:
    def __init__(self, adapters: Mapping[str, ProviderAdapter], router: RoleRouter, governor: UsageGovernor,
                 ledger: CallLedger | None = None, *, max_attempts: int = 3, backoff_s: float = 1.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.adapters = dict(adapters)
        self.router = router
        self.governor = governor
        self.ledger = ledger
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self._sleep = sleep
        self.effort_override: dict[str, str] = {}
        self.refusal_log: Path | None = None  # every refusal with what was sent, so the prompt can be fixed
        self.on_refused: Callable[[CallContext, ContentRefused], Awaitable[None]] | None = None  # the owner's per-provider effort (Settings), beats role efforts
        # awaited when a call falls back to a later provider: (ctx, from_provider, to_provider, reason)
        self.on_switch: Callable[[CallContext, str, str, str], Awaitable[None]] | None = None

    async def _record(self, ctx: CallContext, cand: RouteCandidate, req: CompletionRequest, started: float,
                      resp: CompletionResponse | None, error: Exception | None) -> None:
        if self.ledger is None:
            return
        rec = CallRecord(
            run_id=ctx.run_id, task_id=ctx.task_id, role=ctx.role, provider=cand.provider,
            model=resp.model if resp else cand.model,
            input_tokens=resp.usage.input_tokens if resp else None,
            output_tokens=resp.usage.output_tokens if resp else None,
            images=sum(len(m.images()) for m in req.messages),
            latency_ms=int((time.monotonic() - started) * 1000),
            status="ok" if error is None else "error",
            error_class=type(error).__name__ if error else None,
        )
        try:
            await self.ledger.record(rec)
        except Exception as e:  # the ledger must never break a model call
            log.warning("call ledger write failed: %s", e)

    def _supported_effort(self, provider: str, model: str, effort: str | None) -> str | None:
        """Drop an effort the resolved model doesn't offer (the provider would reject the whole call)."""
        if effort is None:
            return None
        info = next((m for m in self.router.registry.models(provider) if m.model_id == model), None)
        if info is not None and info.reasoning_levels and effort not in info.reasoning_levels:
            log.info("%s %s doesn't offer effort %s; using its default", provider, model, effort)
            return None
        return effort

    async def _refused(self, ctx: CallContext, cand: RouteCandidate, req: CompletionRequest,
                       err: ContentRefused) -> None:
        if self.refusal_log is not None:
            row = {"at": datetime.now(UTC).isoformat(), "role": ctx.role, "provider": cand.provider,
                   "model": req.model, "run_id": str(ctx.run_id) if ctx.run_id else None,
                   "task_id": str(ctx.task_id) if ctx.task_id else None, "request_id": err.request_id,
                   "detail": err.detail, "message": err.message[:1000], "system": req.system[:4000],
                   "messages": "\n".join(f"[{m.role}] {m.text()}" for m in req.messages)[-8000:]}
            try:
                self.refusal_log.parent.mkdir(parents=True, exist_ok=True)
                with self.refusal_log.open("a") as f:
                    f.write(json.dumps(row) + "\n")
            except OSError as e:
                log.warning("refusal log write failed: %s", e)
        if self.on_refused is not None:
            try:
                await self.on_refused(ctx, err)
            except Exception as e:  # narration must never mask the refusal
                log.warning("refusal hook failed: %s", e)

    def end_conversation(self, conversation: str) -> None:
        """An agent finished: providers drop what they kept for its conversation (Claude's CLI session)."""
        for adapter in self.adapters.values():
            end = getattr(adapter, "end_conversation", None)
            if end is not None:
                try:
                    end(conversation)
                except Exception as e:  # cleanup must never fail an agent
                    log.warning("ending conversation on %s failed: %s", getattr(adapter, "name", "?"), e)

    async def complete(self, req: CompletionRequest, ctx: CallContext, only: str | None = None,
                       prefer: str | None = None) -> CompletionResponse:
        last_error: Exception | None = None
        failed_from: str | None = None
        skipped: str | None = None  # a provider passed over because it's at its usage limit (no call made)
        for cand in self.router.candidates(ctx.role, only=only, prefer=prefer):
            if cand.provider not in self.adapters:
                continue
            if not self.governor.available(cand.provider):
                if skipped is None and failed_from is None:
                    skipped = cand.provider
                continue
            if skipped is not None and failed_from is None and self.on_switch is not None:
                st = self.governor.status(skipped)
                try:
                    await self.on_switch(ctx, skipped, cand.provider,
                                         f"{skipped} is at its usage limit ({st.last_error or st.status})")
                except Exception as e:  # narration must never break a model call
                    log.warning("switch hook failed: %s", e)
                skipped = None
            if failed_from is not None and failed_from != cand.provider and self.on_switch is not None:
                try:
                    await self.on_switch(ctx, failed_from, cand.provider, getattr(last_error, "message", None)
                                         or str(last_error))
                except Exception as e:  # narration must never break a model call
                    log.warning("switch hook failed: %s", e)
            adapter = self.adapters[cand.provider]
            effort = self._supported_effort(cand.provider, cand.model,
                                            self.effort_override.get(cand.provider) or cand.effort or req.reasoning_effort)
            concrete = replace(req, model=cand.model, reasoning_effort=effort)
            for attempt in range(1, self.max_attempts + 1):
                started = time.monotonic()
                try:
                    async with self.governor.slot(cand.provider):
                        resp = await adapter.complete(concrete)
                except (TransientProviderError, MalformedResponse) as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    last_error = e
                    if attempt < self.max_attempts:
                        await self._sleep(self.backoff_s * 2 ** (attempt - 1) + random.uniform(0, self.backoff_s / 4))
                        continue
                    break
                except UsageLimited as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    await self.governor.mark_cooling(cand.provider, e.reset_at, e.message)
                    last_error = e
                    break
                except ContentRefused as e:  # owner: fix refusals, never hand the call to another provider
                    await self._record(ctx, cand, concrete, started, None, e)
                    await self._refused(ctx, cand, concrete, e)
                    raise
                except AuthRequired as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    await self.governor.mark_auth_error(cand.provider, e.message)
                    last_error = e
                    break
                except ProviderError as e:
                    await self._record(ctx, cand, concrete, started, None, e)
                    raise
                await self._record(ctx, cand, concrete, started, resp, None)
                await self.governor.mark_ok(cand.provider)
                await self.governor.observe_rate(cand.provider, resp.rate)
                return resp
            failed_from = cand.provider
        raise AllProvidersUnavailable(ctx.role, self.governor.earliest_recovery()) from last_error
