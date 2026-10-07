"""Real-browser access for platforms that only answer a browser: scraping-account logins and JSON capture.

`connect()` opens a visible Chromium window on the platform's login page; the owner logs in by hand (2FA and
checkpoints included) and the session is saved, never the password. `capture_json()` opens a page headless with that
saved session and keeps the JSON responses the page itself fetches (search results, trend lists), which is far
steadier than parsing HTML.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from tf_agent.credentials import SESSIONS, Credentials, SessionSpec
from tf_agent.tools.types import ToolFailure

log = logging.getLogger(__name__)
LOGIN_TIMEOUT_S = 600
LOGIN_URL_RE = re.compile(r"/login|/accounts/login|/i/flow/login|/signup", re.IGNORECASE)


class LoginCancelled(Exception):
    pass


class CookieJar(Protocol):
    async def cookies(self) -> list[dict[str, Any]]: ...


async def wait_for_login(jar: CookieJar, spec: SessionSpec, timeout_s: float, closed: Callable[[], bool],
                         poll_s: float = 1.5, clock: Callable[[], float] = time.monotonic) -> None:
    """Returns once the platform's session cookie is set; raises when the window closes or time runs out."""
    deadline = clock() + timeout_s
    while True:
        if closed():
            raise LoginCancelled("the login window was closed before logging in")
        try:
            cookies = await jar.cookies()
        except Exception as e:  # the window was closed between the check and the read
            raise LoginCancelled("the login window was closed before logging in") from e
        if any(c.get("name") == spec.cookie and c.get("value") and spec.domain in str(c.get("domain"))
               for c in cookies):
            return
        if clock() >= deadline:
            raise TimeoutError(f"no login after {int(timeout_s // 60)} minutes")
        await asyncio.sleep(poll_s)


class StateSource(Protocol):
    async def storage_state(self) -> dict[str, Any]: ...


