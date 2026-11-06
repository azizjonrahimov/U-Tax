"""HubSpotCRM (CRMPort) against a mocked HubSpot API: no live calls."""

from __future__ import annotations

import json

import httpx
import pytest

from app.gateway.adapters.hubspot import CONTACTS, DEALS, DEFAULT_STAGES, KEY_PROPERTY, HubSpotCRM
from app.gateway.ports import AuthExpired, CrmContact, FaultPlan, NotConfigured, PortError, RateLimited, Rejected, Unavailable

TOKEN = "pat-na1-test-0123456789"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The owner exports these for the live portal; the tests must not read them."""
    for name in ("HUBSPOT_PIPELINE", "HUBSPOT_STAGE_MAP"):
        monkeypatch.delenv(name, raising=False)


def store_handler(seen: list[httpx.Request]):
    """A tiny HubSpot: records are keyed by utax_key, so a repeated upsert returns the same id."""
    records: dict[tuple[str, str], str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        record = body["inputs"][0]
        kind = "contact" if request.url.path == CONTACTS else "deal" if request.url.path == DEALS else None
        if kind is None:
            return httpx.Response(404, json={"status": "error", "message": "not found", "category": "OBJECT_NOT_FOUND"})
        key = (kind, record["id"])
        new = key not in records
        records.setdefault(key, str(1000 + len(records)))
        return httpx.Response(200, json={"status": "COMPLETE", "results": [{
            "id": records[key], "new": new, "archived": False, "properties": record["properties"],
            "createdAt": "2026-11-07T00:00:00Z", "updatedAt": "2026-11-07T00:00:00Z"}],
            "startedAt": "2026-11-07T00:00:00Z", "completedAt": "2026-11-07T00:00:01Z"})

    return handler


def make(handler, **kwargs) -> HubSpotCRM:
    return HubSpotCRM(TOKEN, transport=httpx.MockTransport(handler), **kwargs)


def test_upsert_contact_is_idempotent_by_our_key():
    seen: list[httpx.Request] = []
    crm = make(store_handler(seen))
    a = crm.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    b = crm.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    c = crm.upsert_contact(CrmContact(key="case_8", label="Team #8"))
    assert a == b and a != c
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)
    record = json.loads(seen[0].content)["inputs"][0]
    assert record["id"] == "case_7" and record["idProperty"] == KEY_PROPERTY
    assert record["properties"][KEY_PROPERTY] == "case_7"


def test_only_the_masked_label_key_and_stage_leave_the_gateway():
    seen: list[httpx.Request] = []
    crm = make(store_handler(seen))
    crm.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    crm.upsert_contact(CrmContact(key="cand_1", label="Candidate A", kind="candidate"))
    crm.set_stage("deal:case_7", "memo_sent")
    sent = [json.loads(r.content)["inputs"][0]["properties"] for r in seen]
    assert set(sent[0]) == set(sent[1]) == {KEY_PROPERTY, "firstname", "lastname"}
    assert sent[0]["firstname"] == "Team #7" and sent[1]["firstname"] == "Candidate A"
    assert set(sent[2]) == {KEY_PROPERTY, "dealname", "pipeline", "dealstage"}
    assert not any(k in ("email", "amount", "phone", "company") for props in sent for k in props)


@pytest.mark.parametrize("label", ["Jane Doe", "jane.doe@acme.com", "Team #7 Jane", "Acme Inc", "", "Candidate Anna"])
def test_a_label_that_is_not_a_masked_shape_never_leaves(label):
    seen: list[httpx.Request] = []
    make(store_handler(seen)).upsert_contact(CrmContact(key="case_7<script>", label=label))
    props = json.loads(seen[0].content)["inputs"][0]["properties"]
    assert props["firstname"] == "U-Tax case_7script" and props[KEY_PROPERTY] == "case_7script"


def test_a_stage_never_moves_back():
    seen: list[httpx.Request] = []
    crm = make(store_handler(seen))
    crm.set_stage("deal:case_7", "call_booked")
    crm.set_stage("deal:case_7", "memo_sent")  # a late retry of the earlier job
    crm.set_stage("deal:case_8", "memo_sent")
    stages = [json.loads(r.content)["inputs"][0]["properties"]["dealstage"] for r in seen]
    assert stages == [DEFAULT_STAGES["call_booked"], DEFAULT_STAGES["memo_sent"]]
    assert json.loads(seen[1].content)["inputs"][0]["id"] == "deal:case_8"


def test_set_stage_maps_to_pipeline_stage_ids():
    seen: list[httpx.Request] = []
    crm = make(store_handler(seen))
    crm.set_stage("deal:case_7", "memo_sent")
    crm.set_stage("deal:case_7", "call_booked")
    assert seen[0].url.path == DEALS
    stages = [json.loads(r.content)["inputs"][0]["properties"]["dealstage"] for r in seen]
    assert stages == [DEFAULT_STAGES["memo_sent"], DEFAULT_STAGES["call_booked"]]
    assert json.loads(seen[0].content)["inputs"][0]["properties"]["pipeline"] == "default"


def test_stage_map_and_pipeline_can_be_configured(monkeypatch):
    monkeypatch.setenv("HUBSPOT_STAGE_MAP", '{"memo_sent": "1234567"}')
    monkeypatch.setenv("HUBSPOT_PIPELINE", "98765")
    seen: list[httpx.Request] = []
    make(store_handler(seen)).set_stage("deal:case_7", "memo_sent")
    props = json.loads(seen[0].content)["inputs"][0]["properties"]
    assert props["dealstage"] == "1234567" and props["pipeline"] == "98765"



@pytest.mark.parametrize("raw", ["not json", "[1]", "null", '"x"', '{"memo_sent": 5}', '{"memo_sent": ""}'])
def test_bad_stage_map_is_not_configured(monkeypatch, raw):
    monkeypatch.setenv("HUBSPOT_STAGE_MAP", raw)
    with pytest.raises(NotConfigured):
        HubSpotCRM(TOKEN)


def test_unknown_stage_is_rejected_without_a_call():
    seen: list[httpx.Request] = []
    with pytest.raises(Rejected, match="no pipeline stage"):
        make(store_handler(seen)).set_stage("deal:case_7", "teleported")
    assert seen == []


def test_partial_batch_failure_is_rejected():
    body = {"status": "COMPLETE", "results": [], "numErrors": 1,
            "errors": [{"status": "error", "category": "VALIDATION_ERROR", "message": "Property values were not valid"}]}
    with pytest.raises(Rejected, match="partial failure"):
        make(lambda r: httpx.Response(207, json=body)).upsert_contact(CrmContact(key="case_7", label="Team #7"))


@pytest.mark.parametrize("headers,expected", [({"retry-after": "3"}, 3.0), ({"retry-after": "soon"}, None), ({}, None)])
def test_ten_secondly_429_is_rate_limited(headers, expected):
    body = {"status": "error", "message": "You have reached your ten_secondly_rolling limit.",
            "errorType": "RATE_LIMIT", "policyName": "TEN_SECONDLY_ROLLING"}
    crm = make(lambda r: httpx.Response(429, headers=headers, json=body))
    with pytest.raises(RateLimited) as err:
        crm.set_stage("deal:case_7", "memo_sent")
    assert err.value.retry_after == expected and err.value.retryable


def test_daily_429_backs_off_for_an_hour():
    body = {"status": "error", "message": "daily limit", "errorType": "RATE_LIMIT", "policyName": "DAILY"}
    with pytest.raises(RateLimited) as err:
        make(lambda r: httpx.Response(429, json=body)).set_stage("deal:case_7", "memo_sent")
    assert err.value.retry_after == 3600.0


@pytest.mark.parametrize("status", [400, 207])
def test_missing_unique_property_says_what_to_set_up(status):
    error = {"status": "error", "category": "VALIDATION_ERROR",
             "message": f"Property {KEY_PROPERTY} does not exist"}
    body = error if status == 400 else {"status": "COMPLETE", "results": [], "errors": [error]}
    with pytest.raises(NotConfigured, match=KEY_PROPERTY):
        make(lambda r: httpx.Response(status, json=body)).upsert_contact(CrmContact(key="case_7", label="Team #7"))


def test_a_duplicate_key_conflict_is_not_mistaken_for_missing_setup():
    body = {"status": "error", "category": "CONFLICT", "message": f"{KEY_PROPERTY} must be unique: value already used"}
    with pytest.raises(Rejected):
        make(lambda r: httpx.Response(409, json=body)).upsert_contact(CrmContact(key="case_7", label="Team #7"))


@pytest.mark.parametrize("status,error", [(401, NotConfigured), (403, NotConfigured), (400, Rejected), (404, Rejected),
                                          (409, Rejected), (500, Unavailable), (502, Unavailable), (503, Unavailable)])
def test_errors_map(status, error):
    body = {"status": "error", "message": "m", "category": "X"}
    with pytest.raises(error):
        make(lambda r: httpx.Response(status, json=body)).upsert_contact(CrmContact(key="case_7", label="Team #7"))


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(200, text="<html>proxy</html>"),
    lambda r: httpx.Response(200, json={"results": []}),
    lambda r: httpx.Response(200, json={"results": ["x"]}),
    lambda r: httpx.Response(200, json=["a", "list"]),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
])
def test_malformed_replies_and_http_errors_are_port_errors(handler):
    with pytest.raises(PortError):
        make(handler).upsert_contact(CrmContact(key="case_7", label="Team #7"))


def test_empty_keys_are_rejected():
    with pytest.raises(Rejected):
        make(store_handler([])).upsert_contact(CrmContact(key="<>", label="x"))
    with pytest.raises(Rejected):
        make(store_handler([])).set_stage("  ", "memo_sent")


def test_no_token_is_not_configured_and_repr_hides_token():
    with pytest.raises(NotConfigured):
        HubSpotCRM("")
    assert TOKEN not in repr(HubSpotCRM(TOKEN))


def contract_factory(tmp_path, faults: FaultPlan) -> HubSpotCRM:
    """How tests/contract/test_ports.py CRM can list this adapter: FaultPlan errors become the
    matching HTTP answers, everything else is the keyed mock."""
    inner = store_handler([])

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            faults.check()
        except RateLimited:
            return httpx.Response(429, headers={"retry-after": "1"},
                                  json={"status": "error", "message": "x", "policyName": "TEN_SECONDLY_ROLLING"})
        except AuthExpired:
            return httpx.Response(401, json={"status": "error", "message": "x"})
        except PortError:
            return httpx.Response(503, json={"status": "error", "message": "x"})
        return inner(request)

    return make(handler)


def test_contract_style_crm_check(tmp_path):
    faults = FaultPlan()
    port = contract_factory(tmp_path, faults)
    a = port.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    b = port.upsert_contact(CrmContact(key="case_7", label="Team #7"))
    assert a == b
    port.set_stage("deal:case_7", "memo_sent")
    faults.add(RateLimited())
    with pytest.raises(RateLimited):
        port.set_stage("deal:case_7", "call_booked")


def test_a_corrupt_body_is_a_port_error():
    response = lambda r: httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"not gzip")
    with pytest.raises(Unavailable):
        make(response).upsert_contact(CrmContact(key="case_7", label="Team #7"))


def test_a_per_item_rate_limit_in_a_207_is_retryable():
    body = {"status": "COMPLETE", "results": [], "errors": [{"category": "RATE_LIMITS", "message": "slow down"}]}
    with pytest.raises(RateLimited):
        make(lambda r: httpx.Response(207, json=body)).set_stage("deal:case_7", "memo_sent")


@pytest.mark.parametrize("record_id", [{"x": 1}, True, "", None])
def test_an_odd_record_id_is_unavailable(record_id):
    body = {"results": [{"id": record_id}]}
    with pytest.raises(Unavailable):
        make(lambda r: httpx.Response(200, json=body)).upsert_contact(CrmContact(key="case_7", label="Team #7"))
