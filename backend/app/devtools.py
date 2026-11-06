"""Dev CLI against a running gateway (local driver demo).

    python -m app.devtools inject ../fixtures/inbox/01-team7-happy.eml
    python -m app.devtools inject --inbox hiring ../fixtures/hiring/01-applicant-ea.eml
    python -m app.devtools approve-all            # plays the CEO clicking Approve
    python -m app.devtools state

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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
