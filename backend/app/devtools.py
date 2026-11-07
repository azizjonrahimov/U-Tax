"""Dev CLI against a running gateway (local driver demo).

    python -m app.devtools inject ../fixtures/inbox/01-team7-happy.eml
    python -m app.devtools inject --inbox hiring ../fixtures/hiring/01-applicant-ea.eml
    python -m app.devtools approve-all            # plays the CEO clicking Approve
    python -m app.devtools state
    python -m app.devtools agentmail-webhook https://<public-host>   # after a tunnel restart
    python -m app.devtools rotate-host https://<public-host>   # UTAX_PUBLIC_URL + the webhook

Env: UTAX_BASE (default http://localhost:8000), ADMIN_TOKEN.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import email.utils
import json
import os
import sys
import time
from pathlib import Path

import httpx


def _client() -> httpx.Client:
    headers = {}
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=os.environ.get("UTAX_BASE", "http://localhost:8000"),
                        headers=headers, timeout=30)


def _eml(path: Path) -> dict:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))  # HTML-only mail too
    body = part.get_content() if part else ""
    if part is not None and part.get_content_subtype() == "html":
        from app.gateway.masking import html_to_text

        body = html_to_text(body)
    return {"from": email.utils.parseaddr(str(msg.get("From", "")))[1],
            "subject": str(msg.get("Subject", "")),
            "body": body.strip(),
            "message_id": str(msg.get("Message-ID", "")).strip("<> ") or None}


def cmd_inject(args: argparse.Namespace) -> int:
    with _client() as http:
        for name in args.files:
            payload = _eml(Path(name))
            if args.inbox != "clients":
                payload["inbox"] = args.inbox
            r = http.post("/api/dev/inject-email", json=payload)
            print(name, r.status_code, r.text)
            if r.status_code >= 400:
                return 1
    return 0


def cmd_approve_all(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.seconds
    approved: set[str] = set()
    with _client() as http:
        while time.monotonic() < deadline:
            brief = http.get("/api/ceo/brief").json()
            pending = [p for p in brief.get("pending_approvals", [])
                       if p["approval_id"] not in approved]
            for p in pending:
                r = http.post("/api/approve", json={"approval_id": p["approval_id"],
                                                    "decision": "allow", "note": "devtools"})
                print("approve", p["approval_id"], p["summary"], "->", r.status_code)
                approved.add(p["approval_id"])
            time.sleep(args.every)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    with _client() as http:
        print(json.dumps(http.get("/api/state").json(), indent=1))
    return 0


WEBHOOK_CLIENT_ID = "utax-gateway"
AGENTMAIL_API = "https://api.agentmail.to"


def env_file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip('"')
    return values


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    """Replace KEY=... lines in a .env file (append the missing ones); nothing else moves."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    left = dict(updates)
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip() if "=" in line else ""
        if name in left and not line.lstrip().startswith("#"):
            lines[i] = f"{name}={left.pop(name)}"
    lines += [f"{name}={value}" for name, value in left.items()]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    tmp.replace(path)


def hook_url(public: str) -> str:
    """https://<host>[/hooks/agentmail]: only an https host, the path added when missing."""
    url = public.strip().rstrip("/")
    if not url.startswith("https://") or " " in url:
        raise SystemExit("the webhook needs the public https URL (https://<public-host>)")
    return url if url.endswith("/hooks/agentmail") else url + "/hooks/agentmail"


