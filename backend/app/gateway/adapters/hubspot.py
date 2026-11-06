"""CRMPort on HubSpot: every client case and job candidate becomes a contact, and each case's
deal moves through a pipeline (memo sent → call booked). A mirror only: perform() calls it from
the outbox, outside the gateway lock, and never reads it back for decisions.

What HubSpot gets (design doc "What's stored where"): the masked label ("Team #7",
"Candidate A"), our stable key, and a stage. Never a name, an email, cap-table numbers or a
memo. The adapter enforces it: a label that isn't one of those two shapes is replaced by
"U-Tax <key>", and only these fields are ever written.

API (date-versioned CRM, https://developers.hubspot.com/docs/api-reference/2026-03):
- contacts: `POST /crm/objects/2026-03/contacts/batch/upsert` with `idProperty: utax_key`;
- deals:    `POST /crm/objects/2026-03/0-3/batch/upsert` with `idProperty: utax_key`,
  `dealstage` an internal stage id of `pipeline` (HUBSPOT_PIPELINE, default "default").
Upserting by our own key makes both calls idempotent: the same key always maps to the same
record, and a retry after a lost reply can't create a duplicate. Stages only move forward:
a late retry of an earlier stage (memo_sent after call_booked) is skipped. That memory is per
process; the outbox should also send each case's jobs in order (reported to the backend).

Errors follow the shared mapping in `_http` (429 RateLimited, 5xx/transport Unavailable,
400/404/409 Rejected), refined for HubSpot: 401 and 403 (bad token, missing scope) and a
missing `utax_key` property are NotConfigured; a DAILY-policy 429 asks for an hour's wait
(the outbox ignores retry_after today: reported).

One-time setup in the HubSpot portal (owner, Saturday): create a single-line text property
`utax_key` with "unique values" on Contacts and on Deals (Settings → Properties), and a private
app with scopes crm.objects.contacts.write and crm.objects.deals.write; its token goes in
HUBSPOT_TOKEN. Stage ids: defaults are the default sales pipeline's internal ids; override with
HUBSPOT_STAGE_MAP='{"memo_sent": "...", "call_booked": "..."}'.

UNVERIFIED (no token tonight): request/response shapes are from the 2026-03 OpenAPI reference
and the usage-guidelines page (429 body with policyName), read on 2026-11-07; the default
pipeline's stage ids in a fresh portal; whether a missing `utax_key` property answers 400 with
"does not exist" as assumed; that `utax_key` may appear both as `id` and in `properties`.
Deals are not associated with their contact (not needed for the mirror).
"""

from __future__ import annotations

import json
import os
import re
import threading

import httpx

from app.gateway.adapters import _http
from app.gateway.ports import CrmContact, NotConfigured, PortError, RateLimited, Rejected, Unavailable

API = "https://api.hubapi.com"
VERSION = "2026-03"
CONTACTS = f"/crm/objects/{VERSION}/contacts/batch/upsert"
DEALS = f"/crm/objects/{VERSION}/0-3/batch/upsert"
KEY_PROPERTY = "utax_key"
DEFAULT_STAGES = {"memo_sent": "presentationscheduled", "call_booked": "decisionmakerboughtin"}
STAGE_ORDER = ("memo_sent", "call_booked")  # a deal never moves back along this list
_LABEL = re.compile(r"Team #\d{1,6}|Candidate [A-Z]{1,3}")
_KEY = re.compile(r"[^A-Za-z0-9_:.-]")


def _missing_key_property(message: str) -> bool:
    text = message.lower()
    return KEY_PROPERTY in text and ("does not exist" in text or "doesn't exist" in text)


def _classify(status: int, body: dict, message: str) -> PortError | None:
    """HubSpot refinements on top of the shared mapping; None means use the shared one."""
    if status == 429:
        if str(body.get("policyName", "")).upper() == "DAILY":
            return RateLimited("hubspot daily limit reached", retry_after=3600.0)
        return None
    if status in (401, 403):
        return NotConfigured(f"hubspot token rejected or missing a scope ({status})")
    if _missing_key_property(message):
        return NotConfigured(f"hubspot: create the unique '{KEY_PROPERTY}' property on Contacts and Deals first")
    return None


