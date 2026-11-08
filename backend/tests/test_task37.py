"""Task 37: devtools preflight (read-only demo checks) and archive-log (a clean start)."""

from __future__ import annotations

import httpx
import pytest

from app.gateway.settings import Settings
from app.naive_confirm import AGENT_NAMES
from app.preflight import archive_log, preflight

SECRETS = {"naive": "nk_secret_" + "n" * 20, "agentmail": "am_secret_" + "a" * 20,
           "eleven": "el_secret_" + "e" * 20}
PUBLIC = "https://demo.u-tax.test"


def settings(tmp_path, **over) -> Settings:
    base = dict(data_dir=tmp_path / "data", public_url=PUBLIC,
                naive_api_key=SECRETS["naive"], naive_social_identity="utax-mkt",
                agentmail_api_key=SECRETS["agentmail"], inbox_clients="checkup@u-tax.test",
                inbox_hiring="hiring@u-tax.test", elevenlabs_api_key=SECRETS["eleven"])
    return Settings(**{**base, **over})


class World:
    """Every service the preflight reads, all healthy unless told otherwise."""

    def __init__(self) -> None:
        self.agents = list(AGENT_NAMES)
        self.naive_status = 200
        self.hook_url = PUBLIC + "/hooks/agentmail"
        self.chars = (1000, 100_000)
        self.platforms = ["x", "linkedin"]
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.seen.append(f"{request.method} {host}{path}")
        assert request.method == "GET", "preflight is read-only"
        if path == "/api/health":
            return httpx.Response(200, json={"ok": True})
        if host == "api.vetta.sh" and path == "/v1/agents":
            if self.naive_status != 200:
                return httpx.Response(self.naive_status, headers={"Retry-After": "30"},
                                      json={"error": {"code": "rate_limited"}})
            return httpx.Response(200, json={"data": [{"id": f"a{i}", "name": n}
                                                      for i, n in enumerate(self.agents)],
                                             "has_more": False})
        if host == "api.vetta.sh" and path == "/v1/identities/utax-mkt/social/accounts":
            return httpx.Response(200, json={"data": [{"platform": p} for p in self.platforms]})
        if host == "api.agentmail.to" and path.startswith("/v0/inboxes/"):
            return httpx.Response(200, json={"inbox_id": path.rsplit("/", 1)[1]})
        if host == "api.agentmail.to" and path == "/v0/webhooks":
            return httpx.Response(200, json={"webhooks": [
                {"webhook_id": "wh_1", "client_id": "utax-gateway", "url": self.hook_url,
                 "secret": "whsec_should_never_print"}]})
        if host == "api.elevenlabs.io" and path == "/v1/user/subscription":
            used, limit = self.chars
            return httpx.Response(200, json={"character_count": used, "character_limit": limit})
        return httpx.Response(404, json={})


def run(tmp_path, world, **over):
    lines: list[str] = []
    code = preflight(settings(tmp_path, **over), httpx.MockTransport(world), lines.append,
                     color=False)
    return code, lines


def test_all_green_reads_only_and_prints_no_secret(tmp_path):
    world = World()
    code, lines = run(tmp_path, world)
    text = "\n".join(lines)
    assert code == 0, text
    assert not any(line.startswith("RED") for line in lines), text
    for secret in [*SECRETS.values(), "whsec_should_never_print"]:
        assert secret not in text
    assert "all 8 agents exist (incl. ceo-assistant)" in text
    assert "99,000 characters left" in text and "has X and LinkedIn" in text
    assert lines[-1].endswith("0 red")
    assert not list((tmp_path / "data" / "docs").iterdir())  # the probe file is gone


@pytest.mark.parametrize("break_it,needle", [
    (lambda w: w.agents.remove("ceo-assistant"), "missing agents: ceo-assistant"),
    (lambda w: setattr(w, "naive_status", 429), "at capacity (429), retry after 30 s"),
    (lambda w: setattr(w, "hook_url", "https://old-tunnel.test/hooks/agentmail"),
     "points elsewhere"),
    (lambda w: setattr(w, "chars", (99_500, 100_000)), "500 characters left (under 2,000)"),
    (lambda w: w.platforms.remove("linkedin"), "missing LinkedIn"),
])
def test_each_problem_is_a_red_line_and_exit_1(tmp_path, break_it, needle):
    world = World()
    break_it(world)
    code, lines = run(tmp_path, world)
    reds = [line for line in lines if line.startswith("RED")]
    assert code == 1 and len(reds) == 1 and needle in reds[0], lines


def test_unset_keys_are_red_with_the_variable_named(tmp_path):
    code, lines = run(tmp_path, World(), naive_api_key="", elevenlabs_api_key="",
                      public_url="")
    text = "\n".join(lines)
    assert code == 1
    for name in ("NAIVE_API_KEY", "ELEVENLABS_API_KEY", "UTAX_PUBLIC_URL"):
        assert name in text


def test_archive_needs_yes_and_a_stopped_gateway(tmp_path):
    s = settings(tmp_path)
    s.data_dir.mkdir(parents=True)
    s.events_file.write_text('{"id": 1}\n', encoding="utf-8")
    (s.data_dir / "docs" / "case_1").mkdir(parents=True)
    out: list[str] = []
    assert archive_log(s, yes=False, out=out.append, running=lambda: False) == 2
    assert archive_log(s, yes=True, out=out.append, running=lambda: True) == 2
    assert "gateway is running" in out[-1] and s.events_file.exists()
    assert archive_log(s, yes=True, out=out.append, running=lambda: False) == 0
    [target] = list((s.data_dir / "archive").iterdir())
    assert (target / "events.jsonl").read_text(encoding="utf-8") == '{"id": 1}\n'
    assert (target / "docs" / "case_1").is_dir()
    assert not s.events_file.exists() and not (s.data_dir / "docs").exists()
    assert archive_log(s, yes=True, out=out.append, running=lambda: False) == 0
    assert out[-1].startswith("nothing to archive")


def test_archive_refuses_a_database_store(tmp_path):
    out: list[str] = []
    assert archive_log(settings(tmp_path, store="postgres"), yes=True, out=out.append,
                       running=lambda: False) == 2
    assert "STORE=postgres" in out[-1]
