"""Structured outputs every role must produce (validated with pydantic; never parsed from free text)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["tiktok", "instagram", "youtube", "x"]
TaskType = Literal["scout", "radar", "deep_dive"]


class DirectionPlan(BaseModel):
    key: str = Field(min_length=3, max_length=80, description="short slug, reuse an existing key to exploit it")
    label: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=3, max_length=400, description="why this could suit the character")
    niche: str | None = Field(None, max_length=80, description="the niche this direction tests")
    mode: Literal["explore", "exploit"]


class ScopeSpec(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=6)
    hashtags: list[str] = Field(default_factory=list, max_length=6)
    creators: list[str] = Field(default_factory=list, max_length=4)
    sounds: list[str] = Field(default_factory=list, max_length=4)


class TaskPlan(BaseModel):
    task_type: TaskType
    direction_key: str | None = Field(None, description="key of the direction this task serves")
    platform: Platform
    scope: ScopeSpec
    goal: str = Field(min_length=3, max_length=400)
    lead_id: str | None = Field(None, description="for deep_dive: the id of an open lead to follow")
    max_candidates: int = Field(6, ge=1, le=10)


class WorkPlan(BaseModel):
    reasoning_summary: str = Field(max_length=1500)
    directions: list[DirectionPlan] = Field(default_factory=list, max_length=12)
    tasks: list[TaskPlan] = Field(default_factory=list, max_length=16)
    stop: bool = False
    stop_reason: str | None = None


class Candidate(BaseModel):
    canonical_id: str = Field(description="exactly as shown in a tool result, e.g. tiktok:7412…")
    why: str = Field(min_length=3, max_length=500)
    preliminary_fit: float = Field(ge=0, le=10)


class LeadOut(BaseModel):
    type: Literal["sound", "hashtag", "creator", "format", "query"]
    value: str = Field(min_length=1, max_length=200)
    platform: Platform = "tiktok"
    why: str = Field(min_length=3, max_length=300)


class WorkerResult(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list, max_length=10)
    leads: list[LeadOut] = Field(default_factory=list, max_length=8)
    notes: str = Field("", max_length=800)


class FitBreakdown(BaseModel):
    look: float = Field(ge=0, le=10)
    vibe: float = Field(ge=0, le=10)
    energy: float = Field(ge=0, le=10)
    niche: float = Field(ge=0, le=10)
    adaptability: float = Field(ge=0, le=10)


class AnalystResult(BaseModel):
    fit_breakdown: FitBreakdown
    justification: str = Field(min_length=3, max_length=700)
    adaptation_idea: str = Field(min_length=3, max_length=700)
    feasibility_notes: str = Field(max_length=500)
    niche_guess: str = Field(max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=10, description="3-10 topic tags, lowercase, no #")
    trend_type: str = Field("", max_length=60, description="dance, skit, lip-sync, POV, transition, …")
    audio_use: str = Field("", max_length=200, description="how the sound carries it: trending song, voiceover, …")

    @property
    def fit(self) -> float:
        b = self.fit_breakdown
        return round((b.look + b.vibe + b.energy + b.niche + b.adaptability) / 5 * 10, 2)


class CrossCheck(BaseModel):
    fit: float = Field(ge=0, le=100)
    justification: str = Field(min_length=3, max_length=600)


class SeedStudy(BaseModel):
    format: str = Field(max_length=300)
    hook: str = Field(max_length=300)
    why_it_works: str = Field(max_length=500)
    search_angles: list[str] = Field(default_factory=list, max_length=8)
    fit_for_character: str = Field(max_length=400)
    niche: str = Field("", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=10, description="3-10 topic tags, lowercase, no #")
    trend_type: str = Field("", max_length=60, description="dance, skit, lip-sync, POV, transition, …")
    audio_use: str = Field("", max_length=200, description="how the sound carries it: trending song, voiceover, …")
    fit_score: int = Field(5, ge=0, le=10, description="how well this direction suits the character")
