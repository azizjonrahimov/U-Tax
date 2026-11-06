import json
import re

import pytest

from app.gateway.events import PAYLOAD_KEYS, NewEvent, validate
from app.gateway.masking import company_label, mask_email, mask_text, redact_ids
from app.gateway.settings import REPO_ROOT

SAMPLE = REPO_ROOT / "contracts" / "events.sample.jsonl"


def sample_events():
    return [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_every_sample_event_matches_the_catalog():
    for e in sample_events():
        validate(e["type"], e["actor"], e["provider"], e["status"], e["payload"])
        assert set(e) == {"id", "ts", "type", "actor", "case_id", "provider", "status",
                          "payload", "tokens_in", "tokens_out", "cost_usd"}


def test_catalog_rejects_extra_or_missing_payload_keys():
    with pytest.raises(ValueError):
        NewEvent(type="case.status", actor="system", payload={"from": "NEW"})
    with pytest.raises(ValueError):
        NewEvent(type="case.status", actor="system",
                 payload={"from": "NEW", "to": "READY", "extra": 1})
    with pytest.raises(ValueError):
        NewEvent(type="made.up", actor="system", payload={})
    with pytest.raises(ValueError):
        NewEvent(type="case.status", actor="robot", payload={"from": "a", "to": "b"})


def test_catalog_covers_every_contract_type():
    contract = (REPO_ROOT / "contracts" / "api.md").read_text(encoding="utf-8")
    for type_ in PAYLOAD_KEYS:
        assert f"`{type_}`" in contract, type_


# ---------------------------------------------------------------- redaction


@pytest.mark.parametrize("text", [
    "my SSN is 123-45-6789 thanks",
    "ssn 123 45 6789",
    "EIN: 12-3456789",
    "our ein is 123456789",
    "Social Security number: 123456789",
    "tax id 987654321.",
])
def test_ids_are_redacted(text):
    out, count = redact_ids(text)
    assert count == 1
    assert "[redacted]" in out
    assert not re.search(r"\d{4}", out)


@pytest.mark.parametrize("text", [
    "We have 100000000 authorized shares",  # 9 digits, but a share count
    "10,000,000 authorized and 8,000,000 issued",
    "call me at 2026-11-07",
])
def test_share_counts_survive_redaction(text):
    assert redact_ids(text) == (text, 0)


def test_mask_email_and_company():
    assert mask_email("founder@team7.dev") == "f•••@t•••.dev"
    assert company_label("founder@team7.dev") == "team7"
    assert company_label("someone@gmail.com") is None
    masked = mask_text("Team7 here, write to cfo@team7.dev. SSN 123-45-6789",
                       masked_name="Team #7", company="team7")
    assert "team7" not in masked.lower().replace("team #7", "")
    assert "cfo@" not in masked
    assert "123-45-6789" not in masked
