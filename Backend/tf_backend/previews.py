"""Hover previews for platforms whose embeds can't autoplay (Instagram, X): the video plays from its own CDN, relayed
through the app because those CDNs refuse requests that come from another site's page. Nothing is stored; only
links yt-dlp resolved for a known video are ever relayed (never a URL the browser sends)."""
from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from tf_agent.tools.normalize import canonical_url

RELAYED = {"instagram", "x"}
TTL_S = 600  # CDN links expire; resolve again after this
CHUNK = 64 * 1024


class Previews:
    def __init__(self, resolve: Callable[[str], Awaitable[str]], client: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._resolve, self._clock = resolve, clock
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20, read=60), follow_redirects=True)
        self._links: dict[str, tuple[str, float]] = {}

    async def link(self, cid: str) -> str:
        cached = self._links.get(cid)
        if cached and cached[1] > self._clock():
            return cached[0]
        direct = await self._resolve(canonical_url(cid))
        self._links[cid] = (direct, self._clock() + TTL_S)
        return direct

    async def open(self, cid: str, range_header: str | None) -> tuple[httpx.Response, AsyncIterator[bytes]]:
        direct = await self.link(cid)
        req = self._client.build_request("GET", direct, headers={"range": range_header} if range_header else {})
        resp = await self._client.send(req, stream=True)
        if resp.status_code >= 400:  # an expired link: resolve once more
            await resp.aclose()
            self._links.pop(cid, None)
            req = self._client.build_request("GET", await self.link(cid),
                                             headers={"range": range_header} if range_header else {})
            resp = await self._client.send(req, stream=True)

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes(CHUNK):
                    yield chunk
            finally:
                await resp.aclose()

        return resp, body()

    async def close(self) -> None:
        await self._client.aclose()
