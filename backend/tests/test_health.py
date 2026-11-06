from fastapi.testclient import TestClient

from app.main import app
from app.errors import APIError

client = TestClient(app)


def test_health_response():
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == {"status": "ok", "version": "0.1.0"}


def test_health_cors_allows_frontend():
    res = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_preflight_allows_frontend():
    res = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_other_origin():
    res = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in res.headers


def test_unknown_route_uses_contract_error_shape():
    res = client.get("/api/nope")
    assert res.status_code == 404
    assert res.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def _error_app() -> TestClient:
    from fastapi import FastAPI

    from app.errors import install_error_handlers

    demo = FastAPI()
    install_error_handlers(demo)

    @demo.get("/conflict")
    def conflict():
        raise APIError(409, "already_exists", "Thing already exists")

    @demo.get("/items/{item_id}")
    def item(item_id: int):
        return {"id": item_id}

    @demo.get("/crash")
    def crash():
        raise RuntimeError("secret detail")

    return TestClient(demo, raise_server_exceptions=False)


def test_api_error_uses_contract_error_shape():
    res = _error_app().get("/conflict")
    assert res.status_code == 409
    assert res.json() == {"error": {"code": "already_exists", "message": "Thing already exists"}}


def test_validation_error_uses_contract_error_shape():
    res = _error_app().get("/items/abc")
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "validation_error"
    assert "item_id" in body["error"]["message"]


def test_unhandled_error_hides_details():
    res = _error_app().get("/crash")
    assert res.status_code == 500
    assert res.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}
