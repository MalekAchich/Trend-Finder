"""Our own channels: connect (public handle, Instagram token, TikTok login), sync, numbers and the report (Plan 9).
No token or app secret is ever part of a response."""
import re
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from tf_agent.socials.report import channel_report
from tf_agent.socials.types import ReadError
from tf_agent.tools.normalize import norm_handle
from tf_agent.tools.types import CANONICAL_ID_PATTERN
from tf_backend.api.deps import ctx, require_client_header
from tf_backend.app_context import AppContext
from tf_backend.socials import WINDOWS, Socials, now_utc
from tf_db.models import Character, SocialChannel, SocialPost

router = APIRouter(prefix="/socials", tags=["socials"])
INSTAGRAM_SCOPES = ["instagram_business_basic", "instagram_business_manage_insights"]
LONG_LIVED_S = 60 * 24 * 3600


def _service(c: AppContext) -> Socials:
    if c.socials is None:
        raise HTTPException(503, "socials aren't available in this process")
    return c.socials


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, f"unknown {what}") from None


def clean_handle(raw: str) -> str:
    """'@Made_Up', 'https://www.tiktok.com/@made_up?lang=en' or 'instagram.com/made_up/' -> 'made_up'."""
    text = raw.strip()
    m = re.search(r"(?:tiktok\.com/@|instagram\.com/)([A-Za-z0-9_.]+)", text)
    handle = norm_handle(m.group(1) if m else text.lstrip("@").split("/")[0])
    if not re.fullmatch(r"[a-z0-9_.]{1,30}", handle):
        raise HTTPException(422, "that doesn't look like an Instagram or TikTok username")
    return handle


async def _channel(c: AppContext, raw: str) -> SocialChannel:
    async with c.sessionmaker() as s:
        ch = await s.get(SocialChannel, _uuid(raw, "channel"))
    if ch is None:
        raise HTTPException(404, "unknown channel")
    return ch


@router.get("")
async def overview(c: AppContext = Depends(ctx)) -> dict[str, Any]:
    svc, now = _service(c), now_utc()
    async with c.sessionmaker() as s:
        chars = (await s.execute(select(Character).order_by(Character.name))).scalars().all()
        channels = (await s.execute(select(SocialChannel).order_by(SocialChannel.platform))).scalars().all()
        out = []
        for ch in chars:
            mine = [x for x in channels if x.character_id == ch.id]
            out.append({"slug": ch.slug, "name": ch.name,
                        "channels": [await svc.channel_out(s, x, now) for x in mine]})
    return {"characters": out, "tiktok_app": svc.creds.describe_socials()["tiktok_app"],
            "tiktok_redirect_uri": svc.redirect_uri}


class ChannelIn(BaseModel):
    platform: Literal["instagram", "tiktok"]
    handle: str = Field(min_length=1, max_length=200)
    character: str = Field(min_length=1, max_length=64)


