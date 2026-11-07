"""Task 16 (CI run 42389174209): a deeply nested buy input is Rejected on every platform.

json.dumps used to be the only depth check, and its C limit differs by OS: on Linux it took
5000 levels and the recursive email walk then raised RecursionError. The bound tests use
depths far below any recursion limit, so they fail without the explicit bound on any OS; the
walk tests go past the limit on purpose, to prove the walks are iterative."""

from __future__ import annotations

import httpx
import pytest

from app.gateway.adapters import _http
from app.gateway.adapters.monid import _has_email
from app.gateway.ports import Rejected

from .test_monid import completed, counting, make


def chain(depth: int, make_level=lambda inner: {"a": inner}) -> object:
    node: object = "leaf"
    for _ in range(depth):
        node = make_level(node)
    return node


def buyer():
    calls, handler = counting(lambda request, n: httpx.Response(200, json=completed()))
    return make(handler), calls


@pytest.mark.parametrize("level", [lambda i: {"a": i}, lambda i: [i], lambda i: {"a": [i]}])
def test_input_over_the_bound_is_rejected_before_any_call(level):
    port, calls = buyer()
    with pytest.raises(Rejected, match="nest at most 32"):
        port.buy("enrichment", {"q": chain(100, level)}, key="k")
    assert calls == [] and not port._inflight and not port._unsure


def test_the_bound_itself_is_allowed():
    port, calls = buyer()
    assert port.buy("enrichment", chain(_http.MAX_DEPTH), key="ok").bought
    assert len(calls) == 1
    with pytest.raises(Rejected, match="nest at most"):
        port.buy("enrichment", chain(_http.MAX_DEPTH + 1), key="over")


def test_shared_sub_objects_cannot_multiply_the_walk():
    # 30 levels, each holding the level below twice: 2**30 paths through 31 objects
    node: object = "leaf"
    for _ in range(30):
        node = [node, node]
    assert _http.too_nested(node)  # the value budget stops it, quickly
    port, calls = buyer()
    with pytest.raises(Rejected, match="nest at most"):
        port.buy("enrichment", {"q": node}, key="k")
    assert calls == []


def test_the_value_budget_fails_fast():
    # 16 levels of shared halves: 65536 paths, cheap to walk even if the budget broke
    node: object = "leaf"
    for _ in range(16):
        node = [node, node]
    assert not _http.too_nested(node, max_values=10**6)
    assert _http.too_nested(node)


def test_dict_keys_count_toward_the_budget():
    assert _http.too_nested({str(i): 0 for i in range(5000)})  # 1 + 5000 keys + 5000 values
    assert not _http.too_nested({str(i): 0 for i in range(4999)})


def test_too_nested_edges():
    assert not _http.too_nested({})
    assert not _http.too_nested("text")
    assert not _http.too_nested({"q": ["a", {"b": 1}]})
    assert _http.too_nested(chain(5000))  # stops at the bound, never recurses
    assert _http.too_nested(list(range(_http.MAX_VALUES)))  # plus the list itself: over


def test_email_walk_is_iterative():
    # 5000 levels is past the default recursion limit (1000) on every OS
    assert not _has_email(chain(5000, lambda i: {"k": [i]}))
    deep_mail: dict = {}
    node = deep_mail
    for _ in range(5000):
        node["k"] = {}
        node = node["k"]
    node["k"] = "jane@client.dev"
    assert _has_email(deep_mail)
    assert _has_email({"jane@client.dev": 1})  # keys count too


def test_an_email_inside_the_bound_is_still_refused():
    port, calls = buyer()
    with pytest.raises(Rejected, match="email"):
        port.buy("enrichment", chain(30, lambda i: {"a": i}) | {"b": "jane@client.dev"}, key="k")
    assert calls == []
