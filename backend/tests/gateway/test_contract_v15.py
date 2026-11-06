"""Contract v1.5 (task 6b): post links, Applicant answered, templates, /talk turns,
throttles, voice threading, decision mails, the CEO's hiring list."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.gateway.settings import REPO_ROOT
from app.gateway.templates import SPECS, AI_SCREEN_LINE, DISCLAIMER, Templates
from app.main import create_app

from .conftest import HAPPY_QUOTES


def auto_email(h):
    h.ok("ceo_stop", action_type="email")
    h.gw.state.trust["email"] = "AUTO"


# ---------------------------------------------------------------- 1. posts


def test_post_link_is_appended_after_the_guard(h):
    h.ok("set_trust", action_type="social_post", state="AUTO")
    h.ok("post_social", channel="x", text="Delaware C-corp? Free checkup in minutes.")
    assert h.events("post.published")[-1].payload["text"].endswith(" https://u-tax.test/talk")
    h.ok("post_social", channel="linkedin", kind="hiring", text="Hiring an EA for 1 hour.")
    assert h.events("post.published")[-1].payload["text"].endswith(" jobs@u-tax.test")
    # an email typed by the agent is still refused: only the appended one gets through
    assert h.do("post_social", channel="x", text="Write a@b.co").error["code"] == "content_guard"


def test_posts_need_a_talk_url_and_fit_255(make_harness):
    h = make_harness(public_talk_url="")
    assert h.do("post_social", channel="x", text="hi").error["code"] == "no_talk_url"
    h2 = make_harness()
    assert h2.do("post_social", channel="x", text="x" * 256).error["code"] == "too_long"


# ---------------------------------------------------------------- 2. applicant answered


def test_first_questionnaire_reply_wakes_hr_once(h):
    auto_email(h)
    wakes = []
    h.gw.on_wake(wakes.append)
    mail = {"from": "ann@example.com", "subject": "gig", "inbox": "hiring"}
    h.ok("inject_email", **mail, body="EA, 6 years")
    h.ok("inject_email", **{**mail, "subject": "Re: gig"}, body="1. EA (IRS) 4. $150")
    h.ok("inject_email", **{**mail, "subject": "Re: gig 2"}, body="also free Sunday")
    assert wakes == ["New applicant cand_1", "Applicant answered cand_1"]


# ---------------------------------------------------------------- 3. templates


SAMPLE_VALUES = {
    "masked_name": "Team #7", "missing_list": "authorized shares", "problems_line": "",
    "authorized_method": "$85,165", "assumed_par_method": "$400", "annual_report_fee": "$50",
    "total": "$450", "due_date": "March 1, 2027", "form5472_note": "", "rnd_note": "",
    "headline": "h", "lead": "l", "summary": "s", "action_line": "a", "console_url": "u",
    "period": "p", "clients_new": "1", "memos_sent": "1", "calls_booked": "1",
    "tax_saved": "$1", "agent_spend": "$1", "touches_training": "1", "touches_operating": "1",
    "touches_engineering": "0", "pending_approvals": "-", "blocked_lines": "-",
    "hiring_line": "-", "flags_lines": "-", "greeting": "Hi", "approvals_line": "x",
    "blocked_line": "", "hiring_line_": "",
}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_every_agents_template_loads_without_fallback(name, caplog):
    """The agents lane's naive/templates/*.md must pass the strict renderer as written."""
    path = REPO_ROOT / "naive" / "templates" / f"{name}.md"
    if not path.exists():
        pytest.skip(f"{name}.md not on this branch")
    templates = Templates(REPO_ROOT / "naive" / "templates")
    with caplog.at_level(logging.WARNING, logger="utax.templates"):
        text = templates.render(name, SAMPLE_VALUES)
    assert not caplog.records, caplog.records[0].getMessage() if caplog.records else ""
    assert "{{" not in text
    if name.startswith(("memo", "ask-client", "not-eligible")):
        assert DISCLAIMER in text
    if name == "hr-questionnaire":
        assert AI_SCREEN_LINE in text


def test_a_broken_template_falls_back_to_built_in(tmp_path, caplog):
    (tmp_path / "ask-client.md").write_text("Hi {{masked_name}} {{secret}}", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="utax.templates"):
        text = Templates(tmp_path).render("ask-client", SAMPLE_VALUES)
    assert "unknown placeholder secret" in caplog.text
    assert DISCLAIMER in text and "{{" not in text


# ---------------------------------------------------------------- 4. /talk


def test_only_customer_turns_become_case_text(h):
    turns = [{"speaker": "agent", "text": "So that's 99,999,999 authorized shares, right?"},
             {"speaker": "customer", "text": "We're a Delaware C-corp, 10,000,000 authorized"},
             {"speaker": "customer", "text": "Mail me at jane at example dot com"}]
    data = h.ok("voice", turns=turns, channel="voice", speaker="customer",
                reply_email="jane@example.com")
    texts = h.gw.state.cases[data["case_id"]].texts
    assert "99,999,999" not in texts[0] and "10,000,000" in texts[0]
    assert "example dot com" not in texts[0] and "[email hidden]" in texts[0]
    # the AI's read-back can't be quoted as a fact
    out = h.ok("extract_facts", case_id=data["case_id"],
               quotes={"authorized_shares": "99,999,999"}, incorporated_delaware=True,
               foreign_owner_25=False)
    assert "authorized_shares" in out["missing"]


