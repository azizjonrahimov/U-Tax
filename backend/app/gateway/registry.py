"""Pick adapters from env: ADAPTER_MAIL=local|agentmail, STORE=jsonl|postgres, ...

Sponsor adapters land Saturday; naming one before it exists fails at boot with a clear
message instead of silently falling back (a demo should never run on a surprise stand-in).
"""

from __future__ import annotations

import logging

from app.gateway.adapters import local
from app.gateway.adapters.jsonl_store import JsonlEventStore
from app.gateway.perform import Gateway
from app.gateway.ports import Ports
from app.gateway.ports.store import EventStorePort
from app.gateway.settings import Settings

SPONSORS = {
    "mail": ("agentmail",), "llm": ("openrouter", "openai"), "crm": ("hubspot",),
    "device": ("mobilerun",), "scrape": ("apify",), "toolbuy": ("monid",),
    "voice": ("openai",), "calendar": ("agentmail", "google"), "social": ("naive",), "pay": ("stripe",),
}


log = logging.getLogger("utax.registry")


class AdapterNotReady(RuntimeError):
    pass


def _local(name: str, settings: Settings):
    data, fixtures = settings.data_dir, settings.fixtures_dir
    return {
        "mail": lambda: local.LocalMail(data),
        "llm": lambda: local.LocalLLM(),
        "crm": lambda: local.LocalCRM(data),
        "device": lambda: local.LocalDevice(data),
        "scrape": lambda: local.LocalScrape(fixtures),
        "toolbuy": lambda: local.LocalToolBuy(),
        "voice": lambda: local.NoVoice(),
        "calendar": lambda: local.LocalCalendar(data),
        "social": lambda: local.LocalSocial(data),
        "pay": lambda: local.NoPay(),
    }[name]()