@router.post("/channels", dependencies=[Depends(require_client_header)])
async def add_channel(body: ChannelIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    svc, handle = _service(c), clean_handle(body.handle)
    async with c.sessionmaker() as s:
        char = (await s.execute(select(Character).where(Character.slug == body.character))).scalar_one_or_none()
        if char is None:
            raise HTTPException(404, f"unknown character {body.character!r}")
        if (await s.execute(select(SocialChannel.id).where(SocialChannel.platform == body.platform,
                                                           SocialChannel.handle == handle))).first():
            raise HTTPException(409, f"@{handle} is already connected")
        ch = SocialChannel(character_id=char.id, platform=body.platform, handle=handle)
        s.add(ch)
        await s.commit()
    result = await svc.sync_channel(ch.id)  # the first numbers right away (public pages)
    async with c.sessionmaker() as s:
        return {**await svc.channel_out(s, await s.get(SocialChannel, ch.id), now_utc()), "synced": result.posts}


@router.delete("/channels/{channel_id}", dependencies=[Depends(require_client_header)])
async def remove_channel(channel_id: str, c: AppContext = Depends(ctx)) -> dict[str, bool]:
    svc, ch = _service(c), await _channel(c, channel_id)
    svc.creds.delete_social_token(str(ch.id))
    async with c.sessionmaker() as s:
        await s.delete(await s.get(SocialChannel, ch.id))
        await s.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/disconnect", dependencies=[Depends(require_client_header)])
async def disconnect(channel_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    """Back to public numbers: the token is deleted, the history kept."""
    svc, ch = _service(c), await _channel(c, channel_id)
    svc.creds.delete_social_token(str(ch.id))
    async with c.sessionmaker() as s:
        row = await s.get(SocialChannel, ch.id)
        row.mode, row.scopes, row.connected_at, row.last_error = "public", [], None, None
        await s.commit()
        return await svc.channel_out(s, row, now_utc())


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=1000)


@router.post("/channels/{channel_id}/instagram-token", dependencies=[Depends(require_client_header)])
async def instagram_token(channel_id: str, body: TokenIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    svc, ch = _service(c), await _channel(c, channel_id)
    if ch.platform != "instagram":
        raise HTTPException(422, "that's not an Instagram channel")
    token = body.token.strip()
    try:
        me = await svc.instagram.me(token)
    except ReadError as e:
        raise HTTPException(422, f"Instagram didn't accept this token: {e.message}") from None
    if str(me.get("username") or "").lower() != ch.handle:
        raise HTTPException(422, f"this token belongs to @{me.get('username')}, not @{ch.handle}")
    expires_at = time.time() + LONG_LIVED_S
    try:  # a refresh tells the real expiry (it needs a token at least a day old: otherwise assume 60 days)
        fresh = await svc.instagram.refresh(token)
        token, expires_at = fresh["access_token"], time.time() + float(fresh.get("expires_in") or LONG_LIVED_S)
    except (ReadError, KeyError):
        pass
    svc.creds.save_social_token(str(ch.id), {"access_token": token, "expires_at": expires_at,
                                             "scopes": INSTAGRAM_SCOPES})
    async with c.sessionmaker() as s:
        row = await s.get(SocialChannel, ch.id)
        row.mode, row.scopes, row.connected_at, row.last_error = "api", INSTAGRAM_SCOPES, now_utc(), None
        await s.commit()
    await svc.sync_channel(ch.id)
    async with c.sessionmaker() as s:
        return await svc.channel_out(s, await s.get(SocialChannel, ch.id), now_utc())


class TikTokAppIn(BaseModel):
    client_key: str = Field(min_length=4, max_length=200)
    client_secret: str = Field(min_length=4, max_length=500)


@router.put("/tiktok-app", dependencies=[Depends(require_client_header)])
async def tiktok_app(body: TikTokAppIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    svc = _service(c)
    svc.creds.save_tiktok_app(body.client_key, body.client_secret)
    return svc.creds.describe_socials()["tiktok_app"]


@router.get("/tiktok/connect")
async def tiktok_connect(channel: str, c: AppContext = Depends(ctx)) -> RedirectResponse:
    """Opens TikTok's consent page (the browser follows this link; it comes back to the callback)."""
    svc, ch = _service(c), await _channel(c, channel)
    app = svc.creds.tiktok_app()
    if ch.platform != "tiktok":
        raise HTTPException(422, "that's not a TikTok channel")
    if app is None:
        raise HTTPException(409, "add your TikTok app's client key and secret first")
    state, verifier = svc.begin_tiktok(ch.id)
    return RedirectResponse(svc.tiktok.authorize_url(app["client_key"], svc.redirect_uri, state, verifier), 302)


def _back(**params: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(f"/socials?{urlencode(params)}", 302)


@router.get("/tiktok/callback")
async def tiktok_callback(state: str = "", code: str = "", error: str = "", error_description: str = "",
                          c: AppContext = Depends(ctx)) -> RedirectResponse:
    svc = _service(c)
    pending = svc.finish_tiktok(state)
    if pending is None:
        return _back(error="That TikTok login link was already used or has expired. Press Connect again.")
    if error or not code:
        return _back(error=f"TikTok didn't connect: {error_description or error or 'no code came back'}")
    app = svc.creds.tiktok_app()
    if app is None:
        return _back(error="Your TikTok app's keys are missing. Add them, then press Connect again.")
    try:
        tok = await svc.tiktok.exchange(app, code, pending.verifier, svc.redirect_uri)
    except ReadError as e:
        return _back(error=f"TikTok didn't connect: {e.message}")
    scopes = [x for x in str(tok.get("scope") or "").split(",") if x]
    now = time.time()
    svc.creds.save_social_token(str(pending.channel_id), {
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"), "open_id": tok.get("open_id"),
        "expires_at": now + float(tok.get("expires_in") or 0),
        "refresh_expires_at": now + float(tok.get("refresh_expires_in") or 0), "scopes": scopes})
    async with c.sessionmaker() as s:
        row = await s.get(SocialChannel, pending.channel_id)
        if row is None:
            return _back(error="That channel was removed while connecting.")
        row.mode, row.scopes, row.connected_at, row.last_error = "api", scopes, now_utc(), None
        await s.commit()
    await svc.sync_channel(pending.channel_id)
    return _back(connected="tiktok")


@router.post("/channels/{channel_id}/sync", dependencies=[Depends(require_client_header)])
async def sync_now(channel_id: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    svc, ch = _service(c), await _channel(c, channel_id)
    result = await svc.sync_channel(ch.id)
    async with c.sessionmaker() as s:
        return {**await svc.channel_out(s, await s.get(SocialChannel, ch.id), now_utc()),
                "synced": result.posts, "source": result.source}


@router.get("/channels/{channel_id}/posts")
async def posts(channel_id: str, window: Literal["7d", "30d", "all"] = "30d",
                c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    svc, ch = _service(c), await _channel(c, channel_id)
    assert window in WINDOWS
    async with c.sessionmaker() as s:
        return await svc.posts_out(s, ch, window, now_utc())


@router.get("/posts/{post_id}/history")
async def history(post_id: str, c: AppContext = Depends(ctx)) -> list[dict[str, Any]]:
    svc, pid = _service(c), _uuid(post_id, "post")
    async with c.sessionmaker() as s:
        if await s.get(SocialPost, pid) is None:
            raise HTTPException(404, "unknown post")
        return await svc.history(s, pid)


class PostIn(BaseModel):
    inspired_by: str | None = Field(None, max_length=64)


@router.patch("/posts/{post_id}", dependencies=[Depends(require_client_header)])
async def link_post(post_id: str, body: PostIn, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    pid = _uuid(post_id, "post")
    if body.inspired_by is not None and not re.match(CANONICAL_ID_PATTERN, body.inspired_by):
        raise HTTPException(422, "pick a found or manually chosen video")
    async with c.sessionmaker() as s:
        row = await s.get(SocialPost, pid)
        if row is None:
            raise HTTPException(404, "unknown post")
        row.inspired_by = body.inspired_by
        await s.commit()
    return {"id": post_id, "inspired_by": body.inspired_by}


@router.get("/report")
async def report(character: str, c: AppContext = Depends(ctx)) -> dict[str, Any]:
    async with c.sessionmaker() as s:
        char = (await s.execute(select(Character).where(Character.slug == character))).scalar_one_or_none()
    if char is None:
        raise HTTPException(404, f"unknown character {character!r}")
    return {"text": await channel_report(c.sessionmaker, char.id, now_utc())}
