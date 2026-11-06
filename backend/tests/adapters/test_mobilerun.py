"""MobilerunDevice (DevicePort) against a mocked Mobilerun Cloud API: no live calls."""

from __future__ import annotations

import json

import httpx
import pytest

from app.gateway.adapters.mobilerun import MobilerunDevice, TaskEnded, memo_reference
from app.gateway.ports import AuthExpired, DeviceCheck, FaultPlan, NotConfigured, PortError, RateLimited, Rejected, Unavailable
from app.gateway.templates import DISCLAIMER

KEY = "dr_sk_test_0123456789"
REF = "UTX-7F3A9C21"
TASK = "182bd5e5-6e1a-4fe4-a799-aa6d9a6ab26e"
MEMO = (
    "Hi Acme,\n\nThanks for asking U-Tax for a checkup. Delaware lets a corporation compute its franchise tax\n"
    "- Authorized shares method: $85,165\n- Assumed par value capital method: $400\n"
    f"{DISCLAIMER}\nReference: {REF}\n"
)
GOOD_OUTPUT = {"matches": 1, "found_email": True, "text_readable": True, "disclaimer_visible": True}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make(handler, **kwargs) -> tuple[MobilerunDevice, FakeClock]:
    clock = FakeClock()
    device = MobilerunDevice(KEY, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                             max_wait=kwargs.pop("max_wait", 30), poll_every=5, **kwargs)
    return device, clock


def happy(seen: list[httpx.Request], *, statuses=("running", "completed"), output=GOOD_OUTPUT, succeeded=True,
          urls=("https://cdn.mobilerun.ai/s/1.png", "https://cdn.mobilerun.ai/s/2.png"),
          devices=({"id": "dev-1", "activeTaskId": None},), poll_error: int | None = None):
    polls = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/v1/devices":
            return httpx.Response(200, json={"items": list(devices), "pagination": {}})
        if request.method == "POST" and path == "/v1/tasks":
            return httpx.Response(201, json={"id": TASK, "status": "queued", "streamUrl": None})
        if path == f"/v1/tasks/{TASK}/status":
            if poll_error:
                return httpx.Response(poll_error, json={"detail": "x"})
            status = next(polls, statuses[-1])
            body = {"status": status}
            if status in ("completed", "failed", "cancelled"):
                body |= {"succeeded": succeeded, "output": output, "message": "done"}
            return httpx.Response(200, json=body)
        if path == f"/v1/tasks/{TASK}/screenshots":
            return httpx.Response(200, json={"urls": list(urls)})
        if path == f"/v1/tasks/{TASK}/cancel":
            return httpx.Response(200, json={"status": "cancelling"})
        return httpx.Response(404, json={"title": "Not Found", "status": 404, "detail": path, "errors": []})

    return handler


def paths(seen):
    return [(r.method, r.url.path) for r in seen]


def test_success_opens_the_one_memo_and_returns_last_screenshot():
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen))
    check = device.open_and_capture("local-msg-7", MEMO)
    assert check == DeviceCheck(screenshot_url="https://cdn.mobilerun.ai/s/2.png", rendered_ok=True)
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in seen)
    devices = next(r for r in seen if r.url.path == "/v1/devices")
    assert dict(devices.url.params) == {"state": "ready", "mine": "true", "platform": "android", "type": "android_cloud_phone"}
    post = next(r for r in seen if r.method == "POST" and r.url.path == "/v1/tasks")
    body = json.loads(post.content)
    assert body["deviceId"] == "dev-1" and body["vision"] is True
    assert set(body["outputSchema"]["required"]) == {"matches", "found_email", "text_readable", "disclaimer_visible"}
    assert post.headers["idempotency-key"] == f"phone:{REF}"
    assert REF in body["task"] and "$85,165" not in body["task"], "only the reference goes to Mobilerun"
    assert ("POST", f"/v1/tasks/{TASK}/cancel") not in paths(seen)


def test_reference_comes_from_the_gateway_or_the_memo_footer_only():
    # a token anywhere else in the body (client text could carry one) never counts
    for body in ("Hi Acme,\nno reference here", f"Hi {REF},\nour company is {REF}\nthanks",
                 f"Reference: {REF}\nmore text after it"):
        seen: list[httpx.Request] = []
        device, _ = make(happy(seen))
        with pytest.raises(Rejected, match="Reference"):
            device.open_and_capture("m1", body)
        assert seen == []
    assert memo_reference(MEMO) == REF
    assert memo_reference("no footer", reference="UTX-ABCDEF12") == "UTX-ABCDEF12"
    with pytest.raises(Rejected):
        memo_reference(MEMO, reference="utx-lowercase")


