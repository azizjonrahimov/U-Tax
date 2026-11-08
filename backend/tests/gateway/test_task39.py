"""Task 39: the CEO's session is never accepted from the URL: /api/state and /api/events
take it as the Authorization bearer (fetch can send headers); ?token= is the theater's."""

from __future__ import annotations

import pytest

from .test_task30 import ADMIN, THEATER, bearer, login, site  # noqa: F401  (the site fixture)


@pytest.mark.parametrize("path", ["/api/state", "/api/events"])
def test_a_session_in_the_url_is_refused_and_the_bearer_works(site, path):
    h, client = site
    session = login(client).json()["session"]
    assert client.get(path, params={"token": session}).status_code == 401
    assert client.get(path, headers=bearer(session)).status_code == 200
    assert client.get(path, params={"token": THEATER}).status_code == 200


def test_only_the_bearer_gets_the_admin_view(site):
    h, client = site
    session = login(client).json()["session"]
    theater = client.get("/api/state", params={"token": THEATER}).json()
    ceo = client.get("/api/state", headers=bearer(session)).json()
    assert "candidates" not in theater["hiring"] and "candidates" in ceo["hiring"]
    # a session riding along in the URL next to the theater token adds nothing
    both = client.get("/api/state", params={"token": THEATER, "session": session}).json()
    assert "candidates" not in both["hiring"]
    assert "candidates" in client.get("/api/state", headers=bearer(ADMIN)).json()["hiring"]