def test_voice_case_threads_replies_from_its_unowned_address(h):
    data = h.ok("voice", text="hi", speaker="customer", reply_email="judge@yc.com")
    again = h.new_case(body="Here are my numbers", sender="judge@yc.com")
    assert again == data["case_id"]
    # an address that already has a case never gets captured by a voice case
    owner = h.new_case(sender="owner@team9.dev")
    voice = h.ok("voice", text="hi", speaker="customer", reply_email="owner@team9.dev")
    assert voice["case_id"] != owner
    assert h.new_case(body="more", sender="owner@team9.dev") == owner


# ---------------------------------------------------------------- 5. throttles


def test_flag_mails_are_throttled_to_one_an_hour(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    a, b = h.new_case(), h.new_case(sender="b@two.dev")
    h.ok("flag_case", case_id=a, kind="possible_injection")
    h.ok("flag_case", case_id=b, kind="possible_injection")
    flagged = [e for e in h.events("ceo.notified") if e.payload["reason"] == "flagged"]
    assert len(flagged) == 1  # the second goes into the digest
    h.ok("ceo_digest")
    digest = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()][-1]
    assert "2 flag(s)" in digest["body"]


def test_at_most_three_memos_a_day_per_address(h):
    auto_email(h)
    for _ in range(3):
        case_id = h.to_qa_passed(h.new_case(sender="founder@team7.dev"))
        h.ok("send_memo", case_id=case_id)
        h.ok("unblock", case_id=case_id)  # reopen for another round
        h.new_case(body="again", sender="founder@team7.dev")
    case_id = h.to_qa_passed("case_1")
    assert h.do("send_memo", case_id=case_id).error["code"] == "memo_cap"
    h.clock.advance(days=1, minutes=1)
    assert h.ok("send_memo", case_id=case_id)["message_id"]


# ---------------------------------------------------------------- 7. decisions, hiring list


def test_decision_mail_follows_email_trust(h):
    h.ok("inject_email", **{"from": "ann@example.com", "subject": "gig", "body": "EA 6 years",
                            "inbox": "hiring"})
    h.ok("hr_record", stage="screened", candidate_id="cand_1", note="")
    data = h.ok("candidate_decision", candidate_id="cand_1", decision="hire")
    assert data["mail"] == "needs_approval"  # email is still ASK
    approval = [a for a in h.gw.state.approvals.values()
                if a.action_key == "decision:cand_1"][0]
    assert h.ok("approve", approval_id=approval.approval_id, decision="allow")["mail"] == "sent"
    mails = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    decision = [m for m in mails if m["subject"] == "U-Tax: about the memo review gig"]
    assert len(decision) == 1 and decision[0]["to"] == "ann@example.com"
    assert "book you" in decision[0]["body"]
    # the questionnaire that was waiting on the same email approval went out too
    assert any(m["subject"].startswith("U-Tax: a few questions") for m in mails)


def test_screened_candidates_are_listed_for_the_ceo(h):
    h.ok("inject_email", **{"from": "a@x.io", "subject": "g", "body": "EA", "inbox": "hiring"})
    h.ok("inject_email", **{"from": "b@y.io", "subject": "g", "body": "-", "inbox": "hiring"})
    h.ok("hr_record", stage="screened", candidate_id="cand_2", note="")
    listed = h.gw.public_state()["hiring"]["candidates"]
    assert [c["candidate_id"] for c in listed] == ["cand_2"]  # screened, even if not shortlisted
    assert "a@x.io" not in json.dumps(listed)


# ---------------------------------------------------------------- voice session, event mode


def test_voice_session_is_minted_with_the_intake_prompt(make_harness, tmp_path):
    prompt = tmp_path / "voice-intake.md"
    prompt.write_text("You are U-Tax's voice intake agent.", encoding="utf-8")
    h = make_harness(voice_prompt_path=prompt)
    seen = {}

    class FakeVoice:
        provider = "openai"

        def create_session(self, instructions):
            from app.gateway.ports import VoiceSession

            seen["instructions"] = instructions
            return VoiceSession(client_secret="ek", model="m", expires_at="x")

        def speak(self, text):  # pragma: no cover - unused
            return b""

    h.ports.voice = FakeVoice()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").json()["client_secret"] == "ek"
    assert seen["instructions"].startswith("You are U-Tax's voice intake agent.")


def test_event_mode_allows_60_per_minute(make_harness):
    h = make_harness(public_mode="event")
    with TestClient(create_app(h.gw, start_background=False)) as client:
        codes = [client.post("/api/voice/session").status_code for _ in range(45)]
    assert 429 not in codes
