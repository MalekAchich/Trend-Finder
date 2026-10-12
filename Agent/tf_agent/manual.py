"""The owner's manually chosen videos: a permanent list where each video is a reference (intel the agents study),
a target for one character (studied in that character's next run for how to make it), or both. The owner picked them,
so they're never scored or judged."""
from __future__ import annotations

import io
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tf_agent.orchestrator.found import thumb_url
from tf_agent.orchestrator.run import InputError
from tf_agent.pipeline.media import THUMB_MAX_BYTES, THUMB_WIDTH
from tf_agent.tools.normalize import canonical_id, platform_of
from tf_agent.tools.types import ToolFailure, VideoItem
from tf_db.models import Character, Finding, FindingScore, ManualVideo, ReferenceStudy, Target, Video

MAX_ADD = 100
UNSET: Any = object()
GetVideo = Callable[..., Awaitable[VideoItem]]  # (url, fresh=False)
FetchImage = Callable[[str], Awaitable[bytes]]


async def _http_image(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content


def small_jpeg(data: bytes) -> bytes:
    """360 px wide, re-encoded until it fits 40 KB (the same budget as found videos' thumbnails)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im = im.resize((THUMB_WIDTH, max(1, round(im.height * THUMB_WIDTH / im.width))))
        for quality in (82, 72, 62, 52, 42, 32):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            if buf.tell() <= THUMB_MAX_BYTES:
                break
        return buf.getvalue()


@dataclass(frozen=True)
class Reference:
    id: uuid.UUID
    url: str
    study: dict[str, Any] | None  # None: not studied for this character yet


class ManualVideos:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], get_video: GetVideo | None = None,
                 thumbs_dir: Path | None = None, fetch_image: FetchImage = _http_image) -> None:
        self._sm, self._get_video, self._thumbs, self._fetch = sessionmaker, get_video, thumbs_dir, fetch_image

    async def _character_id(self, s: AsyncSession, slug: str) -> uuid.UUID:
        cid = (await s.execute(select(Character.id).where(Character.slug == slug))).scalar_one_or_none()
        if cid is None:
            raise InputError(f"unknown character: {slug}")
        return cid

    async def _sync_target(self, s: AsyncSession, row: ManualVideo) -> None:
        """A pending `targets` row follows the video's target role (used ones stay as history)."""
        if row.canonical_id is None:
            return
        await s.execute(delete(Target).where(Target.canonical_id == row.canonical_id, Target.status == "pending",
                                             Target.character_id != row.target_character_id
                                             if row.target_character_id else True))
        if row.target_character_id is not None:
            await s.execute(pg_insert(Target).values(
                character_id=row.target_character_id, url=row.url, canonical_id=row.canonical_id, status="pending")
                .on_conflict_do_nothing(index_elements=[Target.character_id, Target.canonical_id]))

    # ---- adding ----
    async def add(self, urls: list[str], *, reference: bool, target: str | None = None,
                  reference_for: str | None = None) -> list[uuid.UUID]:
        """Saves the links (all or none). Returns the videos that need checking (new ones). A reference is
        `reference_for` one character's, or every character's without one (D-60)."""
        clean = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
        if not clean:
            return []
        if len(clean) > MAX_ADD:
            raise InputError(f"at most {MAX_ADD} links at a time")
        bad = [u for u in clean if platform_of(u) is None or (canonical_id(u) is None and "vm.tiktok" not in u
                                                                and "vt.tiktok" not in u)]
        if bad:
            raise InputError("not a TikTok, Instagram, YouTube or X video link: " + ", ".join(bad[:5]))
        if not reference and target is None:
            raise InputError("a video must be a reference, a target, or both")
        new: list[uuid.UUID] = []
        async with self._sm() as s:
            char_id = await self._character_id(s, target) if target else None
            ref_id = await self._character_id(s, reference_for) if reference and reference_for else None
            for url in clean:
                cid = canonical_id(url)
                q = select(ManualVideo).where(ManualVideo.canonical_id == cid if cid else ManualVideo.url == url)
                row = (await s.execute(q)).scalar_one_or_none()
                if row is None:
                    row = ManualVideo(url=url, canonical_id=cid, platform=platform_of(url), is_reference=reference,
                                      reference_character_id=ref_id, target_character_id=char_id, status="checking")
                    s.add(row)
                    await s.flush()
                    new.append(row.id)
                else:
                    if reference:
                        row.reference_character_id = _merged_owner(row, ref_id)
                    row.is_reference = row.is_reference or reference
                    if char_id is not None:
                        row.target_character_id = char_id
                await self._sync_target(s, row)
            await s.commit()
        return new

    async def check(self, video_id: uuid.UUID, fresh: bool = False) -> None:
        """Looks the video up (metadata with the connected accounts) and keeps a small thumbnail. `fresh`: skip
        cached metadata (a "check again" means look now)."""
        assert self._get_video is not None and self._thumbs is not None
        async with self._sm() as s:
            row = await s.get(ManualVideo, video_id)
            url = row.url if row else None
        if url is None:
            return
        try:
            item = await (self._get_video(url, fresh=True) if fresh else self._get_video(url))
        except ToolFailure as e:
            await self._update(video_id, status="problem", problem=e.error.message)
            return
        except Exception as e:  # a lookup must never take the list down
            await self._update(video_id, status="problem", problem=f"{type(e).__name__}: {e}"[:300])
            return
        if item.media_access == "login_required":
            await self._update(video_id, canonical_id=item.canonical_id, status="problem",
                               problem=f"needs a connected {item.platform} account to be read "
                                       "(Settings, Accounts & keys), then check it again")
            return
        thumb = None
        if item.thumbnail_url:
            try:
                out = self._thumbs / f"manual-{item.canonical_id.replace(':', '_')}.jpg"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(small_jpeg(await self._fetch(item.thumbnail_url)))
                thumb = str(out)
            except Exception:  # no thumbnail is fine: the card shows the platform instead
                thumb = None
        async with self._sm() as s:
            meta = dict(platform=item.platform, url=item.url, creator_handle=item.creator.handle,
                        creator_followers=item.creator.followers, caption=item.caption, hashtags=item.hashtags,
                        sound_id=item.sound.id, sound_title=item.sound.title, posted_at=item.posted_at,
                        duration_s=item.duration_s, metrics=item.metrics.model_dump(), media_access=item.media_access)
            await s.execute(pg_insert(Video).values(canonical_id=item.canonical_id, **meta)  # a re-check refreshes it
                            .on_conflict_do_update(index_elements=[Video.canonical_id], set_=meta))
            row = await s.get(ManualVideo, video_id)
            dup = (await s.execute(select(ManualVideo).where(ManualVideo.canonical_id == item.canonical_id,
                                                             ManualVideo.id != video_id))).scalar_one_or_none()
            if dup is not None:  # a short link that turned out to be a video already in the list: merge into it
                if row.is_reference:
                    dup.reference_character_id = _merged_owner(dup, row.reference_character_id)
                dup.is_reference = dup.is_reference or row.is_reference
                dup.target_character_id = row.target_character_id or dup.target_character_id
                await s.delete(row)
                await self._sync_target(s, dup)
            else:
                row.canonical_id, row.platform, row.status, row.problem = item.canonical_id, item.platform, "ready", None
                row.thumbnail_path = thumb or row.thumbnail_path
                await self._sync_target(s, row)
            await s.commit()

    async def _update(self, video_id: uuid.UUID, **values: Any) -> None:
        async with self._sm() as s:
            await s.execute(update(ManualVideo).where(ManualVideo.id == video_id).values(**values))
            await s.commit()

    # ---- editing ----
    async def set_roles(self, video_id: uuid.UUID, *, is_reference: bool | None = None, target: Any = UNSET,
                        reference_for: Any = UNSET) -> None:
        async with self._sm() as s:
            row = await s.get(ManualVideo, video_id)
            if row is None:
                raise KeyError(video_id)
            if is_reference is not None:
                row.is_reference = is_reference
            if target is not UNSET:
                row.target_character_id = await self._character_id(s, target) if target else None
            if reference_for is not UNSET:
                row.reference_character_id = await self._character_id(s, reference_for) if reference_for else None
            if not row.is_reference and row.target_character_id is None:
                raise InputError("a video must be a reference, a target, or both (remove it instead)")
            await self._sync_target(s, row)
            await s.commit()

    async def remove(self, video_id: uuid.UUID) -> None:
        async with self._sm() as s:
            row = await s.get(ManualVideo, video_id)
            if row is None:
                raise KeyError(video_id)
            if row.canonical_id:
                await s.execute(delete(Target).where(Target.canonical_id == row.canonical_id,
                                                     Target.status == "pending"))
            thumb = row.thumbnail_path
            await s.delete(row)
            await s.commit()
        if thumb:
            Path(thumb).unlink(missing_ok=True)

    # ---- reading ----
    async def references_for(self, character_id: uuid.UUID) -> list[Reference]:
        """Every usable reference of this character (its own and every character's), with this character's study
        when it exists (studied once, then reused)."""
        async with self._sm() as s:
            rows = (await s.execute(
                select(ManualVideo, ReferenceStudy.study).outerjoin(
                    ReferenceStudy, (ReferenceStudy.manual_video_id == ManualVideo.id)
                    & (ReferenceStudy.character_id == character_id))
                .where(ManualVideo.is_reference, ManualVideo.status != "problem", for_character(character_id))
                .order_by(ManualVideo.created_at))).all()
        return [Reference(m.id, m.url, study) for m, study in rows]

    async def save_study(self, video_id: uuid.UUID, character_id: uuid.UUID, study: dict[str, Any],
                         provider: str | None) -> None:
        async with self._sm() as s:
            stmt = pg_insert(ReferenceStudy).values(manual_video_id=video_id, character_id=character_id, study=study,
                                                    provider=provider)
            await s.execute(stmt.on_conflict_do_update(
                index_elements=[ReferenceStudy.manual_video_id, ReferenceStudy.character_id],
                set_={"study": study, "provider": provider}))
            await s.commit()

    async def list(self, character: str | None = None) -> list[dict[str, Any]]:
        async with self._sm() as s:
            char_id = await self._character_id(s, character) if character else None
            rows = (await s.execute(select(ManualVideo, Video, Character)
                                    .outerjoin(Video, Video.canonical_id == ManualVideo.canonical_id)
                                    .outerjoin(Character, Character.id == ManualVideo.target_character_id)
                                    .order_by(ManualVideo.created_at.desc()))).all()
            names = {c.id: c for c in (await s.execute(select(Character))).scalars()}
            ids = [m.id for m, _, _ in rows]
            studies: dict[uuid.UUID, dict[str, Any]] = {}
            q = select(ReferenceStudy).where(ReferenceStudy.manual_video_id.in_(ids)).order_by(
                ReferenceStudy.created_at)
            for st in (await s.execute(q)).scalars():
                if char_id is None or st.character_id == char_id:
                    studies[st.manual_video_id] = st.study  # latest wins
            cids = [m.canonical_id for m, _, _ in rows if m.canonical_id and m.target_character_id]
            scored = {}
            if cids:
                q2 = (select(Target, FindingScore.adaptation_idea).outerjoin(
                    Finding, (Finding.run_id == Target.run_id) & (Finding.canonical_id == Target.canonical_id))
                    .outerjoin(FindingScore, FindingScore.finding_id == Finding.id)
                    .where(Target.canonical_id.in_(cids)))
                for t, idea in (await s.execute(q2)).all():
                    scored[(t.character_id, t.canonical_id)] = (t, idea)
        out = []
        for m, v, ch in rows:
            target = None
            if ch is not None:
                t, idea = scored.get((ch.id, m.canonical_id), (None, None))
                target = {"slug": ch.slug, "name": ch.name,
                          "state": "analysed" if t is not None and t.status == "used" else "waiting",
                          "run_id": str(t.run_id) if t is not None and t.run_id else None, "adaptation": idea}
            out.append({
                "id": str(m.id), "url": m.url, "canonical_id": m.canonical_id, "platform": m.platform,
                "platform_id": m.canonical_id.split(":", 1)[1] if m.canonical_id else None,
                "thumbnail_url": thumb_url(m.thumbnail_path), "status": m.status, "problem": m.problem,
                "creator": v.creator_handle if v else None, "caption": v.caption if v else None,
                "views": (v.metrics or {}).get("views") if v else None,
                "likes": (v.metrics or {}).get("likes") if v else None, "duration_s": v.duration_s if v else None,
                "posted_at": v.posted_at.isoformat() if v and v.posted_at else None,
                "is_reference": m.is_reference, "target": target,
                "reference_for": ({"slug": names[m.reference_character_id].slug,
                                   "name": names[m.reference_character_id].name}
                                  if m.is_reference and m.reference_character_id in names else None), "study": _no_verdict(studies.get(m.id)),
                "added_at": m.created_at.isoformat() if m.created_at else None,
            })
        return out


def for_character(character_id: uuid.UUID) -> Any:
    """References that count for this character: its own and every character's."""
    return or_(ManualVideo.reference_character_id.is_(None), ManualVideo.reference_character_id == character_id)


def _merged_owner(row: ManualVideo, wanted: uuid.UUID | None) -> uuid.UUID | None:
    """Whose reference a video is once it's added again as one: the same character's stays it; wanted by a
    second character (or by every character), it becomes every character's."""
    if not row.is_reference:
        return wanted
    return row.reference_character_id if row.reference_character_id == wanted else None


def _no_verdict(study: dict[str, Any] | None) -> dict[str, Any] | None:
    """Studies made before the owner's picks stopped being judged still hold a rating: never shown."""
    if study is None:
        return None
    return {k: v for k, v in study.items() if k not in ("fit_score", "fit_for_character")}
