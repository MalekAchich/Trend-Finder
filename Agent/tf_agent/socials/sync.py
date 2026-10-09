"""Reads every one of our channels and keeps its numbers over time: one snapshot per post per sync (Plan 9).

API mode reads the official API, refreshing the token before it expires; when the token stops working the channel
says "reconnect" and that sync still saves the public numbers. Public mode reads the public pages only."""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.socials.types import ChannelRead, ReadError
from tf_agent.tools.normalize import canonical_id, norm_handle
from tf_db.models import SocialChannel, SocialChannelSnapshot, SocialPost, SocialSnapshot

log = logging.getLogger(__name__)
WINDOW = timedelta(days=90)  # posts this recent get a snapshot every sync; older ones keep their history
SNAPSHOT_GAP = timedelta(minutes=30)  # a second sync this soon updates the last reading instead of adding one
# refresh a token this long before it expires: Instagram's last 60 days; TikTok's a day and Google's an hour
REFRESH_BEFORE = {"instagram": timedelta(days=7), "tiktok": timedelta(minutes=10), "youtube": timedelta(minutes=10)}


class Reader(Protocol):
    async def read(self, token: str, since: datetime) -> ChannelRead: ...


class Public(Protocol):
    async def read(self, platform: str, handle: str, since: datetime) -> ChannelRead: ...


@dataclass
class SyncResult:
    posts: int
    new_posts: int
    error: str | None
    source: str | None


def post_key(platform_post_id: str, url: str) -> tuple[str, str | None]:
    """The same post read from the API and from the public page has one key: the id in its canonical URL."""
    cid = canonical_id(url) if url else None
    return (cid.split(":", 1)[1] if cid else platform_post_id), cid


