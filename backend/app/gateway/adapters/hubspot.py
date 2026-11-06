"""CRMPort on HubSpot: every client case and job candidate becomes a contact, and each case's
deal moves through a pipeline (lead → memo sent → call booked). A mirror only: perform() calls
it from the outbox, outside the gateway lock, and never reads it back for decisions.

What HubSpot gets (design doc "What's stored where"): the masked label ("Team #7",
"Candidate A"), our stable key, and a stage. Never a name, an email, cap-table numbers or a
memo. This adapter only ever writes those fields.

API (date-versioned CRM, https://developers.hubspot.com/docs/api-reference/2026-03):
- contacts: `POST /crm/objects/2026-03/contacts/batch/upsert` with `idProperty: utax_key`;
- deals:    `POST /crm/objects/2026-03/0-3/batch/upsert` with `idProperty: utax_key`,
  `dealstage` an internal stage id of `pipeline` (HUBSPOT_PIPELINE, default "default").
Upserting by our own key makes both calls idempotent: the same key always maps to the same
record, and a retry after a lost reply can't create a duplicate.

One-time setup in the HubSpot portal (owner, Saturday): create a single-line text property
`utax_key` with "unique values" on Contacts and on Deals (Settings → Properties), and a private
app with scopes crm.objects.contacts.write and crm.objects.deals.write; its token goes in
HUBSPOT_TOKEN. Stage ids: defaults are the default sales pipeline's internal ids; override with
HUBSPOT_STAGE_MAP='{"memo_sent": "...", "call_booked": "..."}'.

UNVERIFIED (no token tonight): request/response shapes are from the 2026-03 OpenAPI reference
and the usage-guidelines page (429 body with policyName; X-HubSpot-RateLimit-* headers), read
on 2026-11-07; the default pipeline's stage ids in a fresh portal; whether a missing `utax_key`
property answers 400 VALIDATION_ERROR as assumed.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from app.gateway.ports import (
    AuthExpired,
    CrmContact,
    NotConfigured,
    PortError,
    RateLimited,
    Rejected,
    Unavailable,
)

API = "https://api.hubapi.com"
VERSION = "2026-03"
CONTACTS = f"/crm/objects/{VERSION}/contacts/batch/upsert"
DEALS = f"/crm/objects/{VERSION}/0-3/batch/upsert"
KEY_PROPERTY = "utax_key"
DEFAULT_STAGES = {  # our stage -> the default sales pipeline's internal stage ids
    "lead": "appointmentscheduled",
    "memo_sent": "presentationscheduled",
    "call_booked": "decisionmakerboughtin",
    "won": "closedwon",
    "lost": "closedlost",
}
_SAFE = re.compile(r"[^\w #:.-]")  # what a masked label / key may hold


def _retry_after(response: httpx.Response) -> float | None:
    for header, scale in (("retry-after", 1.0), ("x-hubspot-ratelimit-interval-milliseconds", 0.001)):
        value = response.headers.get(header)
        try:
            if value:
                return float(value) * scale
        except ValueError:
            continue
    return None


def _body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _map_error(response: httpx.Response) -> PortError:
    status, body = response.status_code, _body(response)
    message = str(body.get("message") or response.text)[:200]
    category = str(body.get("category") or body.get("errorType") or "")
    if status == 429:
        if body.get("policyName") == "DAILY":  # the day's quota: backoff in seconds won't help
            return RateLimited(f"hubspot daily limit: {message}", retry_after=3600.0)
        return RateLimited(f"hubspot rate limited: {message}", retry_after=_retry_after(response) or 10.0)
    if status in (401, 403):  # bad/expired token, or MISSING_SCOPES
        return AuthExpired(f"hubspot {status} {category}: {message}")
    if status >= 500:
        return Unavailable(f"hubspot {status}: {message}", retry_after=_retry_after(response))
    if KEY_PROPERTY in message and ("exist" in message.lower() or "unique" in message.lower()):
        return NotConfigured(f"hubspot: create the unique '{KEY_PROPERTY}' property first ({message})")
    return Rejected(f"hubspot {status} {category}: {message}")


def _safe(value: str, limit: int = 120) -> str:
    return _SAFE.sub("", value)[:limit].strip()


class HubSpotCRM:
    provider = "hubspot"

    def __init__(self, token: str, *, pipeline: str | None = None, stages: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 15.0) -> None:
        if not token:
            raise NotConfigured("hubspot: no HUBSPOT_TOKEN")
        self.pipeline = pipeline or os.environ.get("HUBSPOT_PIPELINE", "default")
        if stages is None:
            raw = os.environ.get("HUBSPOT_STAGE_MAP", "").strip()
            try:
                stages = DEFAULT_STAGES | (json.loads(raw) if raw else {})
            except ValueError as exc:
                raise NotConfigured("hubspot: HUBSPOT_STAGE_MAP is not JSON") from exc
        self.stages = stages
        self._http = httpx.Client(base_url=API, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never show the token
        return "HubSpotCRM()"

    def close(self) -> None:
        self._http.close()

    def _upsert(self, path: str, key: str, properties: dict[str, str]) -> str:
        payload = {"inputs": [{"id": key, "idProperty": KEY_PROPERTY,
                               "properties": {KEY_PROPERTY: key, **properties}}]}
        try:
            response = self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise Unavailable(f"hubspot unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        body = _body(response)
        if response.status_code == 207 or body.get("errors"):
            errors = body.get("errors") or []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            raise Rejected(f"hubspot partial failure: {str(first.get('message') or errors)[:200]}")
        results = body.get("results")
        record = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise Unavailable("hubspot returned no record id")
        return record_id

    def upsert_contact(self, contact: CrmContact) -> str:
        key = _safe(contact.key, 100)
        if not key:
            raise Rejected("hubspot: empty contact key")
        label = _safe(contact.label) or key
        properties = {"firstname": label, "lastname": "(U-Tax)"}
        if contact.kind == "client":
            properties["lifecyclestage"] = "lead"
        return self._upsert(CONTACTS, key, properties)

    def set_stage(self, deal_key: str, stage: str) -> None:
        key = _safe(deal_key, 100)
        stage_id = self.stages.get(stage)
        if not key:
            raise Rejected("hubspot: empty deal key")
        if not stage_id:
            raise Rejected(f"hubspot: no pipeline stage for '{stage}' (set HUBSPOT_STAGE_MAP)")
        self._upsert(DEALS, key, {"dealname": f"U-Tax {key}", "pipeline": self.pipeline, "dealstage": stage_id})