async def wait_for_close(ctx: StateSource, timeout_s: float, closed: Callable[[], bool], poll_s: float = 1.5,
                         clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """For logins without a known session cookie: keep the latest session while the owner works, and return it when
    they close the window (once closed it can no longer be read)."""
    deadline, last = clock() + timeout_s, {"cookies": [], "origins": []}
    while not closed():
        try:
            last = await ctx.storage_state()
        except Exception:  # closing between the check and the read
            break
        if clock() >= deadline:
            raise TimeoutError(f"the window stayed open for {int(timeout_s // 60)} minutes")
        await asyncio.sleep(poll_s)
    return last


class BrowserSessions:
    def __init__(self, creds: Credentials, max_pages: int = 2) -> None:
        self.creds = creds
        self._pw: Any = None
        self._browser: Any = None
        self._start = asyncio.Lock()
        self._pages = asyncio.Semaphore(max_pages)
        self.connecting: dict[str, dict[str, str]] = {}  # platform -> {state: waiting|connected|failed, message}

    async def _playwright(self) -> Any:
        async with self._start:
            if self._pw is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
            return self._pw

    async def _headless(self) -> Any:
        pw = await self._playwright()
        async with self._start:
            if self._browser is None or not self._browser.is_connected():
                # the full Chromium in its new headless mode: closer to a real browser than the headless shell
                self._browser = await pw.chromium.launch(headless=True, channel="chromium")
            return self._browser

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None

    # ---- logins ----
    async def connect(self, platform: str, timeout_s: float = LOGIN_TIMEOUT_S) -> None:
        spec = SESSIONS[platform]
        self.connecting[platform] = {"state": "waiting", "message": (
            "Log in in the browser window that just opened." if spec.cookie else
            "Log in in the browser window that just opened, then close the window.")}
        try:
            pw = await self._playwright()
            browser = await pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            try:
                ctx = await browser.new_context(viewport=None, locale="en-US")
                page = await ctx.new_page()
                await page.goto(spec.login_url)
                if spec.cookie is None:
                    state = await wait_for_close(ctx, timeout_s, closed=lambda: not browser.is_connected() or page.is_closed())
                    if not any(spec.domain in str(c.get("domain")) for c in state.get("cookies") or []):
                        raise LoginCancelled("the window was closed before anything was saved")
                    self.creds.save_session(platform, state)
                else:
                    await wait_for_login(ctx, spec, timeout_s,
                                         closed=lambda: not browser.is_connected() or page.is_closed())
                    await asyncio.sleep(3)  # let the site finish setting its cookies
                    self.creds.save_session(platform, await ctx.storage_state())
            finally:
                if browser.is_connected():
                    await browser.close()
        except (LoginCancelled, TimeoutError) as e:
            self.connecting[platform] = {"state": "failed", "message": str(e)}
            return
        except Exception as e:  # the window must never take the server down with it
            log.warning("connect %s failed: %s", platform, e)
            self.connecting[platform] = {"state": "failed", "message": f"{type(e).__name__}: {str(e)[:160]}"}
            return
        self.connecting[platform] = {"state": "connected", "message": "Connected."}

    # ---- capture ----
    async def capture_json(self, platform: str, url: str, pattern: re.Pattern[str], *, scrolls: int = 2,
                           settle_ms: int = 2500, need_session: bool = False,
                           blocked: Callable[[str, str], bool] | None = None,
                           follow: Callable[[list[str]], list[str]] | None = None,
                           follow_headers: dict[str, str] | None = None) -> list[Any]:
        """JSON bodies of the page's own requests whose URL matches `pattern`. `follow` maps the matched request URLs
        to more URLs fetched from inside the page (same cookies and origin), e.g. the same API with other filters."""
        has = platform in SESSIONS and self.creds.has_session(platform)
        if need_session and not has:
            raise ToolFailure("login_required", f"{platform}: no scraping account connected "
                                                "(Settings, Accounts & keys)")
        state = str(self.creds.state_file(platform)) if has else None
        async with self._pages:
            browser = await self._headless()
            ctx = await browser.new_context(storage_state=state, locale="en-US",
                                            viewport={"width": 1280, "height": 900})
            try:
                page = await ctx.new_page()
                found: list[Any] = []
                matched: list[str] = []
                pending: list[asyncio.Task[None]] = []

                async def keep(resp: Any) -> None:
                    try:
                        found.append(await resp.json())
                    except Exception:  # not JSON after all, or the body is gone
                        return

                def on_response(r: Any) -> None:
                    if pattern.search(r.url):
                        matched.append(r.url)
                        pending.append(asyncio.ensure_future(keep(r)))

                page.on("response", on_response)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as e:
                    raise ToolFailure("platform_unavailable", f"{platform}: page didn't load ({type(e).__name__})") from e
                await page.wait_for_timeout(settle_ms)
                for _ in range(scrolls):
                    await page.mouse.wheel(0, 2400)
                    await page.wait_for_timeout(1500)
                if pending:
                    await asyncio.wait(pending, timeout=10)
                if blocked is not None and blocked(await page.inner_text("body"), page.url):
                    raise ToolFailure("rate_limited", f"{platform}: the site showed a captcha / bot check",
                                      retry_after_s=1800)
                if LOGIN_URL_RE.search(page.url) and not LOGIN_URL_RE.search(url):
                    if has:
                        self.creds.mark_expired(platform)
                    raise ToolFailure("login_required", f"{platform}: the site asked to log in"
                                      + (" (the scraping account's session has expired; reconnect it)" if has else ""))
                for extra in (follow(matched) if follow is not None else [])[:6]:
                    try:
                        found.append(await page.evaluate(
                            "async ([u, h]) => (await fetch(u, {credentials: 'include', headers: h})).json()",
                            [extra, follow_headers or {}]))
                    except Exception as e:  # one failed extra fetch only costs its results
                        log.info("follow-up fetch failed on %s: %s", platform, type(e).__name__)
                return found
            finally:
                await ctx.close()
