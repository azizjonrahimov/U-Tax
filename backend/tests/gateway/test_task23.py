"""Task 23 (contract v1.11 / v1.11.1 / v1.11.2, design D31): the live CEO orders."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.gateway.adapters.monid import MonidToolBuy
from app.main import create_app

from ..adapters.test_monid import completed

PUBLIC = "https://gw.u-tax.test"
LIST = "Jane Roe <jane.roe@example.com>, not an address, Bob Lee <bob@reviewers.dev>"


@pytest.fixture
def h(make_harness):
    return make_harness(public_url=PUBLIC, hr_candidates=LIST)


def auto_email(h):
    h.ok("set_trust", action_type="email", state="AUTO")
    h.ok("set_trust", action_type="calendar_invite", state="AUTO")  # 25b: outreach invites


# ---------------------------------------------------------------- make_ad


def test_make_ad_renders_the_png_on_the_gateways_own_host(h):
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay $450, not $85,165"},
                     actor="mkt")
    assert r.status == "done" and r.data["image_url"] == f"{PUBLIC}/assets/ad_1.png"
    [event] = h.events("ad.created")
    assert event.payload == {"kind": "client", "headline": "Pay $450, not $85,165",
                             "image_url": f"{PUBLIC}/assets/ad_1.png"}
    with Image.open(h.gw.settings.data_dir / "assets" / "ad_1.png") as image:
        assert image.size == (1200, 628)
    again = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay $450, not $85,165"},
                         actor="mkt")
    assert again.data["duplicate"] is True and len(h.events("ad.created")) == 1
    # post_social takes the gateway's own host as an image host
    post = h.gw.perform("post_social", {"channel": "x", "text": "Free Delaware checkup.",
                                        "image_url": r.data["image_url"]}, actor="mkt")
    assert post.status in ("done", "needs_approval")


def test_the_ad_png_is_served_publicly_and_nothing_else(h):
    url = h.gw.perform("make_ad", {"kind": "hiring", "headline": "Review AI tax memos"},
                       actor="mkt").data["image_url"]
    with TestClient(create_app(h.gw, start_background=False)) as c:
        r = c.get(url.removeprefix(PUBLIC))
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        for bad in ("/assets/ad_9.png", "/assets/..%2Fevents.jsonl", "/assets/ad_1.PNG"):
            assert c.get(bad).status_code == 404, bad


@pytest.mark.parametrize("headline", ["", "x" * 81, "Write to jane@example.com",
                                      "Call 415-555-0100 now", "Team #3 saved big"])
def test_a_headline_passes_the_post_guard(h, headline):
    r = h.gw.perform("make_ad", {"kind": "client", "headline": headline}, actor="mkt")
    assert r.status == "refused"


def test_make_ad_needs_the_gateways_public_url_and_is_marketings(make_harness):
    h = make_harness()
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Hello"}, actor="mkt")
    assert r.error["code"] == "no_public_url"
    assert h.gw.perform("make_ad", {"kind": "client", "headline": "Hi"},
                        actor="hr").error["code"] == "tool_not_allowed"


# ---------------------------------------------------------------- sourcing


def test_source_candidates_with_monid_off_returns_nothing_and_says_so(h):
    r = h.gw.perform("source_candidates", {"count": 2}, actor="hr")
    assert r.data["leads"] == [] and r.data["source"] == "none"
    assert h.events("hr.sourced")[-1].payload == {"count": 0, "source": "none"}
    assert h.gw.perform("source_candidates", {"count": 9}, actor="hr").status == "refused"


def test_source_candidates_on_monid_returns_masked_public_leads(h):
    output = {"results": [
        {"title": "Jane Roe, CPA - Delaware tax", "url": "https://example.com/in/jane"},
        {"title": "Write me: bob@example.com", "url": "https://example.com/in/bob"},
        {"title": "No URL", "url": "ftp://x"},
        {"title": "EA, startup tax", "url": "https://example.com/in/ea"}]}

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=completed(output=output))

    # the live MONID_TOOLS entry (task 24): ploid people search, {filters, limit} only
    tools = {"candidate_search": {"provider": "ploid", "endpoint": "/linkedin/search",
                                  "max_usd": "0.05", "fields": ["filters", "limit"]}}
    h.gw.ports.toolbuy = MonidToolBuy("mk_test", tools=tools, daily_cap_usd="1.00",
                                      transport=httpx.MockTransport(handler),
                                      today=lambda: "2026-11-07")
    r = h.gw.perform("source_candidates", {"count": 5}, actor="hr")
    assert r.status == "done" and r.data["source"] == "monid"
    leads = r.data["leads"]
    assert [lead["label"] for lead in leads] == ["Lead A", "Lead B", "Lead C"]
    assert "bob@example.com" not in json.dumps(leads)
    assert h.events("hr.sourced")[-1].payload == {"count": 3, "source": "monid"}
    assert [e.provider for e in h.events("cost.booked")] == ["monid"]  # bought, booked
    [run] = sent
    assert run["provider"] == "ploid" and run["endpoint"] == "/linkedin/search"
    assert run["input"]["body"] == {"filters": {"keywords": "Delaware franchise tax",
                                                "title": ["CPA", "Enrolled Agent",
                                                          "Tax Accountant"]}, "limit": 5}


def test_a_people_search_nested_under_data_is_read_without_names(h):
    output = {"data": {"people": [
        {"name": "Jane Roe", "headline": "CPA at Roe & Co", "profileUrl": "https://example.com/in/jr"},
        {"name": "Ann Lee", "position": "Enrolled Agent", "linkedinUrl": "https://example.com/in/al"}],
        "cursor": "abc"}}
    leads = h.gw._public_leads(output, 5)
    assert [(lead["headline"], lead["public_url"]) for lead in leads] == [
        ("CPA at Roe & Co", "https://example.com/in/jr"),
        ("Enrolled Agent", "https://example.com/in/al")]
    assert "Jane" not in json.dumps(leads) and "Ann" not in json.dumps(leads)


# ---------------------------------------------------------------- the CEO's list, outreach


def test_the_ceos_list_becomes_candidates_without_names_or_questionnaires(h):
    rows = h.gw.perform("list_candidates", {}, actor="hr").data["candidates"]
    assert [(r["candidate_id"], r["label"], r["has_address"]) for r in rows] == [
        ("cand_1", "Candidate A", True), ("cand_2", "Candidate B", True)]
    assert all(r["offered_slots"] for r in rows)
    blob = json.dumps(rows) + json.dumps([e.payload for e in h.events("hr.applicant")])
    for leak in ("jane", "Roe", "bob@", "reviewers.dev"):
        assert leak not in blob, leak
    assert h.gw.retry_questionnaires() == [] and h.outbox() == []
    h.restart()
    assert len(h.events("hr.applicant")) == 2  # seeded once


def test_hr_outreach_emails_a_consented_candidate_and_books_the_call(h):
    auto_email(h)
    slot = h.gw.perform("get_candidate", {"candidate_id": "cand_1"},
                        actor="hr").data["offered_slots"][0]
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": slot}, actor="hr")
    assert r.status == "done", r.error
    [mail] = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    assert mail["to"] == "jane.roe@example.com" and "calendar invite" in mail["body"]
    assert h.events("hr.outreach")[-1].payload == {"candidate_id": "cand_1",
                                                   "label": "Candidate A", "slot": slot}
    call = h.events("call.booked")[-1]
    assert call.case_id is None and call.payload["kind"] == "candidate"
    assert call.payload["invitee_masked"] == "Candidate A"
    assert slot not in h.gw.offered_slots(None)  # held for everyone now
    assert h.gw.state.public()["hiring"]["contacted"] == 1
    again = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": slot}, actor="hr")
    assert again.data["duplicate"] is True and len(h.outbox()) == 1


def test_hr_outreach_refuses_a_lead_and_an_unoffered_slot(h):
    auto_email(h)
    lead = h.gw.perform("hr_outreach", {"candidate_id": "lead_1",
                                        "slot": "2026-11-09T18:00:00Z"}, actor="hr")
    assert lead.error["code"] == "no_consented_address"
    late = h.gw.perform("hr_outreach", {"candidate_id": "cand_2",
                                        "slot": "2030-01-01T18:00:00Z"}, actor="hr")
    assert late.error["code"] == "slot_not_offered"
    assert h.outbox() == []


def test_hr_outreach_waits_for_the_ceo_on_ask(h):
    slot = h.gw.offered_slots(None)[0]
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_2", "slot": slot}, actor="hr")
    assert r.status == "needs_approval" and h.outbox() == []


# ---------------------------------------------------------------- follow_up


def test_follow_up_writes_in_the_clients_thread_once_a_day(h):
    auto_email(h)
    case_id = h.to_sent()
    r = h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"}, actor="intake")
    assert r.status == "done", r.error
    sent = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert sent["subject"].startswith("Re:") and "memo" in sent["body"]
    assert h.events("client.followed_up")[-1].payload == {"kind": "memo_check"}
    assert h.events("client.followed_up")[-1].case_id == case_id
    second = h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"},
                          actor="intake")
    assert second.data.get("duplicate") is True
    other = h.gw.perform("follow_up", {"case_id": case_id, "kind": "call_reminder"},
                         actor="intake")
    assert other.status == "refused"
    h.clock.advance(days=1)
    assert h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"},
                        actor="intake").status == "done"


def test_follow_up_fits_the_case(h):
    auto_email(h)
    case_id = h.new_case()
    r = h.gw.perform("follow_up", {"case_id": case_id, "kind": "call_reminder"},
                     actor="intake")
    assert r.status == "refused" and r.error["code"] == "wrong_status"


# ---------------------------------------------------------------- order linking


def test_a_ceo_order_is_linked_through_its_steps(h):
    h.ok("voice", text="advertise the firm", speaker="ceo", channel="typed")
    order = h.events("voice.command")[-1]
    oid = f"order_{order.id}"
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay less Delaware tax",
                                 "order_id": oid}, actor="mkt")
    assert h.events("ad.created")[-1].payload["order_id"] == oid
    assert r.status == "done"


@pytest.mark.parametrize("bad", ["order_999", "order_x", "ORDER_1", 7, "order_1 "])
def test_an_order_id_that_names_no_ceo_order_is_ignored(h, bad):
    # event 1 is the seeded hr.applicant: "order_1" names an event that is no CEO order
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay less Delaware tax",
                                 "order_id": bad}, actor="mkt")
    assert r.status == "done" and "order_id" not in h.events("ad.created")[-1].payload


def test_the_order_wake_carries_the_order_id(h):
    woke = []
    h.gw.on_wake(woke.append)
    h.ok("voice", text="hire 2 people", speaker="ceo", channel="typed")
    order = h.events("voice.command")[-1]
    assert woke == [f"CEO order_{order.id}: hire 2 people"]


def test_every_tool_offers_order_id_and_the_new_tools_have_schemas():
    from app.gateway.perform import TOOL_ACTORS
    from app.mcp import TOOLS

    assert set(TOOLS) == set(TOOL_ACTORS)
    assert all("order_id" in spec["inputSchema"]["properties"] for spec in TOOLS.values())


# ---------------------------------------------------------------- review 1


def test_a_failed_outreach_retry_keeps_its_slot_and_mails_once(h, monkeypatch):
    from app.gateway.ports import Unavailable

    auto_email(h)
    first, second = h.gw.offered_slots(None)[:2]
    real = h.gw.ports.calendar.book

    def down(*a, **k):
        raise Unavailable("calendar down")

    monkeypatch.setattr(h.gw.ports.calendar, "book", down)
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": first}, actor="hr")
    assert r.status == "error" and h.outbox() == []  # the invite failed first: no mail
    assert first not in h.gw.offered_slots(None)  # held while it's under way
    other = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": second},
                         actor="hr")
    assert other.error["code"] == "slot_bound" and other.data["slot"] == first
    monkeypatch.setattr(h.gw.ports.calendar, "book", real)
    again = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": first}, actor="hr")
    assert again.status == "done" and len(h.outbox()) == 1  # one mail, for that slot


def test_an_approval_for_one_slot_never_sends_another(h):
    first, second = h.gw.offered_slots(None)[:2]
    asked = h.gw.perform("hr_outreach", {"candidate_id": "cand_2", "slot": first}, actor="hr")
    assert asked.status == "needs_approval"
    h.ok("approve", approval_id=asked.approval_id, decision="allow")
    h.ok("set_trust", action_type="email", state="ASK")  # the approve flipped it to AUTO
    other = h.gw.perform("hr_outreach", {"candidate_id": "cand_2", "slot": second},
                         actor="hr")
    assert other.status == "needs_approval" and h.outbox() == []


def test_a_repeat_ad_is_served_on_todays_host(h, make_harness):
    url = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay less Delaware tax"},
                       actor="mkt").data["image_url"]
    (h.gw.settings.data_dir / "assets" / "ad_1.png").unlink()  # say the disk was wiped
    from dataclasses import replace

    h.settings = replace(h.settings, public_url="https://new-tunnel.trycloudflare.com")
    gw = h.restart()
    again = gw.perform("make_ad", {"kind": "client", "headline": "Pay less Delaware tax"},
                       actor="mkt").data
    assert url.startswith(PUBLIC)
    assert again["image_url"] == "https://new-tunnel.trycloudflare.com/assets/ad_1.png"
    assert (gw.settings.data_dir / "assets" / "ad_1.png").is_file()


# ---------------------------------------------------------------- review 2


def test_a_case_worked_for_a_ceo_order_still_passes_qa(h):
    # the order id groups steps on the theater; it is never part of the tax result QA checks
    from .conftest import HAPPY_QUOTES

    h.ok("voice", text="process the new case", speaker="ceo", channel="typed")
    oid = f"order_{h.events('voice.command')[-1].id}"
    case_id = h.new_case()
    for name, args, actor in (
            ("extract_facts", {"quotes": HAPPY_QUOTES, "incorporated_delaware": True,
                               "foreign_owner_25": False}, "intake"),
            ("compute_tax", {}, "tax")):
        r = h.gw.perform(name, {"case_id": case_id, "order_id": oid, **args}, actor=actor)
        assert r.status == "done", (name, r.error)
    assert h.events("tax.computed")[-1].payload["order_id"] == oid
    assert "order_id" not in h.gw.state.cases[case_id].result
    qa = h.gw.perform("qa_check", {"case_id": case_id, "order_id": oid}, actor="qa")
    assert qa.status == "done" and qa.data["passed"], qa.data


def test_an_order_id_is_normalized_and_a_bad_lookup_is_ignored(h, monkeypatch):
    h.ok("voice", text="advertise", speaker="ceo", channel="typed")
    eid = h.events("voice.command")[-1].id
    assert h.gw._valid_order(f"order_0{eid}") == f"order_{eid}"

    def broken(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(h.gw.store, "since", broken)
    assert h.gw._valid_order(f"order_{eid}") is None


def test_the_own_host_is_an_image_host_only_for_ad_pngs(h):
    assert h.gw._asset_url_ok(f"{PUBLIC}/assets/ad_3.png")
    for bad in (f"{PUBLIC}/api/state?token=x", f"{PUBLIC}/assets/ad_3.png?x=1",
                f"{PUBLIC}/hooks/agentmail", "http://gw.u-tax.test/assets/ad_3.png"):
        assert not h.gw._asset_url_ok(bad), bad


def test_a_follow_up_approval_names_its_kind(h):
    case_id = h.to_sent()
    h.ok("set_trust", action_type="email", state="ASK")  # the memo approval made it AUTO
    asked = h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"},
                         actor="intake")
    assert asked.status == "needs_approval"
    assert "memo check" in h.gw.state.approvals[asked.approval_id].summary


def test_a_lead_never_carries_an_object_repr_or_digs_forever(h):
    rows = [{"position": {"title": "CPA", "name": "Jane Roe"}, "url": "https://example.com/a"},
            {"headline": "EA, startup tax", "url": {"href": "https://example.com/b"}},
            {"headline": "CPA, Delaware", "url": "https://example.com/c"}]
    leads = h.gw._public_leads({"results": [], "people": rows}, 5)
    assert [lead["headline"] for lead in leads] == ["CPA, Delaware"]
    deep: dict = {"data": {}}
    node = deep["data"]
    for _ in range(50):
        node["data"] = {}
        node = node["data"]
    assert h.gw._public_leads(deep, 5) == []
