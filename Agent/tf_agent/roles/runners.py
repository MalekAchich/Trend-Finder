"""Role runners: structured judgments (master, analyst, cross-check, seed study) and tool-using workers."""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from tf_agent.loop.agent import AgentBudget, AgentResult, EventSink, run_agent
from tf_agent.loop.tools import Tool
from tf_agent.models.client import CallContext, ModelClient
from tf_agent.models.types import CompletionRequest, ImagePart, Message
from tf_agent.roles.prompts import PromptLibrary
from tf_agent.roles.schemas import AnalystResult, CrossCheck, SeedStudy, WorkerResult, WorkPlan

T = TypeVar("T", bound=BaseModel)


class RoleOutputError(Exception):
    pass


@dataclass
class Judged(Generic[T]):
    result: T
    provider: str
    model: str


@dataclass
class TaskSpec:
    task_type: str
    platform: str
    scope: dict[str, Any]
    goal: str
    max_candidates: int = 6
    direction_label: str = ""
    direction_hypothesis: str = ""
    lead: dict[str, Any] | None = field(default=None)


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'root'}: {err['msg']}" for err in e.errors()[:8])


def _task_text(spec: TaskSpec) -> str:
    scope = spec.scope or {}
    lines = [f"Your task: {spec.task_type} on {spec.platform}."]
    if spec.direction_label:
        lines.append(f"Direction: {spec.direction_label} — {spec.direction_hypothesis}".rstrip(" —"))
    lines.append(f"Goal: {spec.goal}")
    lines.append("Your scope (yours alone; other workers own everything else):")
    for key in ("queries", "hashtags", "creators", "sounds"):
        if scope.get(key):
            lines.append(f"- {key}: {', '.join(map(str, scope[key]))}")
    if spec.lead:
        lines.append(f"Lead to follow: {spec.lead.get('type')} = {spec.lead.get('value')} ({spec.lead.get('why', '')})")
    lines.append(f"Submit at most {spec.max_candidates} candidates.")
    return "\n".join(lines)


class Roles:
    def __init__(self, client: ModelClient, prompts: PromptLibrary | None = None,
                 worker_budget: AgentBudget | None = None) -> None:
        self.client = client
        self.prompts = prompts or PromptLibrary()
        self.worker_budget = worker_budget or AgentBudget(max_steps=14)

    async def _structured(self, role: str, system: str, text: str, images: Sequence[ImagePart], model_cls: type[T],
                          schema_name: str, *, run_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None,
                          only: str | None = None) -> Judged[T]:
        messages = [Message.user(text, list(images))]
        ctx = CallContext(role=role, run_id=run_id, task_id=task_id)
        for attempt in (1, 2):
            req = CompletionRequest(model="", system=system, messages=messages,
                                    output_schema=model_cls.model_json_schema(), schema_name=schema_name)
            resp = await self.client.complete(req, ctx, only=only)
            try:
                payload = resp.structured if resp.structured is not None else json.loads(resp.text)
                return Judged(model_cls.model_validate(payload), resp.provider, resp.model)
            except (ValidationError, json.JSONDecodeError) as e:
                problem = _short(e) if isinstance(e, ValidationError) else "not valid JSON"
                if attempt == 2:
                    raise RoleOutputError(f"{role}: invalid output after repair: {problem}") from e
                messages = [*messages, Message.assistant(resp.text or json.dumps(resp.structured)),
                            Message.user(f"Your output was invalid: {problem}. Return corrected JSON for the schema.")]
        raise AssertionError("unreachable")

    async def plan(self, context: str, brief: str = "", *, run_id: uuid.UUID | None = None) -> WorkPlan:
        judged = await self._structured("master", self.prompts.render("master", brief=brief), context, (),
                                        WorkPlan, "work_plan", run_id=run_id)
        return judged.result

    async def work(self, spec: TaskSpec, brief: str, tools: Sequence[Tool], *, run_id: uuid.UUID | None = None,
                   task_id: uuid.UUID | None = None, provider: str | None = None,
                   on_event: EventSink | None = None) -> AgentResult[WorkerResult]:
        return await run_agent(client=self.client, role=spec.task_type,
                               system=self.prompts.render(spec.task_type, brief=brief), task=_task_text(spec),
                               result_model=WorkerResult, tools=tools, budget=self.worker_budget, run_id=run_id,
                               task_id=task_id, provider=provider, on_event=on_event)

    def _facts_text(self, facts: dict[str, Any]) -> str:
        return "Candidate facts (metadata, transcript, measurements):\n" + json.dumps(facts, default=str)

    async def analyze(self, brief: str, canonical_image: str, contact_sheet: str, facts: dict[str, Any], *,
                      run_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None,
                      provider: str | None = None) -> Judged[AnalystResult]:
        return await self._structured("analyst", self.prompts.render("analyst", brief=brief), self._facts_text(facts),
                                      [ImagePart(canonical_image), ImagePart(contact_sheet)], AnalystResult,
                                      "analysis", run_id=run_id, task_id=task_id, only=provider)

    async def cross_check(self, brief: str, canonical_image: str, contact_sheet: str, facts: dict[str, Any], *,
                          exclude_provider: str, run_id: uuid.UUID | None = None) -> Judged[CrossCheck]:
        others = [c.provider for c in self.client.router.candidates("cross_check")
                  if c.provider != exclude_provider and c.provider in self.client.adapters]
        if not others:
            raise RoleOutputError("cross_check: no second provider configured")
        return await self._structured("cross_check", self.prompts.render("cross_check", brief=brief),
                                      self._facts_text(facts), [ImagePart(canonical_image), ImagePart(contact_sheet)],
                                      CrossCheck, "cross_check", run_id=run_id, only=others[0])

    async def study_seed(self, brief: str, contact_sheet: str, facts: dict[str, Any], *,
                         run_id: uuid.UUID | None = None) -> Judged[SeedStudy]:
        return await self._structured("seed_study", self.prompts.render("seed_study", brief=brief),
                                      self._facts_text(facts), [ImagePart(contact_sheet)], SeedStudy, "seed_study",
                                      run_id=run_id)
