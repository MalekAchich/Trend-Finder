"""The channel report: how our own posts did, as plain facts for every judge and the learner (Plan 9). No model call,
and never a verdict on the owner's posts: the numbers say what the audience rewarded."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_db.models import SocialChannel, SocialChannelSnapshot, SocialPost, SocialSnapshot, Video

RECENT = timedelta(days=30)
MAX_POSTS = 8
PLATFORM = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube", "x": "X"}


def fmt(n: float | None) -> str:
    if n is None:
        return "–"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return f"{n:.0f}"


def audience_line(groups: dict) -> str | None:
    """'countries US 40%, TN 25% · ages 18-24 50%': the top of each breakdown, as shares."""
    parts = []
    for key, name in (("country", "countries"), ("city", "cities"), ("age", "ages"), ("gender", "genders")):
        rows = groups.get(key) or []
        total = sum(r[1] for r in rows) or 1
        if rows:
            parts.append(f"{name} " + ", ".join(f"{label} {value / total:.0%}" for label, value in rows[:3]))
    return " · ".join(parts) or None


def age(hours: float) -> str:
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} days"


def engagement(s: SocialSnapshot) -> float | None:
    """(likes + comments + shares + saves) / views; None when views are unknown."""
    if not s.views:
        return None
    return sum(x or 0 for x in (s.likes, s.comments, s.shares, s.saves)) / s.views


@dataclass
class PostLine:
    post: SocialPost
    platform: str
    snap: SocialSnapshot
    hours: float

    @property
    def per_hour(self) -> float:
        return (self.snap.views or 0) / max(self.hours, 1.0)


async def channel_report(sm: async_sessionmaker[AsyncSession], character_id: uuid.UUID, now: datetime) -> str | None:
    async with sm() as s:
        channels = (await s.execute(select(SocialChannel).where(SocialChannel.character_id == character_id)
                                    .order_by(SocialChannel.platform))).scalars().all()
        if not channels:
            return None
        lines = ["Your own channels (facts from the platforms, not verdicts on the owner's videos):"]
        posts: list[PostLine] = []
        for ch in channels:
            snaps = (await s.execute(select(SocialChannelSnapshot).where(SocialChannelSnapshot.channel_id == ch.id)
                                     .order_by(SocialChannelSnapshot.taken_at.desc()))).scalars().all()
            latest = snaps[0] if snaps else None
            week = next((x for x in snaps if x.taken_at <= now - timedelta(days=7)), snaps[-1] if snaps else None)
            growth = (f" ({latest.followers - week.followers:+d} in 7 days)" if latest and week and latest is not week
                      and latest.followers is not None and week.followers is not None else "")
            lines.append(f"- {PLATFORM.get(ch.platform, ch.platform)} @{ch.handle}: "
                         f"{fmt(latest.followers if latest else None)} followers{growth}, "
                         f"{fmt(latest.posts if latest else None)} posts.")
            for report, label in (("reached", "Reached (who saw the videos)"), ("engaged", "Engaged"),
                                  ("followers", "Followers")):
                if (where := audience_line((ch.audience or {}).get(report) or {})):
                    lines.append(f"  {label}: {where}")
            rows = (await s.execute(select(SocialPost).where(SocialPost.channel_id == ch.id,
                                                             SocialPost.posted_at >= now - RECENT))).scalars().all()
            for p in rows:
                snap = (await s.execute(select(SocialSnapshot).where(SocialSnapshot.post_id == p.id)
                                        .order_by(SocialSnapshot.taken_at.desc()).limit(1))).scalar_one_or_none()
                if snap is not None and p.posted_at is not None:
                    posts.append(PostLine(p, ch.platform, snap, (snap.taken_at - p.posted_at).total_seconds() / 3600))
        sources = {p.post.inspired_by for p in posts if p.post.inspired_by}
        creators: dict[str, str | None] = {}
        if sources:
            found = await s.execute(select(Video.canonical_id, Video.creator_handle).where(
                Video.canonical_id.in_(sources)))
            creators = {c: h for c, h in found.all()}
    if not posts:
        lines.append("No posts in the last 30 days yet.")
        return "\n".join(lines)
    posts.sort(key=lambda p: (p.per_hour, engagement(p.snap) or 0), reverse=True)
    lines.append(f"Posts of the last 30 days, best first ({len(posts)}):")
    for p in posts[:MAX_POSTS]:
        s, study = p.snap, p.post.study or {}
        eng = engagement(s)
        bits = [f"{fmt(s.views)} views in {age(p.hours)} ({fmt(p.per_hour)}/h)",
                f"{eng * 100:.1f}% engagement" if eng is not None else "engagement –"]
        if s.reach is not None:
            bits.append(f"reach {fmt(s.reach)}")
        if s.avg_watch_s is not None:
            through = f" of {p.post.duration_s:.0f} s ({s.avg_watch_s / p.post.duration_s:.0%})" \
                if p.post.duration_s else ""
            bits.append(f"avg watch {s.avg_watch_s:.1f} s{through}")
        what = study.get("format") or (p.post.caption or "")[:80] or "–"
        tags = ", ".join((study.get("tags") or [])[:5])
        src = p.post.inspired_by
        where = audience_line({"country": (p.post.audience or {}).get("country") or []})
        lines.append(f"- {PLATFORM.get(p.platform, p.platform)}: {', '.join(bits)} | {what}"
                     + (f" | views from {where.removeprefix('countries ')}" if where else "")
                     + (f" | tags: {tags}" if tags else "")
                     + (f" | recreated from @{creators[src]}'s video" if src and creators.get(src)
                        else f" | recreated from {src}" if src else ""))
    return "\n".join(lines)