def test_explicit_reference_is_what_the_phone_searches_for():
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen))
    device.open_and_capture("m1", "a memo without a footer", reference="UTX-ABCDEF12", key="phone:case_7:v2")
    post = next(r for r in seen if r.method == "POST")
    body = json.loads(post.content)
    assert "UTX-ABCDEF12" in body["task"] and body["reasoning"] is False
    assert post.headers["idempotency-key"] == "phone:case_7:v2"


def test_gateway_key_is_used_when_given_and_sender_filter_reaches_the_prompt():
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen), sender="memos@u-tax.example")
    device.open_and_capture("m1", MEMO, key="phone:case_7:v2")
    post = next(r for r in seen if r.method == "POST")
    assert post.headers["idempotency-key"] == "phone:case_7:v2"
    assert "from memos@u-tax.example" in json.loads(post.content)["task"]


def test_configured_device_skips_lookup_and_busy_devices_are_skipped():
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen), device_id="dev-pinned")
    device.open_and_capture("m1", MEMO)
    assert ("GET", "/v1/devices") not in paths(seen)
    seen2: list[httpx.Request] = []
    device, _ = make(happy(seen2, devices=({"id": "busy", "activeTaskId": "t-9"}, "junk", {"id": "idle"})))
    device.open_and_capture("m1", MEMO)
    assert json.loads(next(r for r in seen2 if r.method == "POST").content)["deviceId"] == "idle"


def test_found_but_unreadable_or_missing_disclaimer_is_not_rendered():
    for flaw in ({"text_readable": False}, {"disclaimer_visible": False}):
        device, _ = make(happy([], statuses=("completed",), output=GOOD_OUTPUT | flaw))
        assert device.open_and_capture("m1", MEMO).rendered_ok is False


@pytest.mark.parametrize("final,output,succeeded", [
    ("failed", GOOD_OUTPUT, False),
    ("cancelled", GOOD_OUTPUT, None),
    ("completed", GOOD_OUTPUT, False),
    ("completed", None, True),
    ("completed", GOOD_OUTPUT | {"found_email": False}, True),
    ("completed", GOOD_OUTPUT | {"matches": 3}, True),
    ("completed", GOOD_OUTPUT | {"matches": True}, True),
])
def test_any_other_ending_fails_closed_without_a_screenshot(final, output, succeeded):
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen, statuses=(final,), output=output, succeeded=succeeded))
    with pytest.raises(TaskEnded):
        device.open_and_capture("m1", MEMO)
    assert ("GET", f"/v1/tasks/{TASK}/screenshots") not in paths(seen), "a failed run never returns a frame"
    assert ("POST", f"/v1/tasks/{TASK}/cancel") not in paths(seen), "an ended task is not cancelled"


def test_no_screenshot_fails_closed():
    device, _ = make(happy([], urls=()))
    with pytest.raises(TaskEnded, match="no screenshot"):
        device.open_and_capture("m1", MEMO)


def test_429_maps_to_rate_limited_with_retry_after():
    device, _ = make(lambda r: httpx.Response(429, headers={"retry-after": "7"}, json={"detail": "slow down"}))
    with pytest.raises(RateLimited) as err:
        device.open_and_capture("m1", MEMO)
    assert err.value.retry_after == 7.0 and err.value.retryable


@pytest.mark.parametrize("status,error", [(401, AuthExpired), (403, AuthExpired), (402, Rejected),
                                          (409, Unavailable), (412, Unavailable), (422, Rejected),
                                          (500, Unavailable), (503, Unavailable)])
def test_errors_map_to_port_errors(status, error):
    body = {"title": "x", "status": status, "detail": "nope", "errors": []}
    device, _ = make(lambda r: httpx.Response(status, json=body))
    with pytest.raises(error):
        device.open_and_capture("m1", MEMO)


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(200, text="<html>a proxy page</html>"),
    lambda r: httpx.Response(200, json=["not", "a", "dict"]),
    lambda r: httpx.Response(200, json={"items": ["dev-1"]}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)),
    lambda r: (_ for _ in ()).throw(httpx.DecodingError("bad gzip", request=r)),
])
def test_malformed_replies_and_http_errors_are_port_errors(handler):
    device, _ = make(handler)
    with pytest.raises(PortError):
        device.open_and_capture("m1", MEMO)


