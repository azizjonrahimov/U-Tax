"""Gateway settings from env. Empty token = that check is off (local dev), per contract §1."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

PORT_NAMES = ("mail", "llm", "crm", "device", "scrape", "toolbuy", "voice", "calendar",
              "social", "pay")
DEFAULT_ADAPTERS = {name: "local" for name in PORT_NAMES} | {"voice": "none", "pay": "none"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = BACKEND_DIR / "data"
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    memo_template_path: Path = REPO_ROOT / "naive" / "templates" / "memo.md"
    templates_dir: Path = REPO_ROOT / "naive" / "templates"  # contract §6d
    voice_prompt_path: Path = REPO_ROOT / "naive" / "prompts" / "voice-intake.md"
    ceo_console_url: str = "http://localhost:3200/ceo"
    public_mode: str = ""  # "event": the finale crowd shares one venue IP (60/min)
    store: str = "jsonl"
    events_path: Path | None = None
    database_url: str = ""
    adapters: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ADAPTERS))
    theater_token: str = ""
    admin_token: str = ""
    theater_origin: str = ""
    openai_api_key: str = ""
    cap_per_hour: int = 10
    spend_cap_usd: Decimal | None = None
    max_asks: int = 2
    ceo_email: str = ""
    ceo_digest_hours: int = 3
    inbox_clients: str = ""
    inbox_hiring: str = ""
    public_talk_url: str = ""  # the only URL a post may carry (v1.4)
    driver: str = ""  # "local": the in-process stand-in crew plays the agents (D26)
    driver_pace_seconds: float = 1.2
    tool_tokens: dict[str, str] = field(default_factory=dict)  # actor -> token (v1.3)
    # task 8 sponsor adapters (keys from .env; empty = that adapter can't start)
    agentmail_api_key: str = ""
    agentmail_webhook_secret: str = ""  # whsec_... (Svix); verifies /hooks/agentmail
    agentmail_base_url: str = "https://api.agentmail.to"
    hook_token: str = ""  # fallback for /hooks/*: ?token=<this> when no signing secret
    public_asset_hosts: frozenset[str] = frozenset()  # v1.6.4: post image_url hosts

    @property
    def events_file(self) -> Path:
        return self.events_path or (self.data_dir / "events.jsonl")

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(_env("UTAX_DATA_DIR") or BACKEND_DIR / "data")
        adapters = dict(DEFAULT_ADAPTERS)
        for name in PORT_NAMES:
            value = _env(f"ADAPTER_{name.upper()}")
            if value:
                adapters[name] = value.lower()
        spend_cap = _env("SPEND_CAP_USD")
        tokens = {actor: _env(f"UTAX_TOOL_TOKEN_{actor.upper()}")
                  for actor in ("cos", "intake", "tax", "qa", "acct", "mkt", "hr")}
        return cls(
            data_dir=data_dir,
            fixtures_dir=Path(_env("UTAX_FIXTURES_DIR") or REPO_ROOT / "fixtures"),
            memo_template_path=Path(_env("MEMO_TEMPLATE_PATH")
                                    or REPO_ROOT / "naive" / "templates" / "memo.md"),
            templates_dir=Path(_env("UTAX_TEMPLATES_DIR") or REPO_ROOT / "naive" / "templates"),
            voice_prompt_path=Path(_env("UTAX_VOICE_PROMPT")
                                   or REPO_ROOT / "naive" / "prompts" / "voice-intake.md"),
            ceo_console_url=_env("CEO_CONSOLE_URL") or "http://localhost:3200/ceo",
            public_mode=_env("PUBLIC_MODE").lower(),
            store=(_env("STORE") or "jsonl").lower(),
            events_path=Path(_env("EVENTS_PATH")) if _env("EVENTS_PATH") else None,
            database_url=_env("DATABASE_URL"),
            adapters=adapters,
            theater_token=_env("THEATER_TOKEN"),
            admin_token=_env("ADMIN_TOKEN"),
            theater_origin=_env("THEATER_ORIGIN"),
            openai_api_key=_env("OPENAI_API_KEY"),
            cap_per_hour=int(_env("CAP_PER_HOUR") or 10),
            spend_cap_usd=Decimal(spend_cap) if spend_cap else None,
            ceo_email=_env("CEO_EMAIL"),
            ceo_digest_hours=int(_env("CEO_DIGEST_HOURS") or 3),
            inbox_clients=_env("MAIL_INBOX_CLIENTS"),
            inbox_hiring=_env("MAIL_INBOX_HIRING"),
            public_talk_url=_env("PUBLIC_TALK_URL"),
            driver=_env("UTAX_DRIVER").lower(),
            driver_pace_seconds=float(_env("UTAX_DRIVER_PACE") or 1.2),
            tool_tokens={a: t for a, t in tokens.items() if t},
            agentmail_api_key=_env("AGENTMAIL_API_KEY"),
            agentmail_webhook_secret=_env("AGENTMAIL_WEBHOOK_SECRET"),
            agentmail_base_url=_env("AGENTMAIL_BASE_URL") or "https://api.agentmail.to",
            hook_token=_env("UTAX_HOOK_TOKEN"),
            public_asset_hosts=frozenset(h.strip().lower() for h in
                                         _env("PUBLIC_ASSET_HOSTS").split(",") if h.strip()),
        )
