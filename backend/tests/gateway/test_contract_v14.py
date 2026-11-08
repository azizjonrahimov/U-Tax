"""Contract v1.4 - v1.4.2: flag_case, get_candidate, standing orders, CEO decisions,
post guard (@handles, URLs), CEO mail reasons, questionnaire event, wake retries."""

import json

import pytest

from app.gateway.perform import post_problem


def test_flag_case_writes_case_flagged_and_mails_the_ceo(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    case_id = h.new_case()
    h.ok("flag_case", case_id=case_id, kind="possible_injection")
    assert h.events("case.flagged")[0].payload == {"kind": "possible_injection"}
    assert h.events("case.flagged")[0].actor == "qa"
    assert h.events("ceo.notified")[-1].payload["reason"] == "flagged"
    assert h.do("flag_case", case_id=case_id, kind="spam").error["code"] == "bad_input"
    assert h.gw.perform("flag_case", {"case_id": case_id, "kind": "unrealistic_inputs"},
                        actor="intake").error["code"] == "tool_not_allowed"


def test_get_candidate_is_masked(h):
    h.ok("inject_email", **{"from": "ann.lee@example.com", "subject": "gig", "inbox": "hiring",
                            "body": "Ann Lee here, EA with 6 years. Call (415) 555-0100."})
    h.ok("inject_email", **{"from": "ann.lee@example.com", "subject": "Re: gig",
                            "inbox": "hiring", "body": "Rate $150. Ann"})
    data = h.gw.perform("get_candidate", {"candidate_id": "cand_1"}, actor="hr").data
    blob = json.dumps(data)
    for leak in ("ann.lee@example.com", "555-0100", "Ann", "Lee"):
        assert leak not in blob
    assert data["credentials"] == "EA · 6 yrs" and data["stage"] == "applicant"
    assert len(data["answers"]) == 2 and "$150" in data["answers"][1]


def test_shortlist_mails_the_ceo_and_the_ceo_decides(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "CPA", "inbox": "hiring"})
    h.ok("hr_record", stage="shortlisted", candidate_id="cand_1", note="strong")
    assert h.events("ceo.notified")[-1].payload["reason"] == "shortlisted"
    h.ok("candidate_decision", candidate_id="cand_1", decision="decline")
    declined = h.events("hr.declined")[0]
    assert declined.actor == "ceo" and h.gw.state.candidates["cand_1"]["stage"] == "declined"
    assert h.do("candidate_decision", candidate_id="cand_404",
                decision="hire").error["code"] == "not_found"


def test_standing_order_pauses_posting(h):
    first = h.do("post_social", channel="x", text="Free Delaware checkup.")
    h.approve_pending(first)
    h.ok("set_trust", action_type="social_post", state="PAUSED")
    refused = h.do("post_social", channel="x", text="Another post")
    assert refused.error["code"] == "paused"
    assert h.events("trust.changed")[-1].payload["reason"] == "CEO order"
    h.ok("set_trust", action_type="social_post", state="AUTO")
    assert h.ok("post_social", channel="x", text="Another post")["url"]
    assert h.ok("set_trust", action_type="social_post", state="AUTO")["duplicate"] is True


@pytest.mark.parametrize("text, allowed, ok", [
    ("Talk to us: https://u-tax.app/talk", "https://u-tax.app/talk", True),
    ("Talk to us: https://u-tax.app/talk.", "https://u-tax.app/talk", True),
    ("See https://evil.example/x", "https://u-tax.app/talk", False),
    ("Visit team7.dev now", "", False),
    ("Thanks @founder for the tip", "", False),
    ("Free Delaware checkup in minutes", "", True),
])
def test_post_guard_handles_and_urls(text, allowed, ok):
    assert (post_problem(text, allowed_url=allowed) is None) is ok


def test_questionnaire_is_an_event(h):
    h.ok("ceo_stop", action_type="email")
    h.gw.state.trust["email"] = "AUTO"
    data = h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA",
                                   "inbox": "hiring"})
    assert data["questionnaire"] == "sent"
    sent = h.events("hr.questionnaire_sent")[0]
    assert sent.actor == "system" and sent.payload["candidate_id"] == "cand_1"


def test_failed_wakes_are_retried_with_backoff(h, monkeypatch):
    import app.gateway.perform as perform_mod

    clock = [1000.0]
    monkeypatch.setattr(perform_mod.time, "monotonic", lambda: clock[0])
    calls = []

    def flaky(message):
        calls.append(message)
        if len(calls) < 3:
            raise ConnectionError("naive down")

    h.gw.on_wake(flaky)
    case_id = h.new_case()
    assert h.gw.pending_wake_retries() == [f"FAST PATH {case_id}"]  # D42 M2: every email case runs the fast path
    assert h.gw.retry_wakes() == 0  # backoff not over
    clock[0] += 5
    assert h.gw.retry_wakes() == 1  # second try fails, backs off 15 s
    clock[0] += 15
    h.gw.retry_wakes()
    assert calls == [f"FAST PATH {case_id}"] * 3
    assert h.gw.pending_wake_retries() == []


def test_repeated_ceo_brief_does_not_spam_the_log(h):
    h.new_case()
    first = h.ok("ceo_brief")
    count = len(list(h.store.since(0)))
    again = h.ok("ceo_brief")
    assert again["brief_event_id"] == first["brief_event_id"]
    assert len(list(h.store.since(0))) == count  # nothing new written
    h.new_case(sender="b@two.dev")
    # within a minute the cached brief is reused (pending approvals are always live) ...
    assert h.ok("ceo_brief")["brief_event_id"] == first["brief_event_id"]
    h.clock.advance(seconds=61)  # ... after it, new activity gets a new brief
    assert h.ok("ceo_brief")["brief_event_id"] != first["brief_event_id"]