class SocialSync:
    def __init__(self, sm: async_sessionmaker[AsyncSession], creds: Any, instagram: Any, tiktok: Any, public: Public,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 on_new_posts: Callable[[list[uuid.UUID]], Awaitable[None]] | None = None, youtube: Any = None) -> None:
        self._sm, self.creds, self.instagram, self.tiktok, self.public = sm, creds, instagram, tiktok, public
        self.youtube = youtube
        self._now, self.on_new_posts = clock, on_new_posts

    async def _token(self, ch: SocialChannel) -> str | None:
        tok = self.creds.social_token(str(ch.id))
        if not tok:
            return None
        now = self._now().timestamp()
        expires = float(tok.get("expires_at") or 0)
        if expires and expires - now < REFRESH_BEFORE.get(ch.platform, timedelta(minutes=10)).total_seconds():
            if ch.platform == "instagram":
                new = await self.instagram.refresh(tok["access_token"])
                tok = {**tok, "access_token": new["access_token"], "expires_at": now + float(new.get("expires_in") or 0)}
            else:  # TikTok and YouTube (Google): the app's keys and the refresh token
                api, app = ((self.tiktok, self.creds.tiktok_app()) if ch.platform == "tiktok"
                            else (self.youtube, self.creds.google_app()))
                if app is None or not tok.get("refresh_token"):
                    raise ReadError("expired", "the app keys or the refresh token are missing")
                new = await api.refresh(app, tok["refresh_token"])
                tok = {**tok, "access_token": new["access_token"], "refresh_token": new.get("refresh_token",
                                                                                            tok["refresh_token"]),
                       "expires_at": now + float(new.get("expires_in") or 0)}
            self.creds.save_social_token(str(ch.id), tok)
        return str(tok["access_token"])

    async def _read(self, ch: SocialChannel, since: datetime) -> tuple[ChannelRead | None, str | None]:
        problem: str | None = None
        if ch.mode == "api":
            try:
                token = await self._token(ch)
                if token is None:
                    raise ReadError("expired", "the connection's token is missing")
                api = {"instagram": self.instagram, "tiktok": self.tiktok, "youtube": self.youtube}[ch.platform]
                return await api.read(token, since), None
            except ReadError as e:
                problem = ("reconnect: " if e.code in ("expired", "revoked") else "") + e.message
                log.info("channel %s: official API unavailable, reading the public page (%s)", ch.handle, e.code)
        try:
            return await self.public.read(ch.platform, ch.handle, since), problem
        except ReadError as e:
            return None, problem or e.message

    async def sync_channel(self, channel_id: uuid.UUID) -> SyncResult:
        async with self._sm() as s:
            ch = await s.get(SocialChannel, channel_id)
        if ch is None:
            raise KeyError(channel_id)
        now = self._now()
        read, problem = await self._read(ch, now - WINDOW)
        if read is None:
            async with self._sm() as s:
                await s.execute(update(SocialChannel).where(SocialChannel.id == channel_id).values(last_error=problem))
                await s.commit()
            return SyncResult(0, 0, problem, None)
        new: list[uuid.UUID] = []
        async with self._sm() as s:
            for p in read.posts:
                key, cid = post_key(p.platform_post_id, p.url)
                row = (await s.execute(select(SocialPost).where(SocialPost.channel_id == channel_id,
                                                                SocialPost.platform_post_id == key))).scalar_one_or_none()
                if row is None:
                    row = SocialPost(channel_id=channel_id, platform_post_id=key, url=p.url)
                    s.add(row)
                    await s.flush()
                    new.append(row.id)
                # what a source knows fills in; nothing it lacks erases what another source gave
                for field in ("caption", "posted_at", "thumbnail_url", "duration_s", "audience"):
                    if getattr(p, field) is not None:
                        setattr(row, field, getattr(p, field))
                row.url, row.canonical_id = p.url or row.url, cid or row.canonical_id
                if row.posted_at is not None and row.posted_at < now - WINDOW:
                    continue
                last = (await s.execute(select(SocialSnapshot).where(SocialSnapshot.post_id == row.id)
                                        .order_by(SocialSnapshot.taken_at.desc()).limit(1))).scalar_one_or_none()
                numbers = dict(views=p.views, reach=p.reach, likes=p.likes, comments=p.comments, shares=p.shares,
                               saves=p.saves, avg_watch_s=p.avg_watch_s, total_watch_s=p.total_watch_s)
                if last is None or now - last.taken_at >= SNAPSHOT_GAP:
                    s.add(SocialSnapshot(post_id=row.id, taken_at=now, **numbers))
                else:  # "Sync now" soon after a read: the newest numbers replace that reading (one point per gap)
                    for k, v in numbers.items():
                        if v is not None:  # a public read never erases what the API gave (reach, watch time)
                            setattr(last, k, v)
                    last.taken_at = now
            last_ch = (await s.execute(select(SocialChannelSnapshot).where(
                SocialChannelSnapshot.channel_id == channel_id).order_by(SocialChannelSnapshot.taken_at.desc())
                .limit(1))).scalar_one_or_none()
            if last_ch is None or now - last_ch.taken_at >= SNAPSHOT_GAP:
                s.add(SocialChannelSnapshot(channel_id=channel_id, taken_at=now, followers=read.followers,
                                            total_likes=read.total_likes, posts=read.posts_count))
            else:
                last_ch.taken_at, last_ch.followers = now, read.followers
                last_ch.total_likes, last_ch.posts = read.total_likes, read.posts_count
            extra = {k: v for k, v in (("audience", read.audience), ("account_insights", read.account_insights))
                     if v is not None}  # public reads don't have them: what the API gave last stays
            # the official API reads the account itself: a renamed account brings its new username along
            renamed = norm_handle(read.handle) if read.source == "api" and read.handle else None
            if renamed and renamed != ch.handle and not (await s.execute(select(SocialChannel.id).where(
                    SocialChannel.platform == ch.platform, SocialChannel.handle == renamed))).first():
                extra["handle"] = renamed
            await s.execute(update(SocialChannel).where(SocialChannel.id == channel_id).values(
                last_synced_at=now, last_error=problem, **extra))
            await s.commit()
        if new and self.on_new_posts is not None:
            try:
                await self.on_new_posts(new)
            except Exception as e:  # studying new posts never costs the sync
                log.warning("studying new posts of %s failed: %s", ch.handle, e)
        return SyncResult(len(read.posts), len(new), problem, read.source)

    async def sync_all(self) -> dict[str, SyncResult | str]:
        async with self._sm() as s:
            ids = (await s.execute(select(SocialChannel.id, SocialChannel.handle))).all()
        out: dict[str, SyncResult | str] = {}
        for cid, handle in ids:
            try:
                out[handle] = await self.sync_channel(cid)
            except Exception as e:  # one channel never stops the others
                log.exception("sync of %s failed", handle)
                out[handle] = f"{type(e).__name__}: {e}"[:200]
        return out
