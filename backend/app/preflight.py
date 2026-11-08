"""Task 37: checks before the owner records the demo, and a clean start.

    python -m app.devtools preflight            # read-only; exit 1 on any red
    python -m app.devtools archive-log --yes    # gateway stopped: log + docs -> data/archive/

Preflight never prints a key, a token, a secret or an API answer's body: each line is the
check, green or red, and a reason built from status codes and counts. The only write is
a probe file in the docs folder (created and removed) to prove it is writable.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import httpx

from app.naive_confirm import AGENT_NAMES

GREEN, RED = "\033[32mGREEN\033[0m", "\033[31mRED  \033[0m"
MIN_FREE_BYTES = 1 << 30  # 1 GB
MIN_TTS_CHARACTERS = 2000  # a demo's worth of spoken lines
UA = "utax-gateway-preflight"


@dataclass
class Check:
    name: str
    ok: bool
    reason: str

    def line(self, color: bool = True) -> str:
        mark = (GREEN if self.ok else RED) if color else ("GREEN" if self.ok else "RED  ")
        return f"{mark} {self.name}: {self.reason}"


Transport = httpx.BaseTransport | None


def _http(base: str, transport: Transport, **headers: str) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=10, transport=transport,
                        headers={"User-Agent": UA, **headers})


def _status(err: Exception) -> str:
    if isinstance(err, httpx.HTTPStatusError):
        return f"answered {err.response.status_code}"
    return f"unreachable ({type(err).__name__})"


def load_env(env_path: Path) -> None:
    """backend/.env under the process's own environment (this process only)."""
    from app.devtools import env_file_values

    for key, value in env_file_values(env_path).items():
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------- the checks


def check_gateway(settings, transport: Transport = None) -> list[Check]:
    out = []
    local = os.environ.get("UTAX_BASE", "http://localhost:8000")
    for name, base in (("gateway", local), ("public URL", settings.public_url)):
        if not base:
            out.append(Check(name, False, "UTAX_PUBLIC_URL is not set"))
            continue
        try:
            with _http(base.rstrip("/"), transport) as http:
                r = http.get("/api/health")
                r.raise_for_status()
            out.append(Check(name, True, f"{base.rstrip('/')}/api/health answered 200"))
        except httpx.HTTPError as err:
            out.append(Check(name, False, f"{base.rstrip('/')}/api/health {_status(err)}"))
    return out


def check_naive(settings, transport: Transport = None) -> list[Check]:
    if not settings.naive_api_key:
        return [Check("Naïve", False, "NAIVE_API_KEY is not set")]
    from app.naive.client import API_VERSION, _api_root

    names: set[str] = set()
    try:
        with _http(_api_root(settings.naive_api_base), transport,
                   Authorization=f"Bearer {settings.naive_api_key}",
                   **{"Vetta-Version": API_VERSION}) as http:
            after = None
            for _ in range(10):
                r = http.get("/agents", params={"limit": 100, **({"after": after} if after else {})})
                if r.status_code == 429:
                    wait = r.headers.get("Retry-After")
                    return [Check("Naïve", False, "at capacity (429)"
                                  + (f", retry after {wait} s" if wait else ""))]
                r.raise_for_status()
                body = r.json()
                names |= {str(a.get("name")) for a in body.get("data") or []
                          if not a.get("archived") and a.get("status") != "archived"}
                after = body.get("next_cursor")
                if not body.get("has_more") or not after:
                    break
    except httpx.HTTPError as err:
        return [Check("Naïve", False, f"the key's agent list {_status(err)}")]
    missing = sorted(set(AGENT_NAMES) - names)
    if missing:
        return [Check("Naïve", False, f"key works; missing agents: {', '.join(missing)} "
                                      "(run naive up)")]
    return [Check("Naïve", True, f"key works; all {len(AGENT_NAMES)} agents exist "
                                 "(incl. ceo-assistant); no 429")]


def check_agentmail(settings, transport: Transport = None) -> list[Check]:
    from app.devtools import WEBHOOK_CLIENT_ID, hook_url

    if not settings.agentmail_api_key:
        return [Check("AgentMail", False, "AGENTMAIL_API_KEY is not set")]
    out = []
    try:
        with _http(settings.agentmail_base_url, transport,
                   Authorization=f"Bearer {settings.agentmail_api_key}") as http:
            for label, inbox in (("clients", settings.inbox_clients),
                                 ("hiring", settings.inbox_hiring)):
                if not inbox:
                    out.append(Check(f"AgentMail {label} inbox", False,
                                     f"MAIL_INBOX_{label.upper()} is not set"))
                    continue
                r = http.get(f"/v0/inboxes/{quote(inbox, safe='@')}")
                out.append(Check(f"AgentMail {label} inbox", r.is_success,
                                 "exists" if r.is_success else f"answered {r.status_code}"))
            r = http.get("/v0/webhooks")
            r.raise_for_status()
            ours = [w for w in r.json().get("webhooks", []) if w.get("client_id") ==
                    WEBHOOK_CLIENT_ID]
    except httpx.HTTPError as err:
        return out + [Check("AgentMail", False, f"the key {_status(err)}")]
    if not settings.public_url:
        out.append(Check("AgentMail webhook", False, "UTAX_PUBLIC_URL is not set"))
    elif not ours:
        out.append(Check("AgentMail webhook", False, "none: run devtools rotate-host"))
    else:
        want = hook_url(settings.public_url)
        same = ours[0].get("url") == want
        out.append(Check("AgentMail webhook", same, f"points at {want}" if same else
                         "points elsewhere (an old tunnel?): run devtools rotate-host "
                         f"{settings.public_url}"))
    return out


