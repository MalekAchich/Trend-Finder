"""Real-browser access for platforms that only answer a browser: scraping-account logins and JSON capture.

`connect()` opens a visible Chromium window on the platform's login page; the owner logs in by hand (2FA and
checkpoints included) and the session is saved, never the password. `capture_json()` opens a page headless with that
saved session and keeps the JSON responses the page itself fetches (search results, trend lists), which is far
steadier than parsing HTML.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any, Protocol

from tf_agent.credentials import SESSIONS, Credentials, SessionSpec
from tf_agent.tools.types import ToolFailure

log = logging.getLogger(__name__)
LOGIN_TIMEOUT_S = 600
STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]  # no automation banner / navigator.webdriver
HIDE_WEBDRIVER = "Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => undefined})"
PAGE_GAP_S, PAGE_JITTER_S = 4.0, 3.0
RESULT_WAIT_S = 20.0  # longest wait for the first matching response
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


def logged_in(state: dict[str, Any], spec: SessionSpec) -> bool:
    return any(spec.domain in str(c.get("domain")) and c.get("value") and (not spec.proof or c.get("name") in spec.proof)
               for c in state.get("cookies") or [])


# a throwaway login profile shouldn't greet the owner with first-run pages
FIREFOX_PREFS = {"browser.shell.checkDefaultBrowser": "false", "browser.aboutwelcome.enabled": "false",
                 "datareporting.policy.dataSubmissionEnabled": "false",
                 "browser.startup.homepage_override.mstone": '"ignore"', "toolkit.telemetry.reportingpolicy.firstRun": "false"}
SAME_SITE = {0: "None", 1: "Lax", 2: "Strict"}


def firefox_cookies(db: Path) -> list[dict[str, Any]]:
    """A closed Firefox profile's cookies, in Playwright's storage-state shape."""
    if not db.is_file():
        return []
    with tempfile.TemporaryDirectory() as tmp:  # read a copy: never the live files
        for f in db.parent.glob(db.name + "*"):  # the database and its -wal journal
            shutil.copy(f, Path(tmp) / f.name)
        con = sqlite3.connect(Path(tmp) / db.name)
        try:
            rows = con.execute("select name, value, host, path, expiry, isSecure, isHttpOnly, sameSite "
                               "from moz_cookies").fetchall()
        finally:
            con.close()
    out = []
    for name, value, host, path, expiry, secure, http_only, same_site in rows:
        exp = int(expiry or 0)
        out.append({"name": name, "value": value, "domain": host, "path": path or "/",
                    "expires": exp // 1000 if exp > 10**11 else exp,  # newer Firefox stores milliseconds
                    "httpOnly": bool(http_only), "secure": bool(secure),
                    "sameSite": SAME_SITE.get(int(same_site or 0), "None")})
    return out


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
    def __init__(self, creds: Credentials, max_pages: int = 2, firefox_bin: str = "firefox") -> None:
        self.creds = creds
        self.firefox_bin = firefox_bin
        self._pw: Any = None
        self._browser: Any = None
        self._ua: str | None = None
        self._start = asyncio.Lock()
        self._pages = asyncio.Semaphore(max_pages)
        self._platform_locks: dict[str, asyncio.Lock] = {}
        self._last_page: dict[str, float] = {}
        self._xvfb: Any = None
        self._display: str | None = None
        self.connecting: dict[str, dict[str, str]] = {}  # platform -> {state: waiting|connected|failed, message}

    async def _playwright(self) -> Any:
        async with self._start:
            if self._pw is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
            return self._pw

    async def _headless(self) -> Any:
        """The browser for searches: a real (headed) Chromium on an invisible virtual screen when Xvfb is installed
        (sites tell headless browsers apart and answer with bot checks), otherwise Chromium's new headless mode."""
        pw = await self._playwright()
        async with self._start:
            if self._browser is None or not self._browser.is_connected():
                display = await self._virtual_display()
                if display is not None:
                    self._browser = await pw.chromium.launch(headless=False, channel="chromium", args=STEALTH_ARGS,
                                                             env={**os.environ, "DISPLAY": display})
                else:
                    self._browser = await pw.chromium.launch(headless=True, channel="chromium", args=STEALTH_ARGS)
                # its own identity, minus the "Headless" marker that sites answer with 403 (version stays real)
                probe = await self._browser.new_page()
                self._ua = (await probe.evaluate("navigator.userAgent")).replace("HeadlessChrome", "Chrome")
                await probe.close()
            return self._browser

    async def _virtual_display(self) -> str | None:
        """Starts Xvfb on a free display number (an X screen nobody sees). None when Xvfb isn't installed."""
        if self._xvfb is not None and self._xvfb.returncode is None:
            return self._display
        xvfb = shutil.which("Xvfb")
        if xvfb is None:
            return None
        for n in range(99, 140):
            if Path(f"/tmp/.X11-unix/X{n}").exists() or Path(f"/tmp/.X{n}-lock").exists():
                continue
            proc = await asyncio.create_subprocess_exec(xvfb, f":{n}", "-screen", "0", "1366x900x24", "-nolisten", "tcp",
                                                        stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.DEVNULL)
            for _ in range(30):  # wait for its socket
                if Path(f"/tmp/.X11-unix/X{n}").exists():
                    self._xvfb, self._display = proc, f":{n}"
                    return self._display
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.1)
            if proc.returncode is None:
                proc.terminate()
        return None

    async def _pace(self, platform: str) -> None:
        """One page at a time per platform, a few seconds apart: a burst of searches from one account is a bot."""
        gap = PAGE_GAP_S + random.uniform(0, PAGE_JITTER_S)
        wait = self._last_page.get(platform, 0.0) + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_page[platform] = time.monotonic()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        if self._xvfb is not None and self._xvfb.returncode is None:
            self._xvfb.terminate()
        self._browser = self._pw = self._xvfb = None

    # ---- logins ----
    async def _connect_firefox(self, platform: str, spec: SessionSpec, timeout_s: float, poll_s: float = 3.0) -> None:
        """Opens the owner's real Firefox on a fresh throwaway profile; once Firefox is closed, its cookies are the
        session. The profile is deleted afterwards, so only the session file remains."""
        profile = self.creds.session_dir(platform) / "firefox-profile"
        shutil.rmtree(profile, ignore_errors=True)
        profile.mkdir(parents=True, mode=0o700)
        (profile / "user.js").write_text("".join(f'user_pref("{k}", {v});\n' for k, v in FIREFOX_PREFS.items()))
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.firefox_bin, "--new-instance", "--profile", str(profile), spec.login_url,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                raise LoginCancelled("Firefox isn't installed (or not on the PATH)") from None
            deadline = time.monotonic() + timeout_s
            saved = False

            def running() -> bool:  # some launchers hand off and exit at once: the profile lock says it's in use
                return proc.returncode is None or os.path.lexists(profile / "lock")

            while running():
                if time.monotonic() >= deadline:
                    proc.terminate()
                    raise TimeoutError(f"Firefox stayed open for {int(timeout_s // 60)} minutes")
                state = {"cookies": firefox_cookies(profile / "cookies.sqlite"), "origins": []}
                if logged_in(state, spec):  # saved as soon as the login exists: no need to close Firefox first
                    self.creds.save_session(platform, state)
                    if not saved:
                        self.connecting[platform] = {"state": "connected",
                                                     "message": "Connected. You can close Firefox now."}
                    saved = True
                try:
                    await asyncio.wait_for(proc.wait(), poll_s)
                except TimeoutError:
                    pass
            state = {"cookies": firefox_cookies(profile / "cookies.sqlite"), "origins": []}
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if logged_in(state, spec):
            self.creds.save_session(platform, state)  # the final cookies, as Firefox left them
        elif not saved:
            raise LoginCancelled("Firefox was closed before the login finished (nothing was saved). Log in, wait "
                                 "until you see the logged-in page, then close Firefox.")

    async def connect(self, platform: str, timeout_s: float = LOGIN_TIMEOUT_S) -> None:
        spec = SESSIONS[platform]
        self.connecting[platform] = {"state": "waiting", "message": (
            "A Firefox window just opened: log in there, then close Firefox." if spec.browser == "firefox" else
            "Log in in the browser window that just opened." if spec.cookie else
            "Log in in the browser window that just opened, then close the window.")}
        try:
            if spec.browser == "firefox":
                await self._connect_firefox(platform, spec, timeout_s)
                self.connecting[platform] = {"state": "connected", "message": "Connected."}
                return
            pw = await self._playwright()
            browser = await pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            try:
                ctx = await browser.new_context(viewport=None, locale="en-US")
                page = await ctx.new_page()
                await page.goto(spec.login_url)
                if spec.cookie is None:
                    state = await wait_for_close(ctx, timeout_s, closed=lambda: not browser.is_connected() or page.is_closed())
                    if not logged_in(state, spec):
                        raise LoginCancelled("the window was closed before the login finished (nothing was saved). "
                                             "Log in with the account's email or phone and password: Google sign-in "
                                             "is blocked inside automated browser windows.")
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
                           follow: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
                           follow_headers: dict[str, str] | None = None) -> list[Any]:
        """JSON bodies of the page's own requests whose URL matches `pattern`. `follow` maps the matched requests
        ({url, method, body}) to more requests sent from inside the page (same cookies and origin), e.g. the same API
        with another page or filter."""
        has = platform in SESSIONS and self.creds.has_session(platform)
        if need_session and not has:
            raise ToolFailure("login_required", f"{platform}: no scraping account connected "
                                                "(Settings, Accounts & keys)")
        state = str(self.creds.state_file(platform)) if has else None
        lock = self._platform_locks.setdefault(platform, asyncio.Lock())
        async with lock, self._pages:
            await self._pace(platform)
            browser = await self._headless()
            ctx = await browser.new_context(storage_state=state, locale="en-US", user_agent=self._ua,
                                            viewport={"width": 1280, "height": 900})
            await ctx.add_init_script(HIDE_WEBDRIVER)
            try:
                page = await ctx.new_page()
                found: list[Any] = []
                matched: list[dict[str, Any]] = []
                pending: list[asyncio.Task[None]] = []

                async def keep(resp: Any) -> None:
                    try:
                        found.append(await resp.json())
                    except Exception:  # not JSON after all, or the body is gone
                        return

                def on_response(r: Any) -> None:
                    if pattern.search(r.url):
                        matched.append({"url": r.url, "method": r.request.method, "body": r.request.post_data})
                        pending.append(asyncio.ensure_future(keep(r)))

                page.on("response", on_response)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as e:
                    raise ToolFailure("platform_unavailable", f"{platform}: page didn't load ({type(e).__name__})") from e
                # wait for the page's own answer: TikTok's search results land 8-12 s after the page (a fixed short
                # wait returned nothing for every search in run 2), then give late responses a moment
                deadline = time.monotonic() + RESULT_WAIT_S
                while not matched and time.monotonic() < deadline:
                    await page.wait_for_timeout(250)
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
                            """async ([u, m, b, h]) => (await fetch(u, {method: m, body: b, credentials: 'include',
                                headers: b ? {...h, 'content-type': 'application/json'} : h})).json()""",
                            [extra["url"], extra.get("method") or "GET", extra.get("body"), follow_headers or {}]))
                    except Exception as e:  # one failed extra fetch only costs its results
                        log.info("follow-up fetch failed on %s: %s", platform, type(e).__name__)
                return found
            finally:
                await ctx.close()
