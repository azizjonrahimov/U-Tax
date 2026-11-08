"""Task 26: HR_BOOKING_URL (v1.11.3); list_open_cases without `next`; no X-UTax-Actor."""

from __future__ import annotations

import json

import pytest

from .test_mcp import TOKENS, call, mcp, tool  # noqa: F401  (the mcp fixture)

LIST = "Jo Doe <jo@example.com>"
BOOKING = "https://calendar.app.google/AbCdEf123"


def outreach_mail(make_harness, **settings):
    h = make_harness(public_url="https://gw.u-tax.test", hr_candidates=LIST, **settings)
    h.ok("set_trust", action_type="email", state="AUTO")
    h.ok("set_trust", action_type="calendar_invite", state="AUTO")
    slot = h.gw.offered_slots(None)[0]
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": slot}, actor="hr")
    assert r.status == "done", r.error
    note = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))["body"]
    assert "pick a time on my calendar" not in note  # task 32: never a link in email 1
    h.ok("inject_email", **{"from": "jo@example.com", "inbox": "hiring",
                            "subject": "Re: gig", "body": "Yes, happy to."})
    return json.loads(h.outbox()[-1].read_text(encoding="utf-8"))["body"]  # email 2


def test_the_booking_link_is_offered_when_set(make_harness):
    body = outreach_mail(make_harness, hr_booking_url=BOOKING)
    assert f"You can also pick a time on my calendar: {BOOKING}" in body


@pytest.mark.parametrize("url", ["", "http://calendar.app.google/x", "javascript:alert(1)",
                                 "https://user@calendar.app.google/x",
                                 "https://calendar.app.google/x\nBcc: someone"])
def test_no_booking_line_without_a_clean_https_link(make_harness, url):
    body = outreach_mail(make_harness, hr_booking_url=url)
    assert "pick a time on my calendar" not in body
    assert "{{" not in body and "\n\n\n" not in body


def test_list_open_cases_names_no_next_step(h):
    h.new_case()
    [row] = h.ok("list_open_cases")["cases"]
    assert "next" not in row and row["status"] and row["waiting_reason"]


def test_a_seat_works_without_the_actor_header(mcp):  # noqa: F811
    # live: Naive redacts configured header values from tool output, so the agents lane drops
    # X-UTax-Actor; the seat comes from the URL and that seat's own token
    h, client = mcp
    assert call(client, "cos", "ping").status_code == 200
    rows = tool(client, "cos", "list_open_cases")
    assert rows["status"] == "done" and "cases" in rows["data"]
    assert call(client, "cos", "ping", token=TOKENS["tax"]).status_code == 401