def _https(url: str, switch: str) -> str:
    """Bearer keys never travel over plain http (localhost aside, for a local fake)."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise AdapterNotReady(f"{switch}: the API base URL must be https ({url!r})")
    return url


def _need(value: str, env: str, switch: str) -> str:
    if not value:
        raise AdapterNotReady(f"{switch} needs {env} (set it in backend/.env)")
    return value


def _sponsor(name: str, choice: str, settings: Settings):
    """The sponsor adapters that exist (task 8). A missing key fails at boot, never later."""
    if name == "mail" and choice == "agentmail":
        from app.gateway.adapters.agentmail import AgentMailMail

        switch = "ADAPTER_MAIL=agentmail"
        key = _need(settings.agentmail_api_key, "AGENTMAIL_API_KEY", switch)
        _need(settings.inbox_clients, "MAIL_INBOX_CLIENTS", switch)
        if settings.agentmail_webhook_secret:
            from app.gateway.adapters.agentmail import secret_key

            try:
                secret_key(settings.agentmail_webhook_secret)
            except ValueError as err:
                raise AdapterNotReady(str(err)) from err
        elif not settings.hook_token:
            log.warning("%s: no AGENTMAIL_WEBHOOK_SECRET or UTAX_HOOK_TOKEN, so /hooks/agentmail "
                        "answers 503 and no inbound mail arrives", switch)
        if not settings.inbox_hiring:
            log.warning("%s: MAIL_INBOX_HIRING unset: hiring mail is sent from the clients "
                        "inbox and applicants can't write in", switch)
        return AgentMailMail(key, inboxes={"clients": settings.inbox_clients,
                                           "hiring": settings.inbox_hiring},
                             data_dir=settings.data_dir,
                             base_url=_https(settings.agentmail_base_url, switch))
    if name == "calendar" and choice == "agentmail":  # design D27
        from app.gateway.adapters.agentmail import AgentMailCalendar

        switch = "ADAPTER_CALENDAR=agentmail"
        if settings.adapters.get("mail") != "agentmail":
            # invitations are real mail: never send them for local (fixture) cases
            raise AdapterNotReady(f"{switch} needs ADAPTER_MAIL=agentmail too")
        return AgentMailCalendar(
            _need(settings.agentmail_api_key, "AGENTMAIL_API_KEY", switch),
            inbox=_need(settings.inbox_clients, "MAIL_INBOX_CLIENTS", switch),
            data_dir=settings.data_dir, base_url=_https(settings.agentmail_base_url, switch),
            location=settings.call_location)
    if name == "device" and choice == "mobilerun":  # task 9 (agents lane's adapter)
        from app.gateway.adapters.mobilerun import MobilerunDevice

        switch = "ADAPTER_DEVICE=mobilerun"
        if settings.adapters.get("mail") != "agentmail":
            raise AdapterNotReady(f"{switch} needs ADAPTER_MAIL=agentmail (the phone opens real "
                                  "mail)")
        _need(settings.mail_qa_bcc, "MAIL_QA_BCC", switch)  # the phone's mailbox
        return MobilerunDevice(_need(settings.mobilerun_api_key, "MOBILERUN_API_KEY", switch),
                               device_id=settings.mobilerun_device_id or None,
                               mail_app=settings.mobilerun_mail_app or None,
                               # mandatory: only memos from our own sender count
                               sender=_need(settings.mobilerun_memo_sender,
                                            "MOBILERUN_MEMO_SENDER", switch),
                               model=settings.mobilerun_model or None)
    if name == "scrape" and choice == "apify":  # task 9 (agents lane's adapter)
        from app.gateway.adapters.apify import ApifyScrape

        return ApifyScrape(_need(settings.apify_token, "APIFY_TOKEN", "ADAPTER_SCRAPE=apify"))
    if name == "crm" and choice == "hubspot":  # task 10 (agents lane's adapter)
        from app.gateway.adapters.hubspot import HubSpotCRM

        return HubSpotCRM(_need(settings.hubspot_token, "HUBSPOT_TOKEN", "ADAPTER_CRM=hubspot"))
    if name == "toolbuy" and choice == "monid":  # task 10 (agents lane's adapter)
        from app.gateway.adapters.monid import MonidToolBuy

        return MonidToolBuy(_need(settings.monid_api_key, "MONID_API_KEY",
                                  "ADAPTER_TOOLBUY=monid"))
    if name == "social" and choice == "naive":
        from app.gateway.adapters.naive_social import NaiveSocial

        switch = "ADAPTER_SOCIAL=naive"
        return NaiveSocial(_need(settings.naive_api_key, "NAIVE_API_KEY", switch),
                           identity=_need(settings.naive_social_identity,
                                          "NAIVE_SOCIAL_IDENTITY", switch),
                           data_dir=settings.data_dir, api_base=settings.naive_api_base)
    return None


def build_port(name: str, settings: Settings):
    choice = settings.adapters.get(name, "local")
    if choice in ("local", "none"):
        return _local(name, settings)
    if name == "voice" and choice == "openai":
        if not settings.openai_api_key:  # the routes then answer 503 voice_unavailable
            return local.NoVoice()
        from app.gateway.adapters.openai_voice import OpenAIVoice

        return OpenAIVoice(settings.openai_api_key)
    built = _sponsor(name, choice, settings)
    if built is not None:
        return built
    if choice in SPONSORS.get(name, ()):
        raise AdapterNotReady(f"ADAPTER_{name.upper()}={choice}: the sponsor adapter isn't "
                              "built yet (Saturday); use local")
    raise AdapterNotReady(f"ADAPTER_{name.upper()}={choice!r} is not a known adapter")


def build_ports(settings: Settings) -> Ports:
    return Ports(**{name: build_port(name, settings) for name in
                    ("mail", "llm", "crm", "device", "scrape", "toolbuy", "voice", "calendar",
                     "social", "pay")})


def build_store(settings: Settings) -> EventStorePort:
    if settings.store == "jsonl":
        return JsonlEventStore(settings.events_file)
    if settings.store == "postgres":
        if not settings.database_url:
            raise AdapterNotReady("STORE=postgres needs DATABASE_URL")
        from app.gateway.adapters.postgres_store import PostgresEventStore

        return PostgresEventStore(settings.database_url)
    raise AdapterNotReady(f"STORE={settings.store!r} must be jsonl or postgres")


def open_auth_problem(settings: Settings) -> str | None:
    """Empty tokens disable auth (local dev only). A production store, the event (a public
    crowd) or a public https /talk URL never runs open (task 14), and never with the admin
    token equal to the theater token (that one ships to every browser)."""
    for what, on in (("STORE=postgres", settings.store == "postgres"),
                     ("PUBLIC_MODE=event", settings.public_mode == "event"),
                     ("an https PUBLIC_TALK_URL",
                      settings.public_talk_url.strip().lower().startswith("https://"))):
        if not on:
            continue
        if not (settings.admin_token and settings.theater_token):
            return f"{what} needs ADMIN_TOKEN and THEATER_TOKEN set"
        if settings.admin_token == settings.theater_token:
            return f"{what} needs an ADMIN_TOKEN different from THEATER_TOKEN"
    return None


def build_gateway(settings: Settings | None = None) -> Gateway:
    settings = settings or Settings.from_env()
    problem = open_auth_problem(settings)
    if problem:
        raise AdapterNotReady(problem)
    return Gateway(build_store(settings), build_ports(settings), settings)
