"""Port contract suites (design doc Test Plan: "Port contracts").

One suite per port, run against every adapter listed in that port's FACTORIES. Tonight
only the stand-ins are listed. Saturday, a sponsor adapter joins by adding a factory
`(tmp_path, faults) -> adapter`; `faults` is a FaultPlan whose errors the factory must make
the adapter raise (for HTTP adapters, via an httpx.MockTransport that answers 429/503/401).
Sponsor factories skip themselves when their key env var is missing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.gateway.adapters import local
from app.gateway.ports import (
    CrmContact,
    FaultPlan,
    LLMRequest,
    NotConfigured,
    OutMail,
    Post,
    PortError,
    RateLimited,
    Slot,
    Unavailable,
)
from app.gateway.settings import REPO_ROOT

FIXTURES = REPO_ROOT / "fixtures"


def _ids(factories: dict) -> list[str]:
    return list(factories)


# ---------------------------------------------------------------- mail

def _agentmail(tmp: Path, faults: FaultPlan):
    from app.gateway.adapters.agentmail import AgentMailMail
    from tests.adapters.fakes import FakeAgentMail

    fake = FakeAgentMail(faults)
    return AgentMailMail("am_test", inboxes={"clients": "checkup@u-tax.test"}, data_dir=tmp,
                         transport=fake.transport())


# Sponsor adapters run here against their documented API through an httpx MockTransport
# fake (tests/adapters/fakes.py); live calls are in tests/adapters/, skipped without keys.
MAIL = {"local": lambda tmp, faults: local.LocalMail(tmp, faults), "agentmail": _agentmail}


@pytest.fixture(params=_ids(MAIL))
def mail(request, tmp_path):
    faults = FaultPlan()
    return MAIL[request.param](tmp_path, faults), faults


def test_mail_send_returns_a_message_id(mail):
    port, _ = mail
    sent = port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k1")
    assert isinstance(sent.message_id, str) and sent.message_id


def test_mail_same_key_no_second_mail(mail):
    port, _ = mail
    first = port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k1")
    again = port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k1")
    other = port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k2")
    assert first == again
    assert other != first


def test_mail_maps_rate_limit(mail):
    port, faults = mail
    faults.add(RateLimited(retry_after=2))
    with pytest.raises(RateLimited) as info:
        port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k1")
    assert info.value.retryable and info.value.retry_after == 2
    # a failed send didn't claim the key: the retry goes out
    assert port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k1").message_id


def test_mail_bad_recipient_is_rejected_not_retryable(mail):
    port, _ = mail
    with pytest.raises(PortError) as info:
        port.send(OutMail(to="nobody", subject="s", body="b"), key="k1")
    assert not info.value.retryable


@pytest.mark.parametrize("eml", sorted((FIXTURES / "inbox").glob("*.eml")), ids=lambda p: p.name)
def test_mail_parses_fixture_inbox(mail, eml: Path):
    port, _ = mail
    parsed = port.parse_inbound(eml.read_bytes())
    assert parsed is not None
    assert "@" in parsed.sender and parsed.message_id and parsed.body


# ---------------------------------------------------------------- llm

LLM = {"local": lambda tmp, faults: local.LocalLLM(faults)}


@pytest.mark.parametrize("name", _ids(LLM))
def test_llm_reports_tokens_and_maps_errors(name, tmp_path):
    faults = FaultPlan()
    port = LLM[name](tmp_path, faults)
    result = port.complete(LLMRequest(system="Be terse.", prompt="Say hi."))
    assert isinstance(result.tokens_in, int) and isinstance(result.tokens_out, int)
    assert result.cost_usd is None or Decimal(result.cost_usd) >= 0
    faults.add(Unavailable())
    with pytest.raises(Unavailable):
        port.complete(LLMRequest(system="", prompt="x"))


# ---------------------------------------------------------------- crm

CRM = {"local": lambda tmp, faults: local.LocalCRM(tmp, faults)}


@pytest.mark.parametrize("name", _ids(CRM))
def test_crm_upsert_is_idempotent_and_maps_errors(name, tmp_path):
    faults = FaultPlan()
    port = CRM[name](tmp_path, faults)
    a = port.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    b = port.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    assert a == b
    port.set_stage("deal:case_7", "memo_sent")
    faults.add(RateLimited())
    with pytest.raises(RateLimited):
        port.set_stage("deal:case_7", "call_booked")


# ---------------------------------------------------------------- device

def _mobilerun(tmp: Path, faults: FaultPlan):
    from app.gateway.adapters.mobilerun import MobilerunDevice
    from tests.adapters.fakes import FakeMobilerun

    return MobilerunDevice("mr_test", transport=FakeMobilerun(faults).transport(),
                           sleep=lambda s: None)


DEVICE = {"local": lambda tmp, faults: local.LocalDevice(tmp, faults), "mobilerun": _mobilerun}
MEMO = "Hi Team #7,\nEducational estimate\n\nReference: UTX-7F3K9Q2M"  # v1.7 last line


@pytest.mark.parametrize("name", _ids(DEVICE))
def test_device_capture(name, tmp_path):
    faults = FaultPlan()
    port = DEVICE[name](tmp_path, faults)
    check = port.open_and_capture("local-msg-1", MEMO)
    assert isinstance(check.screenshot_url, str) and isinstance(check.rendered_ok, bool)
    faults.add(Unavailable())
    with pytest.raises(Unavailable):
        port.open_and_capture("local-msg-1", MEMO)


# ---------------------------------------------------------------- scrape

def _apify(tmp: Path, faults: FaultPlan):
    from app.gateway.adapters.apify import ApifyScrape
    from tests.adapters.fakes import FakeApify

    return ApifyScrape("apify_test", transport=FakeApify(faults).transport())


SCRAPE = {"local": lambda tmp, faults: local.LocalScrape(FIXTURES, faults), "apify": _apify}


@pytest.mark.parametrize("name", _ids(SCRAPE))
def test_scrape_fee_page_and_search(name, tmp_path):
    from app.gateway.perform import parse_fee_cents

    faults = FaultPlan()
    port = SCRAPE[name](tmp_path, faults)
    page = port.fetch_page(local.DELAWARE_FEE_URL)
    assert page.source in ("snapshot", "apify")
    assert parse_fee_cents(page.html) == 5000 or page.source == "apify"
    hits = port.search("delaware franchise tax assumed par value method")
    assert hits and all(h.cite and h.snippet for h in hits)
    with pytest.raises(PortError):
        port.fetch_page("https://example.invalid/nothing")
    faults.add(Unavailable())
    with pytest.raises(Unavailable):
        port.search("anything")


# ---------------------------------------------------------------- toolbuy

TOOLBUY = {"local": lambda tmp, faults: local.LocalToolBuy(faults)}


@pytest.mark.parametrize("name", _ids(TOOLBUY))
def test_toolbuy_reports_decimal_cost(name, tmp_path):
    port = TOOLBUY[name](tmp_path, FaultPlan())
    purchase = port.buy("enrichment", {"domain": "team7.dev"}, key="buy-1")
    assert Decimal(purchase.cost_usd) >= 0
    assert isinstance(purchase.bought, bool)


# ---------------------------------------------------------------- voice / pay (no key)


def test_voice_without_key_is_not_configured():
    port = local.NoVoice()
    with pytest.raises(NotConfigured):
        port.create_session()
    with pytest.raises(NotConfigured):
        port.speak("brief")


def test_pay_has_no_adapter_yet():
    with pytest.raises(NotConfigured):
        local.NoPay().create_link(100, "gig", key="p1")


# ---------------------------------------------------------------- calendar

def _agentmail_calendar(tmp: Path, faults: FaultPlan):
    from app.gateway.adapters.agentmail import AgentMailCalendar
    from tests.adapters.fakes import FakeAgentMail

    return AgentMailCalendar("am_test", inbox="checkup@u-tax.test", data_dir=tmp,
                             transport=FakeAgentMail(faults).transport())


CALENDAR = {"local": lambda tmp, faults: local.LocalCalendar(tmp, faults),
            "agentmail": _agentmail_calendar}


@pytest.mark.parametrize("name", _ids(CALENDAR))
def test_calendar_book_is_idempotent(name, tmp_path):
    faults = FaultPlan()
    port = CALENDAR[name](tmp_path, faults)
    slot = Slot(start="2026-11-08T17:30:00.000Z", minutes=15)
    a = port.book(slot, "founder@team7.dev", "U-Tax review", key="call:case_7:v1")
    b = port.book(slot, "founder@team7.dev", "U-Tax review", key="call:case_7:v1")
    assert a == b
    faults.add(Unavailable())
    with pytest.raises(Unavailable):
        port.book(slot, "founder@team7.dev", "U-Tax review", key="call:case_8:v1")


# ---------------------------------------------------------------- social

def _naive_social(tmp: Path, faults: FaultPlan):
    from app.gateway.adapters.naive_social import NaiveSocial
    from tests.adapters.fakes import FakeNaive

    fake = FakeNaive(faults)
    return NaiveSocial("sk_test", identity=fake.identity, data_dir=tmp,
                       transport=fake.transport())


SOCIAL = {"local": lambda tmp, faults: local.LocalSocial(tmp, faults), "naive": _naive_social}


@pytest.mark.parametrize("name", _ids(SOCIAL))
def test_social_post_is_idempotent(name, tmp_path):
    faults = FaultPlan()
    port = SOCIAL[name](tmp_path, faults)
    a = port.post(Post(channel="x", text="hello"), key="post-1")
    b = port.post(Post(channel="x", text="hello"), key="post-1")
    assert a == b and a.url
    faults.add(RateLimited())
    with pytest.raises(RateLimited):
        port.post(Post(channel="x", text="second"), key="post-2")
