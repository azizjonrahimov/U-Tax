"""Async client for the Naïve Managed Agents REST API (https://api.vetta.sh/v1).

The Naïve SDK is TypeScript-only, so this is a thin httpx client over the REST routes.
Routes, methods and body fields were checked against the public spec at
`GET https://api.vetta.sh/v1/openapi.json` (version 2026-09-30) and `research/naive-sdk.md`.

Rules this client keeps:
- Auth: `Authorization: Bearer $NAIVE_API_KEY`, pinned `Vetta-Version`.
- Every POST carries an `Idempotency-Key` (reused across retries of the same call).
- Errors become `NaiveError` carrying `error.code`. Branch on `.code`, never `.message`.
- 429 is retried after `Retry-After`.
- Money is integer micro-USD (1 USD = 1_000_000). Floats are rejected, never rounded.
- Lists come back as `Page(data, has_more, next_cursor)`; pass `next_cursor` as `after`.
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app import __version__

DEFAULT_API_BASE = "https://api.vetta.sh"
API_VERSION = "2026-09-30"
USER_AGENT = f"naive-yc-hackathon-backend/{__version__}"

JSON = dict[str, Any]
Sleep = Callable[[float], Awaitable[None]]


class NaiveError(Exception):
    """A non-2xx answer from Naïve, or a response we could not read.

    `code` is the stable machine value (`subscription_required`, `insufficient_credits`,
    `session_running`, `version_conflict`, `rate_limited`, ...). Branch on it.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        type: str | None = None,
        request_id: str | None = None,
        param: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.type = type
        self.request_id = request_id
        self.param = param
        self.retry_after = retry_after


class NaiveConfigError(RuntimeError):
    """The client is missing configuration (for example `NAIVE_API_KEY`)."""


@dataclass(frozen=True)
class Page:
    """One page of a Naïve list: `{data, has_more, next_cursor}`."""

    data: list[JSON]
    has_more: bool = False
    next_cursor: str | None = None
    raw: JSON = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, body: JSON) -> "Page":
        return cls(
            data=list(body.get("data") or []),
            has_more=bool(body.get("has_more", False)),
            next_cursor=body.get("next_cursor"),
            raw=body,
        )


def micro_usd(name: str, value: Any) -> int:
    """Check a money value is integer micro-USD. Floats are a bug, not a rounding problem."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int in micro-USD (1 USD = 1_000_000), got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def _check_budget(budget: Mapping[str, Any]) -> None:
    for key in ("cap_micro_usd", "max_task_micro_usd"):
        if key in budget:
            micro_usd(f"budget.{key}", budget[key])


def _parse_retry_after(value: str | None) -> float | None:
    """`Retry-After` is either delta-seconds or an HTTP-date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _api_root(base: str) -> str:
    """`NAIVE_API_BASE` is the host (`https://api.vetta.sh`); the client appends `/v1`."""
    base = base.rstrip("/")
    if base.endswith("/v1"):  # tolerate a base that already names the version
        base = base[: -len("/v1")]
    return f"{base}/v1"


def _compact(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop unset query/body values; render bools the way query strings expect."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else value
    return out


def _body(**fields: Any) -> JSON:
    return {k: v for k, v in fields.items() if v is not None}


def _event_seq(event: JSON, sse_id: str | None) -> int | None:
    seq = event.get("seq")
    if isinstance(seq, int) and not isinstance(seq, bool):
        return seq
    if sse_id is not None and sse_id.isdigit():
        return int(sse_id)
    return None


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str | None, str | None, str]]:
    """Parse a text/event-stream into `(id, event, data)` messages (WHATWG SSE rules)."""
    data: list[str] = []
    event_id: str | None = None
    event_type: str | None = None
    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield event_id, event_type, "\n".join(data)
            data, event_type = [], None
            continue
        if line.startswith(":"):  # comment / keep-alive
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            data.append(value)
        elif name == "id":
            event_id = value
        elif name == "event":
            event_type = value
        # "retry" and unknown fields are ignored
    if data:  # stream ended without a trailing blank line
        yield event_id, event_type, "\n".join(data)