def _key(value: str) -> str:
    return _KEY.sub("", value)[:100]


class HubSpotCRM:
    provider = "hubspot"

    def __init__(self, token: str, *, pipeline: str | None = None, stages: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: httpx.Timeout | float = _http.TIMEOUT) -> None:
        if not token:
            raise NotConfigured("hubspot: no HUBSPOT_TOKEN")
        self.pipeline = pipeline or os.environ.get("HUBSPOT_PIPELINE") or "default"
        if stages is None:
            raw = os.environ.get("HUBSPOT_STAGE_MAP", "").strip()
            try:
                override = json.loads(raw) if raw else {}
            except ValueError as exc:
                raise NotConfigured("hubspot: HUBSPOT_STAGE_MAP is not JSON") from exc
            if not isinstance(override, dict):
                raise NotConfigured("hubspot: HUBSPOT_STAGE_MAP must be a JSON object")
            stages = DEFAULT_STAGES | override
        if not all(isinstance(v, str) and v.strip() for v in stages.values()):
            raise NotConfigured("hubspot: every stage id must be a non-empty string")
        self.stages = dict(stages)
        self._lock = threading.Lock()
        self._reached: dict[str, int] = {}  # deal key -> furthest stage sent (index in STAGE_ORDER)
        self._http = httpx.Client(base_url=API, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never show the token
        return "HubSpotCRM()"

    def close(self) -> None:
        self._http.close()

    def _upsert(self, path: str, key: str, properties: dict[str, str]) -> str:
        payload = {"inputs": [{"id": key, "idProperty": KEY_PROPERTY,
                               "properties": {KEY_PROPERTY: key, **properties}}]}
        response = self._http_call(path, payload)
        body = _http.json_dict(response)
        errors = body.get("errors")
        if response.status_code == 207 or errors:
            first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
            refined = _classify(400, first, str(first.get("message", ""))[:200])
            raise refined or Rejected(f"hubspot partial failure: {first.get('category') or 'error'}")
        results = body.get("results")
        record = results[0] if isinstance(results, list) and results else None
        if not isinstance(record, dict) or not record.get("id"):
            raise Unavailable("hubspot: upsert answered without a record id")
        return str(record["id"])

    def _http_call(self, path: str, payload: dict) -> httpx.Response:
        try:
            response = self._http.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise Unavailable("hubspot timed out") from exc
        except httpx.TransportError as exc:
            raise Unavailable(f"hubspot unreachable: {type(exc).__name__}") from exc
        if not response.is_success:
            body = _http.json_dict(response)
            refined = _classify(response.status_code, body, str(body.get("message", ""))[:200])
            if refined is not None:
                raise refined
        return _http.check(response, "hubspot")

    def upsert_contact(self, contact: CrmContact) -> str:
        key = _key(contact.key)
        if not key:
            raise Rejected("hubspot: contact key is empty")
        label = contact.label if _LABEL.fullmatch(contact.label or "") else f"U-Tax {key}"
        return self._upsert(CONTACTS, key, {"firstname": label, "lastname": "(U-Tax)"})

    def set_stage(self, deal_key: str, stage: str) -> None:
        key = _key(deal_key)
        if not key:
            raise Rejected("hubspot: deal key is empty")
        stage_id = self.stages.get(stage)
        if not stage_id:
            raise Rejected(f"hubspot: no pipeline stage for '{stage}' (HUBSPOT_STAGE_MAP)")
        rank = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1
        with self._lock:
            if rank >= 0 and self._reached.get(key, -1) > rank:
                return  # a late retry of an earlier stage: the deal is already further along
        self._upsert(DEALS, key, {"dealname": f"U-Tax {key}", "pipeline": self.pipeline, "dealstage": stage_id})
        if rank >= 0:
            with self._lock:
                self._reached[key] = max(self._reached.get(key, -1), rank)