def check_elevenlabs(settings, transport: Transport = None) -> list[Check]:
    if not settings.elevenlabs_api_key:
        return [Check("ElevenLabs", False, "ELEVENLABS_API_KEY is not set")]
    try:
        with _http("https://api.elevenlabs.io", transport,
                   **{"xi-api-key": settings.elevenlabs_api_key}) as http:
            r = http.get("/v1/user/subscription")
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPError as err:
        return [Check("ElevenLabs", False, f"the key {_status(err)}")]
    try:
        left = int(body["character_limit"]) - int(body["character_count"])
    except (KeyError, TypeError, ValueError):
        return [Check("ElevenLabs", False, "key works; the subscription has no character "
                                           "counts")]
    return [Check("ElevenLabs", left >= MIN_TTS_CHARACTERS,
                  f"key works; {left:,} characters left"
                  + ("" if left >= MIN_TTS_CHARACTERS else f" (under {MIN_TTS_CHARACTERS:,})"))]


def check_openai(settings, transport: Transport = None) -> list[Check]:
    """Task 40a: only when ADAPTER_TTS / ADAPTER_REALTIME / ADAPTER_STT names openai. The
    models list is free; each switched-on port's model must be on the key."""
    from app.gateway.registry import AdapterNotReady, _voice_choice

    try:
        uses = [p for p in ("stt", "realtime", "tts") if _voice_choice(settings, p) == "openai"]
    except AdapterNotReady as err:
        return [Check("OpenAI", False, str(err)[:160])]
    if not uses:
        return []
    switches = ", ".join(f"ADAPTER_{p.upper()}=openai" for p in uses)
    if not settings.openai_api_key:
        return [Check("OpenAI", False, f"OPENAI_API_KEY is not set ({switches})")]
    try:
        with _http("https://api.openai.com/v1", transport,
                   Authorization=f"Bearer {settings.openai_api_key}") as http:
            r = http.get("/models")
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPError as err:
        return [Check("OpenAI", False, f"the key {_status(err)}")]
    have = {str(m.get("id")) for m in (body.get("data") or []) if isinstance(m, dict)}         if isinstance(body, dict) else set()
    wanted = {"stt": (os.environ.get("OPENAI_STT_MODEL", "gpt-4o-transcribe"), "whisper-1"),
              "realtime": (os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime"),),
              "tts": (os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),)}
    missing = [f"{p} ({wanted[p][0]})" for p in uses if not any(m in have for m in wanted[p])]
    return [Check("OpenAI", not missing, f"key works; {', '.join(uses)} models on the key"
                  if not missing else f"key works; no model for {', '.join(missing)}")]


MIN_APIFY_USD = 1.0  # ten capped runs ($0.10 each) left this month


def check_apify(settings, transport: Transport = None) -> list[Check]:
    """Task 40c: only with ADAPTER_SCRAPE=apify. The token (users/me), both actors the
    adapter runs (read, not run: free) and the month's usage against its limit."""
    if settings.adapters.get("scrape") != "apify":
        return []
    if not settings.apify_token:
        return [Check("Apify", False, "APIFY_TOKEN is not set (ADAPTER_SCRAPE=apify)")]
    from app.gateway.adapters.apify import API, PAGE_ACTOR, SEARCH_ACTOR

    try:
        with _http(API, transport, Authorization=f"Bearer {settings.apify_token}") as http:
            http.get("/users/me").raise_for_status()
            for actor in (PAGE_ACTOR, SEARCH_ACTOR):
                http.get(f"/acts/{actor}").raise_for_status()
            limits = http.get("/users/me/limits")
            body = limits.json() if limits.is_success else {}
    except httpx.HTTPError as err:
        return [Check("Apify", False, f"the token or an actor {_status(err)}")]
    data = body.get("data") if isinstance(body, dict) else None
    try:
        used = float(data["current"]["monthlyUsageUsd"])
        cap = float(data["limits"]["maxMonthlyUsageUsd"])
    except (KeyError, TypeError, ValueError):
        return [Check("Apify", True, "token works; both actors reachable (usage unknown)")]
    left = cap - used
    return [Check("Apify", left >= MIN_APIFY_USD,
                  f"token works; both actors reachable; ${left:,.2f} of ${cap:,.2f} left "
                  "this month" + ("" if left >= MIN_APIFY_USD else
                                  f" (under ${MIN_APIFY_USD:.2f})"))]