class NaiveClient:
    """Async client. Use as `async with NaiveClient() as naive: ...` or call `aclose()`.

    `api_key` defaults to env `NAIVE_API_KEY`; `api_base` to env `NAIVE_API_BASE`
    (host only, default `https://api.vetta.sh`). `transport` lets tests inject
    `httpx.MockTransport`; `sleep` lets tests skip real waits.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        *,
        api_version: str = API_VERSION,
        timeout: float = 30.0,
        max_retries: int = 2,
        max_retry_wait: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("NAIVE_API_KEY", "")
        if not key:
            raise NaiveConfigError(
                "NAIVE_API_KEY is not set. Put it in backend/.env (see .env.example)."
            )
        base = api_base or os.environ.get("NAIVE_API_BASE") or DEFAULT_API_BASE
        self.base_url = _api_root(base)
        self.max_retries = max_retries
        self.max_retry_wait = max_retry_wait
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {key}",
                "Vetta-Version": api_version,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> "NaiveClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def __repr__(self) -> str:  # never show the key
        return f"NaiveClient(base_url={self.base_url!r})"

    # ------------------------------------------------------------------ core

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Send one request. Retries 429 after `Retry-After`; raises `NaiveError` otherwise."""
        headers: dict[str, str] = {}
        if method.upper() == "POST":
            # One key per logical call, so a retried POST can't double-create.
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        query = _compact(params or {})
        attempt = 0
        while True:
            response = await self._http.request(
                method, path, params=query, json=json_body, headers=headers
            )
            if response.status_code == 429 and attempt < self.max_retries:
                wait = _parse_retry_after(response.headers.get("Retry-After"))
                # UNVERIFIED: the docs promise Retry-After on every 429; fall back to backoff.
                wait = 2.0**attempt if wait is None else wait
                await self._sleep(min(wait, self.max_retry_wait))
                attempt += 1
                continue
            return self._handle(response)

    def _handle(self, response: httpx.Response) -> Any:
        if response.is_success:
            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise NaiveError(
                    response.status_code,
                    "invalid_response",
                    "Naïve returned a body that is not JSON",
                    request_id=response.headers.get("x-request-id"),
                ) from exc
        raise self._error(response)

    @staticmethod
    def _error(response: httpx.Response) -> NaiveError:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        try:
            err = response.json().get("error")
        except (ValueError, AttributeError):
            err = None
        if isinstance(err, dict) and isinstance(err.get("code"), str):
            return NaiveError(
                response.status_code,
                err["code"],
                str(err.get("message", "")),
                type=err.get("type"),
                request_id=err.get("request_id"),
                param=err.get("param"),
                retry_after=retry_after,
            )
        # Not the documented envelope (proxy error page, gateway timeout, ...).
        return NaiveError(
            response.status_code,
            "rate_limited" if response.status_code == 429 else "unexpected_response",
            response.text[:200] or response.reason_phrase,
            retry_after=retry_after,
        )

    async def _page(self, path: str, params: Mapping[str, Any]) -> Page:
        return Page.from_json(await self.request("GET", path, params=params))

    async def paginate(
        self, path: str, params: Mapping[str, Any] | None = None, *, max_pages: int = 100
    ) -> AsyncIterator[JSON]:
        """Walk a list route page by page (`after` = previous `next_cursor`)."""
        query = dict(params or {})
        for _ in range(max_pages):
            page = await self._page(path, query)
            for item in page.data:
                yield item
            if not page.has_more or not page.next_cursor:
                return
            query["after"] = page.next_cursor

    # ---------------------------------------------------------------- agents

    async def create_agent(
        self,
        *,
        name: str,
        model: str,
        budget: Mapping[str, Any],
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> JSON:
        """`POST /v1/agents`. `budget` = {cap_micro_usd, max_task_micro_usd, period}.

        Other documented fields (harness, system, window, tools, mcp_servers, skills,
        multiagent, handoffs, output_schema, description, metadata, ...) go in `fields`.
        """
        _check_budget(budget)
        body = _body(name=name, model=model, budget=dict(budget), **fields)
        return await self.request("POST", "/agents", json_body=body, idempotency_key=idempotency_key)

    async def get_agent(self, agent_id: str) -> JSON:
        return await self.request("GET", f"/agents/{agent_id}")

    async def list_agents(self, *, limit: int | None = None, after: str | None = None) -> Page:
        return await self._page("/agents", {"limit": limit, "after": after})

    async def update_agent(self, agent_id: str, *, expected_version: int, **fields: Any) -> JSON:
        """`PATCH /v1/agents/{id}`. Every edit mints a version; a stale
        `expected_version` raises `NaiveError(code="version_conflict")`."""
        if "budget" in fields and fields["budget"] is not None:
            _check_budget(fields["budget"])
        body = _body(expected_version=expected_version, **fields)
        return await self.request("PATCH", f"/agents/{agent_id}", json_body=body)

    async def agent_board(
        self, agent_id: str, *, limit: int | None = None, after: str | None = None
    ) -> Page:
        """`GET /v1/agents/{id}/board`: the agent's board cards in column order."""
        return await self._page(f"/agents/{agent_id}/board", {"limit": limit, "after": after})

    async def list_board_cards(
        self, board_id: str, *, limit: int | None = None, after: str | None = None
    ) -> Page:
        """`GET /v1/boards/{id}/cards`."""
        return await self._page(f"/boards/{board_id}/cards", {"limit": limit, "after": after})

    # -------------------------------------------------------------- sessions

    async def create_session(
        self,
        *,
        agent_id: str,
        message: str,
        budget_micro_usd: int | None = None,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> JSON:
        """`POST /v1/sessions`: create and start a run.

        Optional documented fields: agent_version, window, output_schema, metadata,
        identity, mode, deployment_id, ...
        """
        if budget_micro_usd is not None:
            micro_usd("budget_micro_usd", budget_micro_usd)
        body = _body(agent_id=agent_id, message=message, budget_micro_usd=budget_micro_usd, **fields)
        return await self.request("POST", "/sessions", json_body=body, idempotency_key=idempotency_key)

    async def get_session(self, session_id: str) -> JSON:
        return await self.request("GET", f"/sessions/{session_id}")

    async def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        deployment_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> Page:
        params = {"agent_id": agent_id, "deployment_id": deployment_id, "status": status,
                  "limit": limit, "after": after}
        return await self._page("/sessions", params)

    async def send_message(
        self,
        session_id: str,
        *,
        message: str,
        interrupt: bool | None = None,
        queue: bool | None = None,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> JSON:
        """`POST /v1/sessions/{id}/messages` (202) -> `{session_id, status, accepted_seq}`.

        Sending to a running session without `interrupt`/`queue` raises
        `NaiveError(code="session_running")`. Wait for events with `seq > accepted_seq`.
        """
        body = _body(message=message, interrupt=interrupt, queue=queue, **fields)
        return await self.request(
            "POST", f"/sessions/{session_id}/messages", json_body=body,
            idempotency_key=idempotency_key,
        )

    async def confirm_tool(
        self,
        session_id: str,
        *,
        tool_call_id: str,
        decision: str,
        reason: str | None = None,
        scope: str | None = None,
        idempotency_key: str | None = None,
    ) -> JSON:
        """`POST /v1/sessions/{id}/tool_confirmations`. `decision` is "allow" or "deny";
        `scope` is "call" or "session"."""
        if decision not in ("allow", "deny"):
            raise ValueError(f"decision must be 'allow' or 'deny', got {decision!r}")
        body = _body(tool_call_id=tool_call_id, decision=decision, reason=reason, scope=scope)
        return await self.request(
            "POST", f"/sessions/{session_id}/tool_confirmations", json_body=body,
            idempotency_key=idempotency_key,
        )

    async def answer(
        self,
        session_id: str,
        *,
        tool_call_id: str,
        answers: Mapping[str, str | list[str]],
        idempotency_key: str | None = None,
    ) -> JSON:
        """`POST /v1/sessions/{id}/answers`: reply to an `ask_operator` question."""
        body = {"tool_call_id": tool_call_id, "answers": dict(answers)}
        return await self.request(
            "POST", f"/sessions/{session_id}/answers", json_body=body,
            idempotency_key=idempotency_key,
        )

    async def interrupt_session(self, session_id: str, *, message: str | None = None) -> JSON:
        """`POST /v1/sessions/{id}/interrupt`, optionally steering with a new message."""
        return await self.request(
            "POST", f"/sessions/{session_id}/interrupt", json_body=_body(message=message)
        )

    async def cancel_session(self, session_id: str) -> JSON:
        """Terminal. Sessions can't be deleted; this is the only way to end one."""
        return await self.request("POST", f"/sessions/{session_id}/cancel")

    async def set_session_budget(self, session_id: str, budget_micro_usd: int) -> JSON:
        """`PATCH /v1/sessions/{id}/budget`. Raising the cap resumes a `budget_paused` session."""
        micro_usd("budget_micro_usd", budget_micro_usd)
        # The spec names the body field `cap_micro_usd` (the SDK takes a bare number).
        return await self.request(
            "PATCH", f"/sessions/{session_id}/budget", json_body={"cap_micro_usd": budget_micro_usd}
        )

    async def session_events(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        limit: int | None = None,
        include_accepted: bool | None = None,
        after: str | None = None,
    ) -> Page:
        """`GET /v1/sessions/{id}/events`. `data` is returned sorted by `seq`.

        `include_accepted=True` adds tool.confirm.resolved, plan.updated, session.mode,
        outcome.evaluation.* (needed for a "decisions" view).
        """
        page = await self._page(
            f"/sessions/{session_id}/events",
            {"after_seq": after_seq, "limit": limit, "include_accepted": include_accepted,
             "after": after},
        )
        ordered = sorted(page.data, key=lambda e: e.get("seq", 0))
        return Page(ordered, page.has_more, page.next_cursor, page.raw)

    async def stream(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        max_reconnects: int = 3,
    ) -> AsyncIterator[JSON]:
        """`GET /v1/sessions/{id}/stream` (SSE). Yields event dicts in `seq` order.

        Each SSE message is one event as JSON in `data:` with `seq` as the SSE `id:`.
        On a dropped connection it reconnects with `after_seq=<last seen seq>` (up to
        `max_reconnects` times) and skips anything already yielded. A clean close ends it.
        # UNVERIFIED: whether the server closes the stream itself when the session goes
        # idle; callers should also stop on `session.idle` / terminal events if needed.
        """
        last_seq = after_seq
        reconnects = 0
        while True:
            try:
                async with self._http.stream(
                    "GET",
                    f"/sessions/{session_id}/stream",
                    params={"after_seq": last_seq},
                    headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(None, connect=10.0),
                ) as response:
                    if not response.is_success:
                        await response.aread()
                        raise self._error(response)
                    async for sse_id, _type, data in iter_sse(response.aiter_lines()):
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue  # not an event payload
                        if not isinstance(event, dict):
                            continue
                        seq = _event_seq(event, sse_id)
                        if seq is not None:
                            if seq <= last_seq:
                                continue  # replayed after a reconnect
                            last_seq = seq
                        yield event
                return
            except httpx.TransportError:  # includes RemoteProtocolError / ReadError
                if reconnects >= max_reconnects:
                    raise
                reconnects += 1
                await self._sleep(min(2.0 ** (reconnects - 1), self.max_retry_wait))

    # ----------------------------------------------------- dashboard reads

    async def digest(self) -> JSON:
        """`GET /v1/digest`: agents, waiting.{awaiting_approval,awaiting_answer,budget_paused},
        failed, recent, channels. Needs scopes sessions:read + agents:read."""
        return await self.request("GET", "/digest")

    async def credits_spend(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        deployment_id: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        days: int | None = None,
        timezone: str | None = None,
    ) -> JSON:
        """`GET /v1/credits/spend`: totals plus the 50 most expensive agents/sessions.
        Amounts are integer micro-USD."""
        params = {"session_id": session_id, "agent_id": agent_id, "deployment_id": deployment_id,
                  "from": from_, "to": to, "days": days, "timezone": timezone}
        return await self.request("GET", "/credits/spend", params=params)

    # ----------------------------------------------------------- deployments

    async def create_deployment(
        self,
        *,
        agent_id: str,
        cron: str,
        input_template: str,
        budget_micro_usd: int,
        idempotency_key: str | None = None,
        **fields: Any,
    ) -> JSON:
        """`POST /v1/deployments`: a cron schedule (5-field, at most every 5 min).

        The prompt field is `input_template` (the openapi spec's name; the reference
        page's `prompt` is wrong). Optional: timezone, window, agent_version, identity,
        on_idle, enabled.
        """
        micro_usd("budget_micro_usd", budget_micro_usd)
        body = _body(agent_id=agent_id, cron=cron, input_template=input_template,
                     budget_micro_usd=budget_micro_usd, **fields)
        return await self.request("POST", "/deployments", json_body=body, idempotency_key=idempotency_key)

    async def list_deployments(self, *, limit: int | None = None, after: str | None = None) -> Page:
        return await self._page("/deployments", {"limit": limit, "after": after})