def test_poll_failure_after_start_cancels_the_task():
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen, poll_error=503))
    with pytest.raises(Unavailable):
        device.open_and_capture("m1", MEMO)
    assert ("POST", f"/v1/tasks/{TASK}/cancel") in paths(seen)


def test_slow_task_times_out_and_is_cancelled():
    seen: list[httpx.Request] = []
    device, clock = make(happy(seen, statuses=("running",)), max_wait=12)
    with pytest.raises(Unavailable, match="still running"):
        device.open_and_capture("m1", MEMO)
    assert clock.now >= 12
    assert ("POST", f"/v1/tasks/{TASK}/cancel") in paths(seen)


def test_two_phase_start_then_finish():
    seen: list[httpx.Request] = []
    device, _ = make(happy(seen, statuses=("running", "completed")))
    task_id = device.start("m1", MEMO)
    assert task_id == TASK
    assert device.finish(task_id) is None  # still running
    assert device.finish(task_id).rendered_ok is True


def test_no_key_is_not_configured_and_repr_hides_key():
    with pytest.raises(NotConfigured):
        MobilerunDevice("")
    assert KEY not in repr(MobilerunDevice(KEY))


def contract_factory(tmp_path, faults: FaultPlan) -> MobilerunDevice:
    """How tests/contract/test_ports.py DEVICE can list this adapter: a FaultPlan error becomes
    the matching HTTP answer (429/401/503), everything else is the happy mock. The contract's
    memo body needs a UTX- reference for this adapter (it refuses ambiguous memos)."""
    inner = happy([])

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            faults.check()
        except RateLimited as exc:
            return httpx.Response(429, headers={"retry-after": str(exc.retry_after or 1)}, json={"detail": "x"})
        except AuthExpired:
            return httpx.Response(401, json={"detail": "x"})
        except PortError:
            return httpx.Response(503, json={"detail": "x"})
        return inner(request)

    clock = FakeClock()
    return MobilerunDevice(KEY, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock)


def test_contract_style_device_check(tmp_path):
    faults = FaultPlan()
    port = contract_factory(tmp_path, faults)
    check = port.open_and_capture("local-msg-1", f"Hi Team #7,\nEducational estimate\nReference: {REF}")
    assert isinstance(check.screenshot_url, str) and isinstance(check.rendered_ok, bool)
    faults.add(Unavailable())
    with pytest.raises(Unavailable):
        port.open_and_capture("local-msg-1", f"x\nReference: {REF}")


def test_post_timeout_recovers_the_task_by_key_and_cancels_it():
    seen: list[httpx.Request] = []
    inner = happy(seen)
    posts = {"n": 0}

    def handler(request):
        if request.method == "POST" and request.url.path == "/v1/tasks":
            posts["n"] += 1
            if posts["n"] == 1:
                raise httpx.ReadTimeout("slow", request=request)
        return inner(request)

    device, _ = make(handler)
    with pytest.raises(Unavailable, match="timed out"):
        device.open_and_capture("m1", MEMO)
    keys = [r.headers["idempotency-key"] for r in seen if r.method == "POST" and r.url.path == "/v1/tasks"]
    assert len(set(keys)) == 1, "the recovery POST reuses the same Idempotency-Key"
    assert ("POST", f"/v1/tasks/{TASK}/cancel") in paths(seen)


def test_screenshot_failure_after_completion_does_not_cancel():
    seen: list[httpx.Request] = []
    inner = happy(seen)

    def handler(request):
        if request.url.path.endswith("/screenshots"):
            seen.append(request)
            return httpx.Response(503, json={"detail": "x"})
        return inner(request)

    device, _ = make(handler)
    with pytest.raises(TaskEnded, match="screenshots unavailable"):
        device.open_and_capture("m1", MEMO)
    assert ("POST", f"/v1/tasks/{TASK}/cancel") not in paths(seen)


@pytest.mark.parametrize("bad", ["task-1", "../devices", "182bd5e5-6e1a-4fe4-a799-aa6d9a6ab26e\x00"])
def test_task_ids_must_be_uuids(bad):
    device, _ = make(happy([]))
    with pytest.raises(Unavailable, match="task id"):
        device.finish(bad)
    device.cancel(bad)  # best effort: never raises
