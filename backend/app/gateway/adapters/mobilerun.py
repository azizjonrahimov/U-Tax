"""DevicePort on Mobilerun: QA opens each sent memo on a real cloud phone and screenshots it
(Best Mobile Automation track).

How it works (Mobilerun Cloud REST, https://docs.mobilerun.ai/api-reference):
1. pick the phone: MOBILERUN_DEVICE_ID, else the first device in `GET /devices?state=ready`;
2. `POST /tasks` (Idempotency-Key = our key, so a retry never starts a second run): the
   Mobilerun agent opens the mail app on that phone, finds the memo by a short snippet of its
   text, scrolls it once and answers an `outputSchema` (rendered / disclaimer visible);
3. poll `GET /tasks/{id}/status` until a terminal status, bounded by `max_wait` seconds;
4. `GET /tasks/{id}/screenshots` and keep the last URL.

The phone must be signed in to a mailbox that receives a copy of every memo (the gateway's
QA copy; wiring is the backend lane's). Only a short snippet of the memo goes into the task
prompt, never the whole body. A phone check never blocks a case: every failure here raises a
PortError, and perform() records `phone.checked` as skipped.

UNVERIFIED (no key tonight): shapes are from the official API reference read on 2026-11-07;
429/Retry-After is not documented by Mobilerun, so it's mapped the usual way; the task-prompt
wording and the default model are unproven on a real device.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable

import httpx

from app.gateway.ports import (
    AuthExpired,
    DeviceCheck,
    NotConfigured,
    PortError,
    RateLimited,
    Rejected,
    Unavailable,
)

API = "https://api.mobilerun.ai/v1"
TERMINAL = {"completed", "failed", "cancelled"}
DISCLAIMER = "Educational estimate — not tax advice."

OUTPUT_SCHEMA = {  # what the Mobilerun agent must answer (structured output)
    "type": "object",
    "properties": {
        "found_email": {"type": "boolean", "description": "The email was found and opened"},
        "text_readable": {"type": "boolean", "description": "The text is readable, not cut off or garbled"},
        "disclaimer_visible": {"type": "boolean", "description": f"The line '{DISCLAIMER}' is visible"},
    },
    "required": ["found_email", "text_readable", "disclaimer_visible"],
}


def _map_error(response: httpx.Response) -> PortError:
    retry = response.headers.get("retry-after")
    try:
        retry_after = float(retry) if retry else None
    except ValueError:
        retry_after = None
    status = response.status_code
    detail = _detail(response)
    if status == 429:  # UNVERIFIED: not in Mobilerun's documented responses
        return RateLimited("mobilerun rate limited", retry_after=retry_after)
    if status in (401, 403):
        return AuthExpired("mobilerun key rejected")
    if status == 402:
        return Rejected(f"mobilerun out of credits: {detail}")
    if status in (409, 412) or status >= 500:  # device busy / not ready, or their outage
        return Unavailable(f"mobilerun {status}: {detail}", retry_after=retry_after)
    return Rejected(f"mobilerun {status}: {detail}")


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):  # ErrorResponse: {title, status, detail, errors}
        return str(body.get("detail") or body.get("title") or "")[:200]
    return str(body)[:200]


def snippet(body: str, limit: int = 60) -> str:
    """A short, distinctive line of the memo to find it by: the first line after the greeting,
    letters/digits/basic punctuation only, so nothing in it reads as an instruction."""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    pick = next((l for l in lines[1:] if len(l) > 20), lines[0] if lines else "")
    clean = re.sub(r"[^\w\s$,.:-]", "", pick)
    return re.sub(r"\s+", " ", clean)[:limit].strip()


class MobilerunDevice:
    provider = "mobilerun"

    def __init__(self, api_key: str, *, device_id: str | None = None, mail_app: str | None = None,
                 model: str | None = None, max_wait: float = 120.0, poll_every: float = 3.0,
                 transport: httpx.BaseTransport | None = None, timeout: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not api_key:
            raise NotConfigured("mobilerun: no MOBILERUN_API_KEY")
        self.device_id = device_id or os.environ.get("MOBILERUN_DEVICE_ID") or None
        self.mail_app = mail_app or os.environ.get("MOBILERUN_MAIL_APP", "com.google.android.gm")
        self.model = model or os.environ.get("MOBILERUN_MODEL", "mobilerun/mobile-agent-fast")
        self.max_wait = max_wait
        self.poll_every = poll_every
        self._sleep, self._clock = sleep, clock
        self._http = httpx.Client(base_url=API, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def __repr__(self) -> str:  # never show the key
        return "MobilerunDevice()"

    # ------------------------------------------------------------------ http

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise Unavailable(f"mobilerun unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        return response

    def _json(self, method: str, path: str, **kwargs) -> dict:
        body = self._request(method, path, **kwargs).json()
        if not isinstance(body, dict):
            raise Rejected(f"mobilerun {path}: unexpected response")
        return body

    # ------------------------------------------------------------------ port

    def _pick_device(self) -> str:
        if self.device_id:
            return self.device_id
        body = self._json("GET", "/devices", params={"state": "ready"})
        items = body.get("items") or []
        if not items or not items[0].get("id"):
            raise Unavailable("mobilerun: no ready device")
        return str(items[0]["id"])

    def _task_prompt(self, mail_id: str, body: str) -> str:
        return (
            f"Open the email app ({self.mail_app}). Find the most recent email whose text contains "
            f"\"{snippet(body)}\" and open it. Scroll through the whole message once, then answer: "
            f"was the email found, is the text readable (not cut off or garbled), and is the line "
            f"\"{DISCLAIMER}\" visible? Do not reply to, forward, delete or change the email. "
            f"Reference: {re.sub(r'[^\w.@<>-]', '', mail_id)[:120]}"
        )

    def open_and_capture(self, mail_id: str, body: str, key: str | None = None) -> DeviceCheck:
        device = self._pick_device()
        task = self._json("POST", "/tasks", headers={"Idempotency-Key": (key or f"phone:{mail_id}")[:128]}, json={
            "deviceId": device,
            "task": self._task_prompt(mail_id, body),
            "llmModel": self.model,
            "apps": [self.mail_app],
            "maxSteps": 25,
            "executionTimeout": int(self.max_wait),
            "outputSchema": OUTPUT_SCHEMA,
        })
        task_id = task.get("id")
        if not task_id:
            raise Rejected("mobilerun returned no task id")
        status = self._wait(str(task_id), str(task.get("status") or ""))
        urls = self._json("GET", f"/tasks/{task_id}/screenshots").get("urls") or []
        output = status.get("output") or {}
        rendered = bool(status.get("status") == "completed" and status.get("succeeded") is not False
                        and output.get("found_email") and output.get("text_readable")
                        and output.get("disclaimer_visible"))
        if not urls:
            raise Unavailable(f"mobilerun task {task_id}: no screenshot")
        return DeviceCheck(screenshot_url=str(urls[-1]), rendered_ok=rendered)

    def _wait(self, task_id: str, status: str) -> dict:
        deadline = self._clock() + self.max_wait
        while True:
            body = self._json("GET", f"/tasks/{task_id}/status")
            if body.get("status") in TERMINAL:
                return body
            if self._clock() >= deadline:
                self._stop(task_id)
                raise Unavailable(f"mobilerun task {task_id} still {body.get('status')} after {self.max_wait:.0f}s")
            self._sleep(self.poll_every)

    def _stop(self, task_id: str) -> None:
        """Best effort: a timed-out check must not keep a paid phone running."""
        try:
            self._http.post(f"/tasks/{task_id}/cancel")  # Stop Task
        except httpx.TransportError:
            pass
