"""TikTok Login Kit for Desktop (PKCE, loopback redirect) + Display API: our own account's numbers. Read-only.
Scopes: user.info.basic, user.info.profile, user.info.stats, video.list. TikTok gives no watch time here."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from tf_agent.socials.types import ChannelRead, PostRead, ReadError

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = "user.info.basic,user.info.profile,user.info.stats,video.list"
USER_FIELDS = "open_id,username,display_name,avatar_url,follower_count,likes_count,video_count"
MAX_PAGES = 30  # 20 videos a page
VIDEO_FIELDS = "id,create_time,cover_image_url,share_url,video_description,duration,view_count,like_count," \
               "comment_count,share_count"


def challenge(verifier: str) -> str:
    """TikTok's desktop PKCE: the hex SHA-256 of the verifier (not base64url)."""
    return hashlib.sha256(verifier.encode()).hexdigest()


def _error(code: str, message: str) -> ReadError:
    if code in ("access_token_invalid", "invalid_grant", "invalid_token"):
        return ReadError("expired", f"TikTok: the connection expired ({message})")
    if code in ("scope_not_authorized", "scope_permission_missed", "permission_denied"):
        return ReadError("revoked", f"TikTok: a permission is missing ({message})")
    if code == "rate_limit_exceeded":
        return ReadError("rate_limited", "TikTok: too many requests, try later")
    return ReadError("unavailable", f"TikTok: {code} {message}".strip())


class TikTokApi:
    def __init__(self, client: httpx.AsyncClient | None = None, api: str = API, auth_url: str = AUTH_URL) -> None:
        self.client = client or httpx.AsyncClient(timeout=20)
        self.api, self.auth_url = api, auth_url

    def authorize_url(self, client_key: str, redirect: str, state: str, verifier: str) -> str:
        return self.auth_url + "?" + urlencode({
            "client_key": client_key, "scope": SCOPES, "response_type": "code", "redirect_uri": redirect,
            "state": state, "code_challenge": challenge(verifier), "code_challenge_method": "S256"})

    async def _token(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            r = await self.client.post(f"{self.api}/v2/oauth/token/", data=form,
                                       headers={"content-type": "application/x-www-form-urlencoded"})
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ReadError("unavailable", f"TikTok didn't answer ({type(e).__name__})") from None
        if not data.get("access_token"):
            raise _error(str(data.get("error") or "invalid_grant"), str(data.get("error_description") or ""))
        return data

    async def exchange(self, app: dict[str, str], code: str, verifier: str, redirect: str) -> dict[str, Any]:
        return await self._token({"client_key": app["client_key"], "client_secret": app["client_secret"], "code": code,
                                  "grant_type": "authorization_code", "redirect_uri": redirect,
                                  "code_verifier": verifier})

    async def refresh(self, app: dict[str, str], refresh_token: str) -> dict[str, Any]:
        return await self._token({"client_key": app["client_key"], "client_secret": app["client_secret"],
                                  "grant_type": "refresh_token", "refresh_token": refresh_token})

    async def _call(self, method: str, path: str, token: str, **kw: Any) -> dict[str, Any]:
        try:
            r = await self.client.request(method, f"{self.api}{path}", headers={"authorization": f"Bearer {token}"},
                                          **kw)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ReadError("unavailable", f"TikTok didn't answer ({type(e).__name__})") from None
        err = body.get("error") or {}
        if str(err.get("code") or "ok") != "ok":
            raise _error(str(err.get("code")), str(err.get("message") or ""))
        return body.get("data") or {}

    async def me(self, token: str) -> dict[str, Any]:
        """Who this token belongs to (username needs user.info.profile)."""
        return (await self._call("GET", "/v2/user/info/", token, params={"fields": "open_id,username,display_name"})
                ).get("user") or {}

    async def read(self, token: str, since: datetime) -> ChannelRead:
        user = (await self._call("GET", "/v2/user/info/", token, params={"fields": USER_FIELDS})).get("user") or {}
        out = ChannelRead(handle=str(user.get("username") or ""), followers=user.get("follower_count"),
                          total_likes=user.get("likes_count"), posts_count=user.get("video_count"))
        cursor: int | None = None
        for _ in range(MAX_PAGES):
            body: dict[str, Any] = {"max_count": 20} | ({"cursor": cursor} if cursor else {})
            data = await self._call("POST", "/v2/video/list/", token, params={"fields": VIDEO_FIELDS}, json=body)
            old = False
            for v in data.get("videos") or []:
                posted = datetime.fromtimestamp(v["create_time"], UTC) if v.get("create_time") else None
                if posted is not None and posted < since:
                    old = True
                    continue
                out.posts.append(PostRead(
                    platform_post_id=str(v["id"]), url=str(v.get("share_url") or ""),
                    caption=v.get("video_description") or None, posted_at=posted,
                    thumbnail_url=v.get("cover_image_url"),
                    duration_s=float(v["duration"]) if v.get("duration") else None, views=v.get("view_count"),
                    likes=v.get("like_count"), comments=v.get("comment_count"), shares=v.get("share_count")))
            if old or not data.get("has_more") or not data.get("cursor"):
                return out
            if data["cursor"] == cursor:
                return out  # a cursor that doesn't move would loop forever
            cursor = data["cursor"]
        return out
