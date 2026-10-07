"""Hover previews: Instagram and X are relayed (their CDNs refuse other sites' pages); nothing is stored."""
import httpx
import pytest

from tf_backend.previews import Previews


def cdn(requests):
    def handler(r):
        requests.append((str(r.url), r.headers.get("range")))
        if "expired" in str(r.url):
            return httpx.Response(403)
        return httpx.Response(206, headers={"content-type": "video/mp4", "content-range": "bytes 0-3/10",
                                            "accept-ranges": "bytes"}, content=b"\x00\x01\x02\x03")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_relay_passes_ranges_and_reuses_the_resolved_link():
    seen, resolved = [], []

    async def resolve(page):
        resolved.append(page)
        return "https://cdn.invalid/v.mp4"

    p = Previews(resolve, client=cdn(seen))
    resp, body = await p.open("instagram:TESTREEL001", "bytes=0-3")
    assert resp.status_code == 206 and b"".join([c async for c in body]) == b"\x00\x01\x02\x03"
    await p.open("instagram:TESTREEL001", None)
    assert resolved == ["https://www.instagram.com/reel/TESTREEL001/"]  # resolved once, then reused
    assert seen[0] == ("https://cdn.invalid/v.mp4", "bytes=0-3")


async def test_an_expired_link_is_resolved_again():
    links = iter(["https://cdn.invalid/expired.mp4", "https://cdn.invalid/fresh.mp4"])

    async def resolve(page):
        return next(links)

    seen = []
    p = Previews(resolve, client=cdn(seen))
    resp, _ = await p.open("x:1000000000000000003", None)
    assert resp.status_code == 206 and seen[-1][0] == "https://cdn.invalid/fresh.mp4"


@pytest.mark.parametrize("cid", ["youtube:TestShort01", "instagram:../../etc", "nope"])
async def test_only_known_relayed_platforms(cid):
    from tf_backend.main import create_app
    from tf_backend.app_context import AppContext

    app = create_app(context=AppContext(sessionmaker=None, runs=None, learner=None, media_dir=None,
                                        characters_dir=None), frontend_dist=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        assert (await c.get(f"/api/preview/{cid}")).status_code == 404