def check_social(settings, transport: Transport = None) -> list[Check]:
    identity = settings.naive_social_identity
    if not identity or not settings.naive_api_key:
        return [Check("Naïve social", False, "NAIVE_SOCIAL_IDENTITY or NAIVE_API_KEY is not set")]
    from app.naive.client import API_VERSION, _api_root

    try:
        with _http(_api_root(settings.naive_api_base), transport,
                   Authorization=f"Bearer {settings.naive_api_key}",
                   **{"Vetta-Version": API_VERSION}) as http:
            r = http.get(f"/identities/{quote(identity, safe='')}/social/accounts")
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPError as err:
        return [Check("Naïve social", False, f"{identity}'s accounts {_status(err)}")]
    rows = body.get("data") if isinstance(body, dict) else body
    found: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            for key in ("platform", "provider", "type", "network"):
                value = str(row.get(key) or "").lower()
                if value in ("x", "twitter"):
                    found.add("X")
                elif value == "linkedin":
                    found.add("LinkedIn")
    missing = sorted({"X", "LinkedIn"} - found)
    return [Check("Naïve social", not missing,
                  f"{identity} has X and LinkedIn" if not missing else
                  f"{identity} is missing {', '.join(missing)} (connect it in the portal)")]


def check_files(settings) -> list[Check]:
    from app.gateway.talk_phrases import PhraseError, load
    from app.gateway.templates import SPECS, Templates, _problems

    templates = Templates(settings.templates_dir, settings.memo_template_path)
    bad, own = [], 0
    for name, spec in SPECS.items():
        text, origin = templates.source(name)
        if origin != "builtin":
            own += 1
            problems = _problems(name, text, spec)
            if problems:
                bad.append(f"{name} ({problems[0]})")
    out = [Check("templates", not bad, f"{own} files + built-ins load" if not bad
                 else "falls back to the built-in: " + "; ".join(bad))]
    try:
        phrases = load(settings.talk_phrases_path)
        out.append(Check("talk phrases", True, f"{len(phrases)} lines load"))
    except (PhraseError, OSError) as err:
        out.append(Check("talk phrases", False, str(err)[:160]))
    docs = settings.data_dir / "docs"
    try:
        docs.mkdir(parents=True, exist_ok=True)
        probe = docs / ".preflight-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        out.append(Check("docs folder", True, f"{docs} is writable"))
    except OSError as err:
        out.append(Check("docs folder", False, f"{docs} is not writable ({type(err).__name__})"))
    try:
        free = shutil.disk_usage(settings.data_dir if settings.data_dir.exists()
                                 else settings.data_dir.parent).free
        out.append(Check("disk", free >= MIN_FREE_BYTES, f"{free / (1 << 30):.1f} GB free"
                         + ("" if free >= MIN_FREE_BYTES else " (under 1 GB)")))
    except OSError as err:
        out.append(Check("disk", False, f"unknown ({type(err).__name__})"))
    return out


def preflight(settings, transport: Transport = None,
              out: Callable[[str], None] = print, color: bool = True) -> int:
    checks: list[Check] = []
    for run in (check_gateway, check_naive, check_agentmail, check_elevenlabs, check_apify, check_openai,
                check_social):
        try:
            checks += run(settings, transport)
        except Exception as err:  # noqa: BLE001 - one broken check never hides the rest
            checks.append(Check(run.__name__.removeprefix("check_"), False,
                                f"check failed ({type(err).__name__})"))
    checks += check_files(settings)
    for check in checks:
        out(check.line(color))
    reds = [c for c in checks if not c.ok]
    out(f"{len(checks) - len(reds)} green, {len(reds)} red")
    return 1 if reds else 0


# ---------------------------------------------------------------- archive-log


def gateway_running(port: int = 8000) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def archive_log(settings, *, yes: bool, out: Callable[[str], None] = print,
                running: Callable[[], bool] = gateway_running) -> int:
    if not yes:
        out("refused: this moves the live event log and docs/ away; run with --yes")
        return 2
    if settings.store != "jsonl":
        out(f"refused: STORE={settings.store}; archive that database by hand")
        return 2
    if running():
        out("refused: the gateway is running on port 8000; stop it first")
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = settings.data_dir / "archive" / stamp
    moved = []
    for source in (settings.events_file, settings.data_dir / "docs"):
        if source.exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target / source.name))
            moved.append(source.name)
    if not moved:
        out("nothing to archive: no event log and no docs/")
        return 0
    out(f"archived {', '.join(moved)} -> {target}")
    out("the next start is a clean log (case_1 again)")
    return 0


__all__ = ["Check", "archive_log", "gateway_running", "load_env", "preflight"]
if __name__ == "__main__":  # pragma: no cover
    sys.exit("use: python -m app.devtools preflight | archive-log --yes")
