"""Task 29 (contract v1.12.3): an unparsed CEO command becomes an order proposal."""

from __future__ import annotations

import pytest

from app import ceo_tools


def command(h, text):
    return h.gw.perform("ceo_command", {"text": text})


@pytest.mark.parametrize("said", ["advertise our firm", "hire 2 people",
                                  "follow up with the customer"])
def test_the_demo_orders_become_order_proposals(h, said):
    r = command(h, said)
    assert r.status == "done" and r.data["kind"] == "proposal", r.as_dict()
    assert r.data["readback"] == f'Send this order to the Chief of Staff: "{said}". Say confirm.'
    proposed = h.events("ceo.proposed")[-1]
    assert proposed.payload["tool"] == "order" and proposed.payload["source"] == "command"


def test_a_confirmed_free_order_wakes_the_chief_of_staff(h):
    woke = []
    h.gw.on_wake(woke.append)
    proposal = command(h, "hire 2 people").data
    done = h.gw.perform("ceo_confirm", {"proposal_id": proposal["proposal_id"],
                                        "decision": "confirm", "via": "voice", "heard": "confirm"})
    assert done.status == "done", done.error
    assert len(woke) == 1 and woke[0].endswith(": hire 2 people")
    assert woke[0].startswith("CEO order_")


@pytest.mark.parametrize("said", ["ok", "hm", "yes please", "no, cancel", "never mind",
                                  "what is pending in the queue?", "how much did we make",
                                  "is anything blocked", "x" * 1201])  # D41: 1200 max
def test_confirm_cancel_questions_and_odd_lengths_stay_unknown(h, said):
    r = command(h, said)
    assert r.data["kind"] in ("unknown", "confirm", "cancel"), (said, r.data)
    assert not h.events("ceo.proposed")


def test_an_empty_command_is_bad_input(h):
    assert command(h, "  ").status == "refused" and not h.events("ceo.proposed")


def test_a_polite_request_and_a_negated_order_are_orders():
    assert ceo_tools.free_order("can you advertise the firm") == "can you advertise the firm"
    assert ceo_tools.free_order("don't post anything today") == "don't post anything today"


def test_a_free_order_is_masked_and_read_back_as_sent(h):
    r = command(h, "email jane.doe@example.com about the new deadline").data
    assert "jane.doe@example.com" not in r["readback"]
    assert "jane.doe@example.com" not in str(h.events("ceo.proposed")[-1].private)
