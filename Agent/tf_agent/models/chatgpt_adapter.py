"""ChatGPT adapter: OpenAI Responses API over SSE on the Codex backend (D-29, D-37)."""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

from tf_agent.models.chatgpt_auth import CODEX_BASE_URL, ChatGptAuthError, ChatGptTransientError
from tf_agent.models.errors import (
    AuthRequired,
    InvalidRequest,
    MalformedResponse,
    ProviderError,
    TransientProviderError,
    UsageLimited,
)
from tf_agent.models.types import (
    CompletionRequest,
    CompletionResponse,
    ImagePart,
    ModelInfo,
    ProviderHealth,
    RateInfo,
    RateWindow,
    TextPart,
    ToolCall,
    Usage,
)

log = logging.getLogger(__name__)
PROVIDER = "chatgpt"
CLIENT_VERSIONS = ("1.0.0", "2.0.0", "0.99.0")
USAGE_LIMIT_CODES = ("usage_limit_reached", "rate_limit_exceeded", "insufficient_quota")


class ChatGptAuthLike(Protocol):
    async def headers(self, accept: str = "text/event-stream") -> dict[str, str]: ...

    async def ensure_fresh(self, force: bool = False, stale_token: str | None = None) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


def _image_url(part: ImagePart) -> str:
    try:
        data = Path(part.path).read_bytes()
    except OSError as e:
        raise InvalidRequest(PROVIDER, f"image not readable: {part.path}") from e
    mime = part.mime or mimetypes.guess_type(part.path)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def build_payload(req: CompletionRequest) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role == "user":
            content: list[dict[str, Any]] = []
            for p in m.parts:
                if isinstance(p, TextPart):
                    content.append({"type": "input_text", "text": p.text})
                else:
                    content.append({"type": "input_image", "image_url": _image_url(p)})
            items.append({"type": "message", "role": "user", "content": content})
        elif m.role == "assistant":
            # our own earlier reasoning for this turn goes back first (stateless: store=False keeps nothing), but only to
            # the model that wrote it: the owner can change models between steps, and another model can't read it
            mine = m.provider_state.get(PROVIDER) or {}
            if mine.get("model") == req.model:
                items.extend(dict(r) for r in mine.get("reasoning") or [])
            if m.text():
                items.append({"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": m.text()}]})
            for c in m.tool_calls:
                items.append({"type": "function_call", "call_id": c.id, "name": c.name,
                              "arguments": json.dumps(c.arguments)})
        else:
            items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": m.text()})

    payload: dict[str, Any] = {
        "model": req.model,
        "instructions": req.system,
        "input": items,
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
    }
    if req.tools:
        payload["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                             "parameters": t.parameters, "strict": False} for t in req.tools]
        payload["tool_choice"] = "required" if req.require_tool else "auto"
        payload["parallel_tool_calls"] = True
    else:
        payload["tools"] = []
        payload["tool_choice"] = "none"
        payload["parallel_tool_calls"] = False
    if req.reasoning_effort:
        payload["reasoning"] = {"effort": req.reasoning_effort, "summary": "auto"}
    if req.output_schema is not None:
        payload["text"] = {"format": {"type": "json_schema", "name": req.schema_name,
                                      "schema": req.output_schema, "strict": False}}
    return payload


def _num(headers: Mapping[str, str], key: str, cast: Callable[[str], Any]) -> Any:
    value = headers.get(key)
    if value is None:
        return None
    try:
        return cast(value)
    except ValueError:
        return None


def parse_rate_headers(headers: Mapping[str, str]) -> RateInfo | None:
    """The binding window is whichever of the 5-hour (primary) and weekly (secondary) windows is fuller (D-37)."""
    windows = []
    for name in ("primary", "secondary"):
        used = _num(headers, f"x-codex-{name}-used-percent", float)
        if used is not None:
            minutes = _num(headers, f"x-codex-{name}-window-minutes", int)
            label = "five_hour" if name == "primary" else "seven_day"
            windows.append(RateWindow(label, used, minutes, _num(headers, f"x-codex-{name}-reset-at", float)))
    if not windows:
        return None
    top = max(windows, key=lambda w: w.used_percent)
    return RateInfo(used_percent=top.used_percent, window_minutes=top.window_minutes, resets_at=top.resets_at,
                    windows=tuple(windows))


def _reset_at(headers: Mapping[str, str]) -> float | None:
    rate = parse_rate_headers(headers)
    if rate is not None and rate.resets_at is not None:
        return rate.resets_at
    retry_after = _num(headers, "retry-after", float)
    return time.time() + retry_after if retry_after is not None else None


def _error_from_status(status: int, body: str, headers: Mapping[str, str]) -> ProviderError:
    lowered = body.lower()
    if status == 429 or any(code in lowered for code in USAGE_LIMIT_CODES):
        return UsageLimited(PROVIDER, f"usage limited ({status})", _reset_at(headers))
    if status in (401, 403):
        return AuthRequired(PROVIDER, f"authentication failed ({status}): run `tf login chatgpt`")
    if status >= 500:
        return TransientProviderError(PROVIDER, f"server error ({status})")
    return InvalidRequest(PROVIDER, f"request rejected ({status}): {body[:500]}")


def _reasoning_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """A reasoning output item to send back next step: encrypted content + summary, without the server-side id
    (nothing is stored with store=False, so an id would point at nothing)."""
    if not item.get("encrypted_content"):
        return None
    return {"type": "reasoning", "encrypted_content": item["encrypted_content"], "summary": item.get("summary") or []}


async def _consume_sse(lines: AsyncIterator[str]) -> tuple[str, list[ToolCall], Usage, str, list[dict[str, Any]]]:
    reasoning_items: list[dict[str, Any]] = []
    texts: list[str] = []
    summaries: list[str] = []
    deltas: list[str] = []
    calls: list[ToolCall] = []
    usage = Usage()
    completed = False
    async for line in lines:
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "response.reasoning_summary_text.delta":
            deltas.append(str(event.get("delta") or ""))
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "reasoning":
                if (kept := _reasoning_item(item)) is not None:
                    reasoning_items.append(kept)
                summaries.extend(str(p.get("text") or "") for p in item.get("summary") or []
                                 if p.get("type") == "summary_text")
            elif item.get("type") == "message":
                texts.extend(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text")
            elif item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {"_unparseable_arguments": item.get("arguments")}
                calls.append(ToolCall(id=item.get("call_id") or item.get("id") or "", name=item.get("name", ""),
                                      arguments=args if isinstance(args, dict) else {"value": args}))
        elif kind == "response.completed":
            u = (event.get("response") or {}).get("usage") or {}
            usage = Usage(u.get("input_tokens"), u.get("output_tokens"))
            completed = True
        elif kind == "response.incomplete":
            reason = ((event.get("response") or {}).get("incomplete_details") or {}).get("reason", "unknown")
            raise TransientProviderError(PROVIDER, f"response incomplete: {reason}")
        elif kind in ("error", "response.failed"):
            err = event.get("error") or (event.get("response") or {}).get("error") or {}
            code = str(err.get("code") or err.get("type") or "")
            message = str(err.get("message") or json.dumps(event)[:300])
            if code in USAGE_LIMIT_CODES:
                raise UsageLimited(PROVIDER, message, None)
            if not code or code in ("server_error", "overloaded"):
                raise TransientProviderError(PROVIDER, message)
            raise InvalidRequest(PROVIDER, f"{code}: {message}")
    if not completed:
        raise TransientProviderError(PROVIDER, "stream ended without response.completed")
    reasoning = "\n\n".join(t for t in summaries if t) or "".join(deltas)
    return "".join(texts), calls, usage, reasoning.strip(), reasoning_items


def _levels(model: dict[str, Any]) -> tuple[str, ...]:
    out = []
    for level in model.get("supported_reasoning_levels") or []:
        effort = level.get("effort") if isinstance(level, dict) else level
        if effort:
            out.append(str(effort))
    return tuple(out)


class ChatGPTOAuthAdapter:
    name = PROVIDER

    def __init__(
        self,
        auth: ChatGptAuthLike,
        client_factory: Callable[[float], httpx.AsyncClient] | None = None,
        base_url: str = CODEX_BASE_URL,
        models_cache: Path | None = None,
    ) -> None:
        self.auth = auth
        self.base_url = base_url
        self.models_cache = models_cache
        self._client_factory = client_factory or (
            lambda timeout: httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15)))

    async def _headers(self, accept: str, stale_token: str | None = None) -> dict[str, str]:
        try:
            if stale_token is not None:
                await self.auth.ensure_fresh(force=True, stale_token=stale_token)
            return await self.auth.headers(accept)
        except ChatGptTransientError as e:
            raise TransientProviderError(PROVIDER, str(e)) from e
        except ChatGptAuthError as e:
            raise AuthRequired(PROVIDER, str(e)) from e
        except httpx.TransportError as e:
            raise TransientProviderError(PROVIDER, f"auth network error: {type(e).__name__}") from e

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        payload = build_payload(req)
        rejected_token: str | None = None
        for attempt in (1, 2):
            headers = await self._headers("text/event-stream", stale_token=rejected_token)
            try:
                async with self._client_factory(req.timeout_s) as client:
                    async with client.stream("POST", f"{self.base_url}/responses", headers=headers,
                                             json=payload) as r:
                        if r.status_code == 401 and attempt == 1:
                            rejected_token = headers.get("authorization", "").removeprefix("Bearer ")
                            continue
                        if r.status_code >= 400:
                            body = (await r.aread()).decode(errors="replace")
                            raise _error_from_status(r.status_code, body, r.headers)
                        rate = parse_rate_headers(r.headers)
                        text, calls, usage, reasoning, kept = await _consume_sse(r.aiter_lines())
            except httpx.TransportError as e:
                raise TransientProviderError(PROVIDER, f"network error: {type(e).__name__}") from e
            structured = None
            if req.output_schema is not None:
                try:
                    structured = json.loads(text)
                except json.JSONDecodeError as e:
                    raise MalformedResponse(PROVIDER, "structured output was not valid JSON") from e
            return CompletionResponse(provider=PROVIDER, model=req.model, text=text, tool_calls=calls,
                                      structured=structured, usage=usage, rate=rate, reasoning=reasoning,
                                      provider_state={PROVIDER: {"model": req.model, "reasoning": kept}} if kept else {})
        raise AuthRequired(PROVIDER, "still unauthorized after token refresh: run `tf login chatgpt`")

    async def usage(self) -> RateInfo | None:
        """The subscription's 5-hour and weekly windows, as the website shows them (no model call, no cost)."""
        url = self.base_url.rsplit("/codex", 1)[0] + "/wham/usage"
        try:
            headers = await self._headers("application/json")
            async with self._client_factory(20) as client:
                r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return None
            limits = (r.json() or {}).get("rate_limit") or {}
        except (httpx.HTTPError, ValueError, ProviderError) as e:
            log.info("chatgpt usage lookup failed: %s", e)
            return None
        windows = []
        for key, name in (("primary_window", "five_hour"), ("secondary_window", "seven_day")):
            w = limits.get(key)
            if isinstance(w, dict) and w.get("used_percent") is not None:
                seconds = w.get("limit_window_seconds")
                windows.append(RateWindow(name, float(w["used_percent"]), int(seconds) // 60 if seconds else None,
                                          float(w["reset_at"]) if w.get("reset_at") else None))
        if not windows:
            return None
        top = max(windows, key=lambda w: w.used_percent)
        return RateInfo(used_percent=top.used_percent, window_minutes=top.window_minutes, resets_at=top.resets_at,
                        windows=tuple(windows))

    async def list_models(self) -> list[ModelInfo]:
        headers = await self._headers("application/json")
        best: list[dict[str, Any]] = []
        async with self._client_factory(30) as client:
            for version in CLIENT_VERSIONS:
                try:
                    r = await client.get(f"{self.base_url}/models", params={"client_version": version},
                                         headers=headers)
                except httpx.HTTPError:
                    continue
                if r.status_code >= 400:
                    continue
                body = r.json()
                models = body.get("models") or body.get("data") or []
                if len(models) > len(best):
                    best = models
        if best and self.models_cache:
            self.models_cache.parent.mkdir(parents=True, exist_ok=True)
            self.models_cache.write_text(json.dumps(best))
        elif not best and self.models_cache and self.models_cache.exists():
            best = json.loads(self.models_cache.read_text())
        infos = []
        for m in best:
            model_id = m.get("slug") or m.get("id")
            if not model_id:
                continue
            infos.append(ModelInfo(
                provider=PROVIDER,
                model_id=model_id,
                display_name=m.get("display_name"),
                context_window=m.get("context_window"),
                vision="image" in (m.get("input_modalities") or ["text"]),
                priority=int(m.get("priority") or 100),
                hidden=(m.get("visibility") or "list") != "list" or m.get("supported_in_api") is False,
                reasoning_levels=_levels(m),
            ))
        return infos

    async def health(self) -> ProviderHealth:
        st = self.auth.status()
        if not st.get("connected"):
            return ProviderHealth(PROVIDER, False, "not logged in: run `tf login chatgpt`")
        return ProviderHealth(PROVIDER, True, "connected", account=f"{st.get('email')} ({st.get('plan')})")
