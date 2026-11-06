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
    store: str = "jsonl"
    events_path: Path | None = None
    database_url: str = ""
    adapters: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ADAPTERS))
    tool_secret: str = ""
    theater_token: str = ""
    admin_token: str = ""
    theater_origin: str = ""
    openai_api_key: str = ""
    cap_per_hour: int = 10
    spend_cap_usd: Decimal | None = None
    max_asks: int = 2

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
        return cls(
            data_dir=data_dir,
            fixtures_dir=Path(_env("UTAX_FIXTURES_DIR") or REPO_ROOT / "fixtures"),
            memo_template_path=Path(_env("MEMO_TEMPLATE_PATH")
                                    or REPO_ROOT / "naive" / "templates" / "memo.md"),
            store=(_env("STORE") or "jsonl").lower(),
            events_path=Path(_env("EVENTS_PATH")) if _env("EVENTS_PATH") else None,
            database_url=_env("DATABASE_URL"),
            adapters=adapters,
            tool_secret=_env("UTAX_TOOL_SECRET"),
            theater_token=_env("THEATER_TOKEN"),
            admin_token=_env("ADMIN_TOKEN"),
            theater_origin=_env("THEATER_ORIGIN"),
            openai_api_key=_env("OPENAI_API_KEY"),
            cap_per_hour=int(_env("CAP_PER_HOUR") or 10),
            spend_cap_usd=Decimal(spend_cap) if spend_cap else None,
        )
