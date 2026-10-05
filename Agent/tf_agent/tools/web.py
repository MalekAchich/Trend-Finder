"""General web tools: SearXNG metasearch (D-14) and a safe readable-page fetcher (03-tools.md)."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from tf_agent.tools.types import ToolFailure

ClientFactory = Callable[[], httpx.AsyncClient]
Resolver = Callable[[str], Awaitable[list[str]]]
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 8000
MAX_LINKS = 30
MAX_REDIRECTS = 3
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HREF_RE = re.compile(r"""<a\s[^>]*href=["']([^"'#]+)["']""", re.I)
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    engine: str | None = None
    published: str | None = None


@dataclass
class SearchResponse:
    results: list[SearchHit] = field(default_factory=list)
    unresponsive: list[str] = field(default_factory=list)


class SearxClient:
    def __init__(self, base_url: str, client_factory: ClientFactory | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=20))

    async def search(self, query: str, max_results: int = 10, time_range: str | None = None) -> SearchResponse:
        params = {"q": query, "format": "json"}
        if time_range:
            params["time_range"] = time_range
        try:
            async with self._client_factory() as client:
                r = await client.get(f"{self.base_url}/search", params=params)
        except httpx.HTTPError as e:
            raise ToolFailure("platform_unavailable", f"search backend unreachable: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ToolFailure("platform_unavailable", f"search backend error ({r.status_code})")
        body = r.json()
        unresponsive = [str(e[0]) for e in body.get("unresponsive_engines") or [] if e]
        hits = [SearchHit(title=x.get("title") or "", url=x.get("url") or "", snippet=x.get("content") or "",
                          engine=x.get("engine"), published=x.get("publishedDate"))
                for x in body.get("results") or [] if x.get("url")]
        if not hits and unresponsive:
            raise ToolFailure("platform_unavailable",
                              f"no results and engines unresponsive: {', '.join(unresponsive)}", retry_after_s=300)
        return SearchResponse(results=hits[:max_results], unresponsive=unresponsive)


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    return addr.is_global and not addr.is_multicast


async def _check_url(url: str, resolver: Resolver) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ToolFailure("invalid_input", "only https:// URLs can be fetched")
    host = parsed.hostname
    if host == "localhost" or host.endswith(".localhost"):
        raise ToolFailure("invalid_input", "private or local address refused")
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            ips = await resolver(host)
        except OSError as e:
            raise ToolFailure("not_found", f"cannot resolve {host}") from e
    if not ips or not all(_is_public(ip) for ip in ips):
        raise ToolFailure("invalid_input", "private or local address refused")


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    text: str
    links: list[str]


async def web_fetch(url: str, *, resolver: Resolver = system_resolver,
                    client_factory: ClientFactory | None = None) -> FetchedPage:
    """Fetch a public HTTPS page (redirects re-checked, 5 MB cap) and return readable text and links."""
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=20, headers={"user-agent": _UA}))
    current = url
    async with factory() as client:
        for _ in range(MAX_REDIRECTS + 1):
            await _check_url(current, resolver)
            try:
                async with client.stream("GET", current, follow_redirects=False) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code == 404:
                        raise ToolFailure("not_found", f"page not found ({current})")
                    if r.status_code == 429:
                        raise ToolFailure("rate_limited", "site is rate limiting", retry_after_s=60)
                    if r.status_code >= 400:
                        raise ToolFailure("platform_unavailable", f"site error ({r.status_code})")
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_PAGE_BYTES:
                            raise ToolFailure("invalid_input", "page larger than 5 MB")
                    html = body.decode(r.encoding or "utf-8", errors="replace")
                    break
            except httpx.HTTPError as e:
                raise ToolFailure("platform_unavailable", f"fetch failed: {type(e).__name__}") from e
        else:
            raise ToolFailure("invalid_input", "too many redirects")
    title_m = _TITLE_RE.search(html)
    title = " ".join(title_m.group(1).split()) if title_m else ""
    text = trafilatura.extract(html, include_comments=False) or re.sub(r"<[^>]+>", " ", html)
    text = " ".join(text.split())[:MAX_TEXT_CHARS]
    links: list[str] = []
    for href in _HREF_RE.findall(html):
        absolute = urljoin(current, href.strip())
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
        if len(links) >= MAX_LINKS:
            break
    return FetchedPage(url=current, title=title, text=text, links=links)
