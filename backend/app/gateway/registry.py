"""Pick adapters from env: ADAPTER_MAIL=local|agentmail, STORE=jsonl|postgres, ...

Sponsor adapters land Saturday; naming one before it exists fails at boot with a clear
message instead of silently falling back (a demo should never run on a surprise stand-in).
"""

from __future__ import annotations

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


def _need(value: str, env: str, switch: str) -> str:
    if not value:
        raise AdapterNotReady(f"{switch} needs {env} (set it in backend/.env)")
    return value


def _sponsor(name: str, choice: str, settings: Settings):
    """The sponsor adapters that exist (task 8). A missing key fails at boot, never later."""
    if name == "mail" and choice == "agentmail":
        from app.gateway.adapters.agentmail import AgentMailMail

        key = _need(settings.agentmail_api_key, "AGENTMAIL_API_KEY", "ADAPTER_MAIL=agentmail")
        _need(settings.inbox_clients, "MAIL_INBOX_CLIENTS", "ADAPTER_MAIL=agentmail")
        return AgentMailMail(key, inboxes={"clients": settings.inbox_clients,
                                           "hiring": settings.inbox_hiring},
                             data_dir=settings.data_dir, base_url=settings.agentmail_base_url)
    if name == "calendar" and choice == "agentmail":  # design D27
        from app.gateway.adapters.agentmail import AgentMailCalendar

        switch = "ADAPTER_CALENDAR=agentmail"
        return AgentMailCalendar(
            _need(settings.agentmail_api_key, "AGENTMAIL_API_KEY", switch),
            inbox=_need(settings.inbox_clients, "MAIL_INBOX_CLIENTS", switch),
            data_dir=settings.data_dir, base_url=settings.agentmail_base_url)
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


def build_gateway(settings: Settings | None = None) -> Gateway:
    settings = settings or Settings.from_env()
    if settings.store == "postgres" and not (settings.admin_token and settings.theater_token):
        # empty tokens disable auth (local dev only); a production store never runs open
        raise AdapterNotReady("STORE=postgres needs ADMIN_TOKEN and THEATER_TOKEN set")
    return Gateway(build_store(settings), build_ports(settings), settings)
