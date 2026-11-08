"""Dev CLI against a running gateway (local driver demo).

    python -m app.devtools inject ../fixtures/inbox/01-team7-happy.eml
    python -m app.devtools inject --inbox hiring ../fixtures/hiring/01-applicant-ea.eml
    python -m app.devtools approve-all            # plays the CEO clicking Approve
    python -m app.devtools state
    python -m app.devtools agentmail-webhook https://<public-host>   # after a tunnel restart
    python -m app.devtools rotate-host https://<public-host>   # UTAX_PUBLIC_URL + the webhook
    python -m app.devtools stripe-webhook https://<public-host>  # task 40b: /hooks/stripe
    python -m app.devtools preflight              # demo checks, read-only (task 37)
    python -m app.devtools archive-log --yes      # gateway stopped: a clean log (task 37)

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
    """https://<host>[/hooks/agentmail]: only an https host (no query, fragment, user or
    control characters, which could also break the .env line), the path added when missing."""
    from urllib.parse import urlsplit

    url = public.strip().rstrip("/")
    bad = SystemExit("needs the public https URL (https://<public-host>)")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise bad
    try:
        parts = urlsplit(url)
    except ValueError as err:
        raise bad from err
    if (parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment
            or parts.username or parts.password
            or parts.path not in ("", "/hooks/agentmail")):
        raise bad
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


STRIPE_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")


def stripe_hook_url(public: str) -> str:
    base = hook_url(public).removesuffix("/hooks/agentmail")
    return base + "/hooks/stripe"


def stripe_webhook(public: str, env_path: Path,
                   transport: httpx.BaseTransport | None = None) -> str:
    """Task 40b: point the U-Tax Stripe webhook endpoint (metadata utax=gateway) at
    `public`/hooks/stripe. A new endpoint's secret goes into `env_path`
    (STRIPE_WEBHOOK_SECRET), never printed. A live key needs STRIPE_LIVE=1.
    Returns "created", "updated" or "unchanged"."""
    from app.gateway.adapters.stripe_pay import key_mode

    env = {**env_file_values(env_path), **{k: v for k, v in os.environ.items()
                                           if k.startswith("STRIPE_")}}
    key = env.get("STRIPE_SECRET_KEY", "")
    mode = key_mode(key)
    if not mode:
        raise SystemExit("needs STRIPE_SECRET_KEY (an sk_test_ key)")
    if mode == "live" and env.get("STRIPE_LIVE") != "1":
        raise SystemExit("a live Stripe key needs STRIPE_LIVE=1 (the owner's word)")
    url = stripe_hook_url(public)
    with httpx.Client(base_url="https://api.stripe.com",
                      headers={"Authorization": f"Bearer {key}",
                               "User-Agent": "utax-gateway-devtools"},
                      timeout=20, transport=transport) as http:
        listed = http.get("/v1/webhook_endpoints", params={"limit": 100})
        listed.raise_for_status()
        ours = [w for w in listed.json().get("data", [])
                if (w.get("metadata") or {}).get("utax") == "gateway"]
        if ours:
            hook = ours[0]
            if hook.get("url") == url and sorted(hook.get("enabled_events") or []) == sorted(
                    STRIPE_EVENTS):
                return "unchanged"
            data = {"url": url, **{f"enabled_events[{i}]": e
                                   for i, e in enumerate(STRIPE_EVENTS)}}
            http.post(f"/v1/webhook_endpoints/{hook['id']}", data=data).raise_for_status()
            return "updated"  # same secret: no restart needed
        data = {"url": url, "metadata[utax]": "gateway",
                "description": "U-Tax gateway: paid filings",
                **{f"enabled_events[{i}]": e for i, e in enumerate(STRIPE_EVENTS)}}
        made = http.post("/v1/webhook_endpoints", data=data)
        made.raise_for_status()
        secret = made.json().get("secret", "")
        if not secret.startswith("whsec_"):
            raise SystemExit("Stripe answered without a whsec_ secret; nothing written")
        set_env_values(env_path, {"STRIPE_WEBHOOK_SECRET": secret})
        return "created"


# ---------------------------------------------------------------- D38 market wallets

PIXELAGENT_ENV = Path(__file__).resolve().parents[2] / "contractors" / "pixelagent" / ".env"
PIXELAGENT_BASE = "http://127.0.0.1:8020"


def _address_of(key: str) -> str:
    from mpp.methods.tempo import TempoAccount

    return TempoAccount.from_key(key if key.startswith("0x") else "0x" + key).address


def market_wallets(env_path: Path, contractor_env: Path, *, out=print,
                   transport: httpx.BaseTransport | None = None,
                   new_key=None, fund=None) -> dict[str, str]:
    """D38 §2.3: the firm's and PixelAgent's testnet wallets, funded from the faucet.
    Idempotent: a key already in the .env is kept (never overwritten) and only its address
    and balance are printed. Writes FIRM_WALLET_KEY and MARKET_CONTRACTORS (backend .env);
    PIXELAGENT_KEY, MPP_SECRET_KEY and MARKET_TOKEN (the contractor's .env). Prints only
    addresses and balances: never a key or a token."""
    import secrets

    from app.gateway.adapters import tempo_wallet as tw

    new_key = new_key or tw.new_key
    fund = fund or (lambda address: tw.faucet(address, transport=transport))
    backend = env_file_values(env_path)
    contractor = env_file_values(contractor_env)
    addresses: dict[str, str] = {}
    backend_updates: dict[str, str] = {}
    contractor_updates: dict[str, str] = {}
    for who, values, var, updates in (("firm", backend, "FIRM_WALLET_KEY", backend_updates),
                                      ("pixelagent", contractor, "PIXELAGENT_KEY",
                                       contractor_updates)):
        key = values.get(var, "")
        if key:
            addresses[who] = _address_of(key)
            continue
        key, address = new_key()
        fund(address)
        updates[var] = key
        addresses[who] = address
    if not contractor.get("MPP_SECRET_KEY"):
        contractor_updates["MPP_SECRET_KEY"] = secrets.token_hex(32)
    token = contractor.get("MARKET_TOKEN") or secrets.token_hex(16)
    if not contractor.get("MARKET_TOKEN"):
        contractor_updates["MARKET_TOKEN"] = token
    if "pixelagent|" not in backend.get("MARKET_CONTRACTORS", "") or "PIXELAGENT_KEY" in \
            contractor_updates:
        base = contractor.get("PIXELAGENT_PUBLIC_URL") or PIXELAGENT_BASE
        backend_updates["MARKET_CONTRACTORS"] = f"pixelagent|{token}|{addresses['pixelagent']}|{base}"
    if contractor_updates:
        contractor_env.parent.mkdir(parents=True, exist_ok=True)
        set_env_values(contractor_env, contractor_updates)
    if backend_updates:
        set_env_values(env_path, backend_updates)
    for who, address in addresses.items():
        try:
            balance = f"{tw.balance_of(address, transport=transport):,.2f} pathUSD"
        except Exception as err:  # noqa: BLE001 - the RPC down: still print the address
            balance = f"balance unknown ({type(err).__name__})"
        out(f"{who}: {address}  {balance}")
    written = sorted(backend_updates) + sorted(contractor_updates)
    out("written: " + (", ".join(written) if written else "nothing (all keys already set)"))
    return addresses


def cmd_market_wallets(args: argparse.Namespace) -> int:
    market_wallets(Path(args.env), Path(args.contractor_env))
    return 0


def cmd_market_balance(args: argparse.Namespace) -> int:
    """The firm wallet and every MARKET_CONTRACTORS wallet: addresses and pathUSD only."""
    from app.gateway.adapters import tempo_wallet as tw
    from app.gateway.settings import parse_contractors

    env = env_file_values(Path(args.env))
    rows = []
    if env.get("FIRM_WALLET_KEY"):
        rows.append(("firm", _address_of(env["FIRM_WALLET_KEY"])))
    rows += [(c.name, c.wallet) for c in parse_contractors(env.get("MARKET_CONTRACTORS", ""))]
    for who, address in rows:
        print(f"{who}: {address}  {tw.balance_of(address):,.2f} pathUSD")
    return 0 if rows else 1


def cmd_market_faucet(args: argparse.Namespace) -> int:
    """Testnet only: fund one address from the Tempo Moderato faucet."""
    import re

    from app.gateway.adapters import tempo_wallet as tw

    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", args.address):
        print("needs a 0x address", file=sys.stderr)
        return 1
    tw.faucet(args.address)
    print(f"{args.address}: {tw.balance_of(args.address):,.2f} pathUSD")
    return 0


def cmd_stripe_webhook(args: argparse.Namespace) -> int:
    outcome = stripe_webhook(args.url, Path(args.env))
    note = {"created": "secret written; restart the gateway",
            "updated": "same secret; no restart needed",
            "unchanged": "already pointing there"}[outcome]
    print(f"Stripe webhook {outcome} -> {stripe_hook_url(args.url)} ({note})")
    return 0


def rotate_host(public: str, env_path: Path,
                transport: httpx.BaseTransport | None = None) -> str:
    """A quick tunnel's new host, in one go: UTAX_PUBLIC_URL (make_ad's images) in the
    .env, then the AgentMail webhook. Returns the webhook outcome."""
    base = hook_url(public).removesuffix("/hooks/agentmail")
    outcome = agentmail_webhook(base, env_path, transport=transport)  # first: a failure
    set_env_values(env_path, {"UTAX_PUBLIC_URL": base})  # leaves the .env as it was
    return outcome


def cmd_rotate_host(args: argparse.Namespace) -> int:
    outcome = rotate_host(args.url, Path(args.env))
    base = hook_url(args.url).removesuffix("/hooks/agentmail")
    restart = outcome in ("created", "recreated")
    print(f"UTAX_PUBLIC_URL={base}")
    print(f"AgentMail webhook {outcome} -> {base}/hooks/agentmail")
    print("restart the gateway (UTAX_PUBLIC_URL" + (" and the new webhook secret" if restart
                                                     else "") + " are read at boot)")
    return 0


def cmd_set_ceo_password(args: argparse.Namespace) -> int:
    """v1.13: the CEO's password from stdin (a hidden prompt on a terminal), scrypt-hashed
    into the .env as CEO_PASSWORD_HASH. Never echoed or printed."""
    import getpass

    from app.auth import hash_password

    if sys.stdin.isatty():
        password = getpass.getpass("CEO password: ")
        if getpass.getpass("again: ") != password:
            print("the two didn't match; nothing written", file=sys.stderr)
            return 1
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    if len(password) < 10:
        print("use at least 10 characters; nothing written", file=sys.stderr)
        return 1
    set_env_values(Path(args.env), {"CEO_PASSWORD_HASH": hash_password(password)})
    print("CEO_PASSWORD_HASH written; restart the gateway")
    return 0


def cmd_agentmail_webhook(args: argparse.Namespace) -> int:
    outcome = agentmail_webhook(args.url, Path(args.env))
    note = {"created": "secret written; restart the gateway",
            "recreated": "new secret written; restart the gateway",
            "updated": "same secret; no restart needed",
            "unchanged": "already pointing there"}[outcome]
    print(f"AgentMail webhook {outcome} -> {hook_url(args.url)} ({note})")
    return 0


def _settings_from(env: str):
    from app.gateway.settings import Settings
    from app.preflight import load_env

    load_env(Path(env))
    return Settings.from_env()


def cmd_preflight(args: argparse.Namespace) -> int:
    """Task 37: read-only checks before a demo recording; exit 1 on any red."""
    from app.preflight import preflight

    return preflight(_settings_from(args.env), color=sys.stdout.isatty())


def cmd_archive_log(args: argparse.Namespace) -> int:
    """Task 37: the live event log and docs/ into data/archive/<UTC time>/ (gateway stopped)."""
    from app.preflight import archive_log

    return archive_log(_settings_from(args.env), yes=args.yes)


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
    p = sub.add_parser("market-wallets",
                       help="D38: the firm's and PixelAgent's testnet wallets, funded")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"))
    p.add_argument("--contractor-env", default=str(PIXELAGENT_ENV))
    p.set_defaults(func=cmd_market_wallets)
    p = sub.add_parser("market-balance", help="D38: pathUSD of the firm and contractor wallets")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"))
    p.set_defaults(func=cmd_market_balance)
    p = sub.add_parser("market-faucet", help="D38: fund an address on the Tempo testnet")
    p.add_argument("address")
    p.set_defaults(func=cmd_market_faucet)
    p = sub.add_parser("stripe-webhook",
                       help="point the Stripe webhook at https://<public-host>/hooks/stripe")
    p.add_argument("url", help="the public https URL (the tunnel host)")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"),
                   help="the .env that gets STRIPE_WEBHOOK_SECRET (default backend/.env)")
    p.set_defaults(func=cmd_stripe_webhook)
    p = sub.add_parser("set-ceo-password",
                       help="hash the CEO's password (read from stdin) into the .env")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"),
                   help="the .env to update (default backend/.env)")
    p.set_defaults(func=cmd_set_ceo_password)
    p = sub.add_parser("rotate-host",
                       help="a new tunnel host: UTAX_PUBLIC_URL and the AgentMail webhook")
    p.add_argument("url", help="the new public https URL (the tunnel host)")
    p.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"),
                   help="the .env to update (default backend/.env)")
    p.set_defaults(func=cmd_rotate_host)
    env_default = str(Path(__file__).resolve().parents[1] / ".env")
    p = sub.add_parser("preflight", help="read-only demo checks (exit 1 on any red)")
    p.add_argument("--env", default=env_default, help="the .env to read (default backend/.env)")
    p.set_defaults(func=cmd_preflight)
    p = sub.add_parser("archive-log",
                       help="move the event log and docs/ to data/archive/ (gateway stopped)")
    p.add_argument("--yes", action="store_true", help="really move them")
    p.add_argument("--env", default=env_default, help="the .env to read (default backend/.env)")
    p.set_defaults(func=cmd_archive_log)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
