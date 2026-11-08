from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.gateway.adapters.jsonl_store import JsonlEventStore
from app.gateway.perform import Gateway
from app.gateway.registry import build_ports
from app.gateway.settings import REPO_ROOT, Settings

HAPPY_BODY = (
    "Hey U-Tax! We're a Delaware C-corp. Delaware says we owe a big franchise tax.\n"
    "We have 10,000,000 authorized shares, 8,000,000 issued, and about $500k in assets. "
    "No foreign owners. Can you check?"
)
HAPPY_QUOTES = {
    "incorporated_delaware": "We're a Delaware C-corp",
    "authorized_shares": "10,000,000",
    "issued_shares": "8,000,000",
    "gross_assets": "$500k",
    "foreign_owner_25": "No foreign owners",
}


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 21, 14, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class Harness:
    def __init__(self, tmp: Path, **settings_kw) -> None:
        self.tmp = tmp
        self.clock = FakeClock()
        self.slept: list[float] = []
        settings_kw.setdefault("memo_template_path", tmp / "no-template.md")
        settings_kw.setdefault("templates_dir", tmp / "no-templates")  # built-in texts
        settings_kw.setdefault("public_talk_url", "https://u-tax.test/talk")
        settings_kw.setdefault("inbox_hiring", "jobs@u-tax.test")
        self.settings = Settings(data_dir=tmp / "data", fixtures_dir=REPO_ROOT / "fixtures",
                                 **settings_kw)
        self.ports = build_ports(self.settings)
        self.store = JsonlEventStore(self.settings.events_file)
        self.gw = self._gateway(self.store)

    def _gateway(self, store) -> Gateway:
        return Gateway(store, self.ports, self.settings, clock=self.clock,
                       sleep=self.slept.append)

    def restart(self) -> Gateway:
        self.store = JsonlEventStore(self.settings.events_file)
        self.gw = self._gateway(self.store)
        return self.gw

    def do(self, name: str, **args):
        return self.gw.perform(name, args)

    def ok(self, name: str, **args) -> dict:
        result = self.do(name, **args)
        assert result.status == "done", (name, result.as_dict())
        return result.data

    def types(self) -> list[str]:
        return [e.type for e in self.store.since(0)]

    def events(self, type_: str):
        return [e for e in self.store.since(0) if e.type == type_]

    def outbox(self) -> list[Path]:
        return sorted((self.settings.data_dir / "outbox").glob("local-msg-*.json"))

    # -------------------------------------------------------- flows

    def new_case(self, body: str = HAPPY_BODY, sender: str = "founder@team7.dev",
                 subject: str = "free franchise tax checkup?") -> str:
        return self.ok("inject_email", **{"from": sender, "subject": subject,
                                          "body": body})["case_id"]

    def to_qa_passed(self, case_id: str | None = None, *, docs: bool = True) -> str:
        """QA_PASSED, with the documents prepared (v1.15; the status stays QA_PASSED)."""
        case_id = case_id or self.new_case()
        self.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
                incorporated_delaware=True, foreign_owner_25=False)
        self.ok("compute_tax", case_id=case_id)
        data = self.ok("qa_check", case_id=case_id)
        assert data["passed"], data
        if docs:
            self.ok("prepare_documents", case_id=case_id)
        return case_id

    def approve_pending(self, result) -> None:
        assert result.status == "needs_approval", result.as_dict()
        self.ok("approve", approval_id=result.approval_id, decision="allow", note="ok")

    def to_sent(self, case_id: str | None = None) -> str:
        case_id = self.to_qa_passed(case_id)
        first = self.do("send_memo", case_id=case_id)
        if first.status == "needs_approval":
            self.approve_pending(first)
            self.ok("send_memo", case_id=case_id)
        return case_id


@pytest.fixture
def h(tmp_path) -> Harness:
    return Harness(tmp_path)


@pytest.fixture
def make_harness(tmp_path):
    counter = iter(range(100))

    def make(**kw) -> Harness:
        return Harness(tmp_path / f"h{next(counter)}", **kw)

    return make
