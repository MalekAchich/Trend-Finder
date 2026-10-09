"""The Socials service: syncs our channels every few hours, keeps their thumbnails, and holds the TikTok login
hand-off (state + PKCE verifier, single use, 10 minutes). Numbers for the page are computed here."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.manual import small_jpeg
from tf_agent.socials.report import engagement
from tf_agent.socials.sync import SocialSync, SyncResult
from tf_db.models import SocialChannel, SocialChannelSnapshot, SocialPost, SocialSnapshot

log = logging.getLogger(__name__)
SYNC_EVERY_S = 3600  # hourly: new posts change fast
FIRST_SYNC_AFTER_S = 60  # a fresh start lets the app settle first
PENDING_TTL_S = 600
WINDOWS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}
VERIFIER_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


@dataclass
class Pending:
    channel_id: uuid.UUID
    verifier: str
    expires: float


@dataclass
class Socials:
    sm: async_sessionmaker[AsyncSession]
    sync: SocialSync
    creds: Any
    instagram: Any
    tiktok: Any
    thumbs_dir: Path
    fetch_image: Any
    redirect_uri: str = "http://127.0.0.1:8000/api/socials/tiktok/callback"
    get_meta: Any = None  # url -> VideoItem (yt-dlp): a post's length when the platform's API doesn't give it
    clock: Any = time.time
    pending: dict[str, Pending] = field(default_factory=dict)
    _loop: asyncio.Task[None] | None = None
    _locks: dict[uuid.UUID, asyncio.Lock] = field(default_factory=dict)

    # ---- the TikTok login hand-off ----
    def begin_tiktok(self, channel_id: uuid.UUID) -> tuple[str, str]:
        """(state, verifier) for one login, kept 10 minutes; older ones are dropped."""
        now = self.clock()
        self.pending = {k: v for k, v in self.pending.items() if v.expires > now}
        state = secrets.token_urlsafe(24)
        verifier = "".join(secrets.choice(VERIFIER_CHARS) for _ in range(64))
        self.pending[state] = Pending(channel_id, verifier, now + PENDING_TTL_S)
        return state, verifier

    def finish_tiktok(self, state: str) -> Pending | None:
        """The login this callback belongs to, used once; None for an unknown, replayed or expired state."""
        p = self.pending.pop(state, None)
        return p if p is not None and p.expires > self.clock() else None

    # ---- syncing ----
    async def sync_channel(self, channel_id: uuid.UUID) -> SyncResult:
        async with self._locks.setdefault(channel_id, asyncio.Lock()):  # a click during the 3-hour sync: one read
            result = await self.sync.sync_channel(channel_id)
        await self._save_thumbnails(channel_id)
        await self._fill_lengths(channel_id)
        return result

    async def _fill_lengths(self, channel_id: uuid.UUID) -> None:
        """Instagram's API gives no video length (needed for "watched through"): looked up once per post."""
        if self.get_meta is None:
            return
        async with self.sm() as s:
            posts = (await s.execute(select(SocialPost).where(SocialPost.channel_id == channel_id,
                                                              SocialPost.duration_s.is_(None)))).scalars().all()
        for p in posts:
            try:
                item = await self.get_meta(p.url)
            except Exception as e:  # no length is fine: watched-through stays "–"
                log.info("length of %s unknown: %s", p.url, e)
                continue
            if item.duration_s:
                async with self.sm() as s:
                    row = await s.get(SocialPost, p.id)
                    row.duration_s = float(item.duration_s)
                    await s.commit()

    async def sync_all(self) -> None:
        async with self.sm() as s:
            ids = (await s.execute(select(SocialChannel.id))).scalars().all()
        for cid in ids:
            try:
                await self.sync_channel(cid)
            except Exception as e:  # one channel never stops the others
                log.warning("socials sync of %s failed: %s", cid, e)

    def start(self) -> None:
        async def loop() -> None:
            await asyncio.sleep(FIRST_SYNC_AFTER_S)
            while True:
                await self.sync_all()
                await asyncio.sleep(SYNC_EVERY_S)
        self._loop = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()

    def thumb_file(self, post: SocialPost, platform: str) -> Path:
        return self.thumbs_dir / f"social-{platform}_{post.platform_post_id}.jpg"

    async def _save_thumbnails(self, channel_id: uuid.UUID) -> None:
        """Covers expire on the platforms' CDNs: each post's is kept once, small, like found videos'."""
        async with self.sm() as s:
            ch = await s.get(SocialChannel, channel_id)
            posts = (await s.execute(select(SocialPost).where(SocialPost.channel_id == channel_id))).scalars().all()
        for p in posts:
            out = self.thumb_file(p, ch.platform)
            if out.is_file() or not (p.thumbnail_url or "").startswith("http"):
                continue
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(small_jpeg(await self.fetch_image(p.thumbnail_url)))
            except Exception as e:  # no cover is fine: the card shows the platform instead
                log.info("cover of %s not saved: %s", p.platform_post_id, e)

    # ---- numbers for the page ----
    async def channel_out(self, s: AsyncSession, ch: SocialChannel, now: datetime) -> dict[str, Any]:
        snaps = (await s.execute(select(SocialChannelSnapshot).where(SocialChannelSnapshot.channel_id == ch.id)
                                 .order_by(SocialChannelSnapshot.taken_at.desc()))).scalars().all()
        latest = snaps[0] if snaps else None
        week = next((x for x in snaps if x.taken_at <= now - timedelta(days=7)), snaps[-1] if snaps else None)
        posts = await self.posts_out(s, ch, "7d", now)
        return {"id": str(ch.id), "platform": ch.platform, "handle": ch.handle, "mode": ch.mode,
                "connected_at": ch.connected_at, "last_synced_at": ch.last_synced_at, "last_error": ch.last_error,
                "scopes": ch.scopes or [], "followers": latest.followers if latest else None,
                "followers_7d": (latest.followers - week.followers) if latest and week and latest is not week
                and latest.followers is not None and week.followers is not None else None,
                "total_likes": latest.total_likes if latest else None, "posts": latest.posts if latest else None,
                "views_7d": sum(p["views_gained"] or 0 for p in posts) if posts else None,
                "audience": ch.audience, "account_insights": ch.account_insights}

    async def posts_out(self, s: AsyncSession, ch: SocialChannel, window: str, now: datetime) -> list[dict[str, Any]]:
        span = WINDOWS[window]
        q = select(SocialPost).where(SocialPost.channel_id == ch.id)
        rows = (await s.execute(q.order_by(SocialPost.posted_at.desc().nulls_last()))).scalars().all()
        out = []
        for p in rows:
            snaps = (await s.execute(select(SocialSnapshot).where(SocialSnapshot.post_id == p.id)
                                     .order_by(SocialSnapshot.taken_at.desc()))).scalars().all()
            if not snaps:
                continue
            last = snaps[0]
            # a post from before the window counts by what it gained inside it (it needs a snapshot from then)
            start = next((x for x in snaps if x.taken_at <= now - span), None) if span is not None else None
            posted_inside = span is None or (p.posted_at is not None and p.posted_at >= now - span)
            if not posted_inside and start is None:
                continue
            gained = (last.views if posted_inside else
                      (last.views - start.views if last.views is not None and start and start.views is not None
                       else None))
            day = next((x for x in snaps if x.taken_at <= now - timedelta(hours=24)), None)
            eng = engagement(last)
            # the post's life so far, oldest first: it starts at 0 views when posted
            spark = ([[0.0, 0]] if p.posted_at else []) + [
                [round((x.taken_at - p.posted_at).total_seconds() / 3600, 2) if p.posted_at else i, x.views]
                for i, x in enumerate(reversed(snaps)) if x.views is not None]
            out.append({
                "id": str(p.id), "platform": ch.platform, "platform_id": p.platform_post_id, "url": p.url,
                "canonical_id": p.canonical_id, "caption": p.caption, "posted_at": p.posted_at,
                "duration_s": p.duration_s, "inspired_by": p.inspired_by,
                "thumbnail_url": (f"/api/media/thumbs/{self.thumb_file(p, ch.platform).name}"
                                  if self.thumb_file(p, ch.platform).is_file() else p.thumbnail_url),
                "format": (p.study or {}).get("format"), "tags": (p.study or {}).get("tags") or [],
                "views": last.views, "reach": last.reach, "likes": last.likes, "comments": last.comments,
                "shares": last.shares, "saves": last.saves, "avg_watch_s": last.avg_watch_s,
                "watch_through": (last.avg_watch_s / p.duration_s) if last.avg_watch_s and p.duration_s else None,
                "engagement": eng, "views_gained": gained,
                "views_24h": (last.views - day.views) if day and last.views is not None and day.views is not None
                else None, "taken_at": last.taken_at, "spark": spark})
        return out

    async def timeline(self, s: AsyncSession, ch: SocialChannel) -> list[dict[str, Any]]:
        """The channel over time: total views of its posts and followers, one point per sync."""
        views = dict((await s.execute(
            select(SocialSnapshot.taken_at, func.sum(SocialSnapshot.views))
            .join(SocialPost, SocialPost.id == SocialSnapshot.post_id).where(SocialPost.channel_id == ch.id)
            .group_by(SocialSnapshot.taken_at))).all())
        followers = dict((await s.execute(select(SocialChannelSnapshot.taken_at, SocialChannelSnapshot.followers)
                                          .where(SocialChannelSnapshot.channel_id == ch.id))).all())
        times = sorted(set(views) | set(followers))
        return [{"t": t, "views": int(views[t]) if views.get(t) is not None else None,  # SUM comes back as Decimal
                 "followers": followers.get(t)} for t in times]

    async def history(self, s: AsyncSession, post_id: uuid.UUID) -> list[dict[str, Any]]:
        snaps = (await s.execute(select(SocialSnapshot).where(SocialSnapshot.post_id == post_id)
                                 .order_by(SocialSnapshot.taken_at))).scalars().all()
        return [{"taken_at": x.taken_at, "views": x.views, "reach": x.reach, "likes": x.likes,
                 "comments": x.comments, "shares": x.shares, "saves": x.saves, "avg_watch_s": x.avg_watch_s}
                for x in snaps]


def now_utc() -> datetime:
    return datetime.now(UTC)
