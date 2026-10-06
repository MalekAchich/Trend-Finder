"""Plan 4 final #10: a DNS-rebinding page reaches 127.0.0.1 under a foreign Host name; refuse it."""
import httpx

from tf_backend.main import create_app
from tf_backend.services import Services


async def _get(app, host):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}") as c:
        return await c.get("/api/health")


async def test_only_local_host_names_are_served():
    app = create_app(services=Services.__new__(Services))
    for host in ("127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
        assert (await _get(app, host)).status_code == 200, host
    assert (await _get(app, "attacker.example:8000")).status_code == 400


async def test_allow_remote_opens_the_host_check(monkeypatch):
    monkeypatch.setenv("TF_ALLOWED_HOSTS", "*")
    app = create_app(services=Services.__new__(Services))
    assert (await _get(app, "192.168.1.20:8000")).status_code == 200
