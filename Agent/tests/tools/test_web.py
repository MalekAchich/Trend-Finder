import httpx
import pytest

from tf_agent.tools.types import ToolFailure
from tf_agent.tools.web import SearxClient, web_fetch


def mk(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport)


async def public(host):
    return ["93.184.216.34"]


def hit(i):
    return {"title": f"T{i}", "url": f"https://www.tiktok.com/@a/video/{1000 + i}", "content": f"cap {i}",
            "engine": "google cse", "publishedDate": None}


async def test_search_maps_results_and_unresponsive_engines():
    def handler(r):
        assert r.url.path == "/search" and r.url.params["format"] == "json"
        assert r.url.params["q"] == "site:tiktok.com deadpan"
        return httpx.Response(200, json={"results": [hit(1)], "unresponsive_engines": [["brave", "too many"]]})

    res = await SearxClient("http://searx", mk(handler)).search("site:tiktok.com deadpan")
    assert [(h.title, h.url, h.snippet, h.engine) for h in res.results] == [
        ("T1", "https://www.tiktok.com/@a/video/1001", "cap 1", "google cse")]
    assert res.unresponsive == ["brave"]


async def test_search_caps_results_and_passes_time_range():
    def handler(r):
        assert r.url.params["time_range"] == "week"
        return httpx.Response(200, json={"results": [hit(i) for i in range(25)], "unresponsive_engines": []})

    res = await SearxClient("http://searx", mk(handler)).search("x", max_results=10, time_range="week")
    assert len(res.results) == 10


async def test_zero_results_with_engines_down_is_platform_unavailable():
    payload = {"results": [], "unresponsive_engines": [["brave", "too many"], ["duckduckgo", "CAPTCHA"]]}
    with pytest.raises(ToolFailure) as ei:
        await SearxClient("http://searx", mk(lambda r: httpx.Response(200, json=payload))).search("x")
    assert ei.value.error.code == "platform_unavailable"


async def test_zero_results_with_healthy_engines_is_empty():
    payload = {"results": [], "unresponsive_engines": []}
    res = await SearxClient("http://searx", mk(lambda r: httpx.Response(200, json=payload))).search("x")
    assert res.results == []


@pytest.mark.parametrize("response", [httpx.Response(500), "network"])
async def test_search_failures_are_platform_unavailable(response):
    def handler(r):
        if response == "network":
            raise httpx.ConnectError("down", request=r)
        return response

    with pytest.raises(ToolFailure) as ei:
        await SearxClient("http://searx", mk(handler)).search("x")
    assert ei.value.error.code == "platform_unavailable"


@pytest.mark.parametrize("url", ["http://example.com/", "https://localhost/x", "https://127.0.0.1/",
                                 "https://10.0.0.5/", "https://[::1]/", "https://169.254.169.254/latest",
                                 "ftp://example.com/x"])
async def test_fetch_rejects_unsafe_urls(url):
    with pytest.raises(ToolFailure) as ei:
        await web_fetch(url, resolver=public, client_factory=mk(lambda r: httpx.Response(200, text="x")))
    assert ei.value.error.code == "invalid_input"


async def test_fetch_rejects_hostname_resolving_to_private_ip():
    async def private(host):
        return ["10.1.2.3"]

    with pytest.raises(ToolFailure, match="private"):
        await web_fetch("https://intranet.example/", resolver=private,
                        client_factory=mk(lambda r: httpx.Response(200, text="x")))


async def test_fetch_rejects_redirect_into_private_network():
    async def resolver(host):
        return ["10.0.0.9"] if host == "internal.example" else ["93.184.216.34"]

    def handler(r):
        return httpx.Response(302, headers={"location": "https://internal.example/admin"})

    with pytest.raises(ToolFailure, match="private"):
        await web_fetch("https://example.com/", resolver=resolver, client_factory=mk(handler))


async def test_fetch_extracts_title_text_and_links():
    html = """<html><head><title>Deadpan trend explained</title></head><body>
    <article><h1>Why deadpan works</h1><p>The deadpan dance trend took off because the contrast between a serious
    face and silly music is funny. Creators keep a straight face while doing absurd moves.</p>
    <p>See <a href="/tag/deadpan">the tag</a> and <a href="https://www.tiktok.com/@a/video/1">a video</a>.</p>
    </article></body></html>"""
    page = await web_fetch("https://news.example/post", resolver=public,
                           client_factory=mk(lambda r: httpx.Response(200, text=html,
                                                                     headers={"content-type": "text/html"})))
    assert page.title == "Deadpan trend explained"
    assert "straight face" in page.text
    assert "https://news.example/tag/deadpan" in page.links and "https://www.tiktok.com/@a/video/1" in page.links


async def test_fetch_enforces_size_cap():
    big = "a" * (5 * 1024 * 1024 + 10)
    with pytest.raises(ToolFailure, match="5 MB"):
        await web_fetch("https://example.com/", resolver=public,
                        client_factory=mk(lambda r: httpx.Response(200, text=big)))