def agentmail_webhook(public: str, env_path: Path,
                      transport: httpx.BaseTransport | None = None) -> str:
    """Point the U-Tax AgentMail webhook (message.received, our two inboxes) at `public`.
    A new secret goes straight into `env_path` (AGENTMAIL_WEBHOOK_SECRET), never printed.
    Returns what happened: "created", "updated", "recreated" or "unchanged"."""
    env = {**env_file_values(env_path), **{k: v for k, v in os.environ.items()
                                           if k.startswith(("AGENTMAIL_", "MAIL_INBOX_"))}}
    key = env.get("AGENTMAIL_API_KEY", "")
    inboxes = [env.get("MAIL_INBOX_CLIENTS", ""), env.get("MAIL_INBOX_HIRING", "")]
    if not key or not all(inboxes):
        raise SystemExit("needs AGENTMAIL_API_KEY, MAIL_INBOX_CLIENTS and MAIL_INBOX_HIRING")
    url = hook_url(public)
    with httpx.Client(base_url=env.get("AGENTMAIL_BASE_URL") or AGENTMAIL_API,
                      headers={"Authorization": f"Bearer {key}",
                               "User-Agent": "utax-gateway-devtools"},
                      timeout=20, transport=transport) as http:
        listed = http.get("/v0/webhooks")
        listed.raise_for_status()
        ours = [w for w in listed.json().get("webhooks", [])
                if w.get("client_id") == WEBHOOK_CLIENT_ID]
        body = {"url": url, "event_types": ["message.received"], "inbox_ids": inboxes,
                "client_id": WEBHOOK_CLIENT_ID}
        if ours:
            hook = ours[0]
            hook_id = hook.get("webhook_id") or hook.get("id")
            if hook.get("url") == url:
                return "unchanged"
            patched = http.patch(f"/v0/webhooks/{hook_id}", json={"url": url})
            if patched.is_success and patched.json().get("url") == url:
                return "updated"  # same secret: no restart needed
            http.delete(f"/v0/webhooks/{hook_id}").raise_for_status()
            outcome = "recreated"
        else:
            outcome = "created"
        made = http.post("/v0/webhooks", json=body)
        made.raise_for_status()
        secret = made.json().get("secret", "")
        if not secret.startswith("whsec_"):
            raise SystemExit("AgentMail answered without a whsec_ secret; nothing written")
        set_env_values(env_path, {"AGENTMAIL_WEBHOOK_SECRET": secret})
        return outcome


def rotate_host(public: str, env_path: Path,
                transport: httpx.BaseTransport | None = None) -> str:
    """A quick tunnel's new host, in one go: UTAX_PUBLIC_URL (make_ad's images) in the
    .env, then the AgentMail webhook. Returns the webhook outcome."""
    base = hook_url(public).removesuffix("/hooks/agentmail")
    set_env_values(env_path, {"UTAX_PUBLIC_URL": base})
    return agentmail_webhook(base, env_path, transport=transport)


def cmd_rotate_host(args: argparse.Namespace) -> int:
    outcome = rotate_host(args.url, Path(args.env))
    base = hook_url(args.url).removesuffix("/hooks/agentmail")
    restart = outcome in ("created", "recreated")
    print(f"UTAX_PUBLIC_URL={base}")
    print(f"AgentMail webhook {outcome} -> {base}/hooks/agentmail")
    print("restart the gateway (UTAX_PUBLIC_URL" + (" and the new webhook secret" if restart
                                                     else "") + " are read at boot)")
    return 0


def cmd_agentmail_webhook(args: argparse.Namespace) -> int:
    outcome = agentmail_webhook(args.url, Path(args.env))
    note = {"created": "secret written; restart the gateway",
            "recreated": "new secret written; restart the gateway",
            "updated": "same secret; no restart needed",
            "unchanged": "already pointing there"}[outcome]
    print(f"AgentMail webhook {outcome} -> {hook_url(args.url)} ({note})")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # "→" in summaries on a cp1251/cp437 console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="python -m app.devtools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inject", help="post fixture .eml files to /api/dev/inject-email")
    p.add_argument("files", nargs="+")
    p.add_argument("--inbox", choices=("clients", "hiring"), default="clients")
    p.set_defaults(func=cmd_inject)
    p = sub.add_parser("approve-all", help="approve every pending approval for a while")
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--every", type=float, default=2)
    p.set_defaults(func=cmd_approve_all)
    p = sub.add_parser("state", help="print /api/state")
    p.set_defaults(func=cmd_state)
    p = sub.add_parser("agentmail-webhook",
                       help="point the AgentMail webhook at https://<public-host>/hooks/agentmail")
    p.add_argument("url", help="the public https URL (the tunnel host)")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"),
                   help="the .env that gets AGENTMAIL_WEBHOOK_SECRET (default backend/.env)")
    p.set_defaults(func=cmd_agentmail_webhook)
    p = sub.add_parser("rotate-host",
                       help="a new tunnel host: UTAX_PUBLIC_URL and the AgentMail webhook")
    p.add_argument("url", help="the new public https URL (the tunnel host)")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"),
                   help="the .env to update (default backend/.env)")
    p.set_defaults(func=cmd_rotate_host)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
