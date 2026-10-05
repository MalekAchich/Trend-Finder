"""ChatGPT adapter: OpenAI Responses API over SSE on the Codex backend (D-29, D-37)."""
from __future__ import annotations

import base64
import json
import mimetypes
import time
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

from tf_agent.models.chatgpt_auth import CODEX_BASE_URL, ChatGptAuthError
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
    TextPart,
    ToolCall,
    Usage,
)

PROVIDER = "chatgpt"
CLIENT_VERSIONS = ("1.0.0", "2.0.0", "0.99.0")
USAGE_LIMIT_CODES = ("usage_limit_reached", "rate_limit_exceeded", "insufficient_quota")


class ChatGptAuthLike(Protocol):
    async def headers(self, accept: str = "text/event-stream") -> dict[str, str]: ...

    async def ensure_fresh(self, force: bool = False) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]: ...


def _image_url(part: ImagePart) -> str:
    data = Path(part.path).read_bytes()
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
        "include": [],
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
        payload["reasoning"] = {"effort": req.reasoning_effort}
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
    used = _num(headers, "x-codex-primary-used-percent", float)
    if used is None:
        return None
    return RateInfo(
        used_percent=used,
        window_minutes=_num(headers, "x-codex-primary-window-minutes", int),
        resets_at=_num(headers, "x-codex-primary-reset-at", float),
    )


def _reset_at(headers: Mapping[str, str]) -> float | None:
    reset = _num(headers, "x-codex-primary-reset-at", float)
    if reset is not None:
        return reset
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


async def _consume_sse(lines: AsyncIterator[str]) -> tuple[str, list[ToolCall], Usage]:
    texts: list[str] = []
    calls: list[ToolCall] = []
    usage = Usage()
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
        if kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "message":
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
        elif kind in ("error", "response.failed"):
            err = event.get("error") or (event.get("response") or {}).get("error") or {}
            code = str(err.get("code") or err.get("type") or "")
            message = str(err.get("message") or json.dumps(event)[:300])
            if code in USAGE_LIMIT_CODES:
                raise UsageLimited(PROVIDER, message, None)
            if not code or code in ("server_error", "overloaded"):
                raise TransientProviderError(PROVIDER, message)
            raise InvalidRequest(PROVIDER, f"{code}: {message}")
    return "".join(texts), calls, usage


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

    async def _headers(self, accept: str, force_refresh: bool) -> dict[str, str]:
        try:
            if force_refresh:
                await self.auth.ensure_fresh(force=True)
            return await self.auth.headers(accept)
        except ChatGptAuthError as e:
            raise AuthRequired(PROVIDER, str(e)) from e

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        payload = build_payload(req)
        for attempt in (1, 2):
            headers = await self._headers("text/event-stream", force_refresh=attempt == 2)
            try:
                async with self._client_factory(req.timeout_s) as client:
                    async with client.stream("POST", f"{self.base_url}/responses", headers=headers,
                                             json=payload) as r:
                        if r.status_code == 401 and attempt == 1:
                            continue
                        if r.status_code >= 400:
                            body = (await r.aread()).decode(errors="replace")
                            raise _error_from_status(r.status_code, body, r.headers)
                        rate = parse_rate_headers(r.headers)
                        text, calls, usage = await _consume_sse(r.aiter_lines())
            except httpx.TransportError as e:
                raise TransientProviderError(PROVIDER, f"network error: {type(e).__name__}") from e
            structured = None
            if req.output_schema is not None:
                try:
                    structured = json.loads(text)
                except json.JSONDecodeError as e:
                    raise MalformedResponse(PROVIDER, "structured output was not valid JSON") from e
            return CompletionResponse(provider=PROVIDER, model=req.model, text=text, tool_calls=calls,
                                      structured=structured, usage=usage, rate=rate)
        raise AuthRequired(PROVIDER, "still unauthorized after token refresh: run `tf login chatgpt`")

    async def list_models(self) -> list[ModelInfo]:
        headers = await self._headers("application/json", force_refresh=False)
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
