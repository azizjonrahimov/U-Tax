"""Task 20: `python -m app.devtools agentmail-webhook <url>` (a quick tunnel's URL rotates)."""

from __future__ import annotations

import json

import httpx
import pytest

from app import devtools

SECRET = "whsec_" + "s" * 32


class FakeAgentMail:
    def __init__(self, hooks=None, patch_ok=True):
        self.hooks = list(hooks or [])
        self.patch_ok = patch_ok
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        assert request.headers["Authorization"] == "Bearer am_test"
        if request.method == "GET":
            return httpx.Response(200, json={"count": len(self.hooks), "webhooks": self.hooks})
        if request.method == "POST":
            body = json.loads(request.content)
            hook = {"webhook_id": "wh_new", "secret": SECRET, **body}
            self.hooks = [hook]
            return httpx.Response(200, json=hook)
        if request.method == "PATCH":
            if not self.patch_ok:
                return httpx.Response(400, json={"name": "ValidationError"})
            self.hooks[0]["url"] = json.loads(request.content)["url"]
            return httpx.Response(200, json=self.hooks[0])
        if request.method == "DELETE":
            self.hooks = []
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("AGENTMAIL_API_KEY", "MAIL_INBOX_CLIENTS", "MAIL_INBOX_HIRING",
                 "AGENTMAIL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text("# keep me\nAGENTMAIL_API_KEY=am_test\nMAIL_INBOX_CLIENTS=c@agentmail.to\n"
                    "MAIL_INBOX_HIRING=h@agentmail.to\nAGENTMAIL_WEBHOOK_SECRET=\nOTHER=1\n",
                    encoding="utf-8")
    return path


def secret_in(path):
    return devtools.env_file_values(path)["AGENTMAIL_WEBHOOK_SECRET"]


def test_a_first_webhook_is_created_and_its_secret_written_not_printed(env, capsys):
    fake = FakeAgentMail()
    outcome = devtools.agentmail_webhook("https://a.trycloudflare.com", env,
                                         transport=httpx.MockTransport(fake))
    assert outcome == "created" and secret_in(env) == SECRET
    [hook] = fake.hooks
    assert hook["url"] == "https://a.trycloudflare.com/hooks/agentmail"
    assert hook["event_types"] == ["message.received"]
    assert hook["inbox_ids"] == ["c@agentmail.to", "h@agentmail.to"]
    text = env.read_text(encoding="utf-8")
    assert text.startswith("# keep me\n") and "OTHER=1\n" in text  # nothing else moved
    assert SECRET not in capsys.readouterr().out


def test_a_rotated_tunnel_updates_the_url_in_place(env):
    fake = FakeAgentMail([{"webhook_id": "wh_1", "client_id": "utax-gateway",
                           "url": "https://old.trycloudflare.com/hooks/agentmail"}])
    assert devtools.agentmail_webhook("https://new.trycloudflare.com/", env,
                                      transport=httpx.MockTransport(fake)) == "updated"
    assert fake.hooks[0]["url"] == "https://new.trycloudflare.com/hooks/agentmail"
    assert secret_in(env) == ""  # the secret didn't change: nothing written


def test_a_webhook_that_cannot_be_patched_is_recreated_with_a_new_secret(env):
    fake = FakeAgentMail([{"webhook_id": "wh_1", "client_id": "utax-gateway",
                           "url": "https://old.trycloudflare.com/hooks/agentmail"}],
                         patch_ok=False)
    assert devtools.agentmail_webhook("https://new.trycloudflare.com", env,
                                      transport=httpx.MockTransport(fake)) == "recreated"
    assert "DELETE /v0/webhooks/wh_1" in fake.calls and secret_in(env) == SECRET


def test_the_same_url_changes_nothing(env):
    fake = FakeAgentMail([{"webhook_id": "wh_1", "client_id": "utax-gateway",
                           "url": "https://a.trycloudflare.com/hooks/agentmail"}])
    assert devtools.agentmail_webhook("https://a.trycloudflare.com", env,
                                      transport=httpx.MockTransport(fake)) == "unchanged"
    assert fake.calls == ["GET /v0/webhooks"]


def test_someone_elses_webhook_is_never_touched(env):
    fake = FakeAgentMail([{"webhook_id": "wh_x", "client_id": "another-app",
                           "url": "https://x.example/hook"}])
    devtools.agentmail_webhook("https://a.trycloudflare.com", env,
                               transport=httpx.MockTransport(fake))
    assert not any(c.startswith(("PATCH", "DELETE")) for c in fake.calls)


@pytest.mark.parametrize("url", ["http://a.trycloudflare.com", "a.trycloudflare.com",
                                 "https://a b.com"])
def test_only_a_public_https_url_is_accepted(env, url):
    with pytest.raises(SystemExit):
        devtools.agentmail_webhook(url, env, transport=httpx.MockTransport(FakeAgentMail()))
