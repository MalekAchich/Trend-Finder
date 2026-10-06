"""The built web app is served at / with deep links falling back to index.html; /api stays JSON."""
import httpx
import pytest

from tf_backend.main import create_app
from tf_backend.services import Services


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>")
    (d / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    return d


async def _get(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def test_deep_links_serve_index(dist):
    app = create_app(services=Services.__new__(Services), frontend_dist=dist)
    for path in ("/", "/runs/123/review", "/settings"):
        r = await _get(app, path)
        assert r.status_code == 200 and "id=root" in r.text, path
    assert (await _get(app, "/assets/app.js")).text == "console.log(1)"


async def test_api_and_missing_assets_are_not_swallowed(dist):
    app = create_app(services=Services.__new__(Services), frontend_dist=dist)
    r = await _get(app, "/api/does-not-exist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert (await _get(app, "/assets/missing.js")).status_code == 404
    r = await _get(app, "/..%2Fsecret.txt")
    assert "nope" not in r.text
