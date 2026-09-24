"""cursor 分页与 429/5xx 退避重试。"""

from __future__ import annotations

import json
import urllib.parse

import pytest

import litsearch
from conftest import SleepRecorder, load_fixture, make_http, make_plan

OA = litsearch.PROVIDERS["openalex"]
CR = litsearch.PROVIDERS["crossref"]
CONFIG = litsearch.Config(api_keys={"openalex": "test-key"})


def params_of(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


# --------------------------------------------------------------------------- #
# cursor 分页
# --------------------------------------------------------------------------- #

def test_openalex_follows_cursor_until_limit_reached():
    http, opener = make_http([
        load_fixture("openalex_page1.json"),
        load_fixture("openalex_page2.json"),
    ])
    plan = make_plan(limit=3)
    result = OA.search("q", plan, http, litsearch.RetryPolicy(), CONFIG)

    assert result.total == 137
    assert [r.doi for r in result.records] == ["10.1000/aaa", None, "10.1000/ddd"]
    assert len(opener.urls) == 2
    assert params_of(opener.urls[0])["cursor"] == ["*"]
    assert params_of(opener.urls[0])["per_page"] == ["3"]
    assert params_of(opener.urls[1])["cursor"] == ["CURSOR2"]
    assert params_of(opener.urls[1])["per_page"] == ["1"]


def test_openalex_stops_when_next_cursor_is_null():
    http, opener = make_http([
        load_fixture("openalex_page1.json"),
        load_fixture("openalex_page2.json"),
    ])
    plan = make_plan(limit=500)
    result = OA.search("q", plan, http, litsearch.RetryPolicy(), CONFIG)
    assert len(result.records) == 3          # 第二页 next_cursor 为 null
    assert len(opener.urls) == 2
    assert params_of(opener.urls[0])["per_page"] == ["100"]


def test_openalex_truncates_when_remote_overdelivers():
    http, _ = make_http([load_fixture("openalex_page1.json")])
    plan = make_plan(limit=1)
    result = OA.search("q", plan, http, litsearch.RetryPolicy(), CONFIG)
    assert len(result.records) == 1


def test_crossref_paginates_by_offset_without_extra_requests():
    http, opener = make_http([load_fixture("crossref_page1.json")])
    plan = make_plan(sources=["crossref"], limit=3)
    result = CR.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())
    assert len(opener.urls) == 1
    assert params_of(opener.urls[0])["offset"] == ["0"]
    assert params_of(opener.urls[0])["rows"] == ["3"]
    assert [r.doi for r in result.records] == ["10.1000/aaa", "10.1000/ccc"]


# --------------------------------------------------------------------------- #
# 退避重试
# --------------------------------------------------------------------------- #

def test_backoff_retries_429_then_succeeds():
    body = load_fixture("openalex_page2.json")
    http, opener = make_http([429, 429, body])
    sleeps = SleepRecorder()
    policy = litsearch.RetryPolicy(attempts=4, sleep=sleeps)
    out = litsearch.request_with_backoff(http, "https://example.org/x", policy)
    assert out == body
    assert sleeps.calls == [1.0, 2.0]
    assert len(opener.urls) == 3


def test_backoff_retries_5xx():
    body = load_fixture("openalex_page2.json")
    http, _ = make_http([503, body])
    policy = litsearch.RetryPolicy(attempts=3, sleep=lambda _: None)
    assert litsearch.request_with_backoff(http, "https://example.org/x", policy) == body


def test_backoff_exhaustion_raises_network_error():
    http, _ = make_http([500, 500])
    policy = litsearch.RetryPolicy(attempts=2, sleep=lambda _: None)
    with pytest.raises(litsearch.NetworkError):
        litsearch.request_with_backoff(http, "https://example.org/x", policy)


def test_backoff_does_not_retry_plain_4xx():
    http, opener = make_http([400])
    policy = litsearch.RetryPolicy(attempts=4, sleep=lambda _: None)
    with pytest.raises(litsearch.NetworkError):
        litsearch.request_with_backoff(http, "https://example.org/x", policy)
    assert len(opener.urls) == 1


def test_429_message_hints_at_the_api_key():
    http, _ = make_http([429])
    policy = litsearch.RetryPolicy(attempts=1, sleep=lambda _: None)
    with pytest.raises(litsearch.NetworkError, match="key"):
        litsearch.request_with_backoff(http, "https://example.org/x", policy)


def test_delay_is_exponential_and_capped():
    policy = litsearch.RetryPolicy(base=1.0, cap=5.0)
    assert [policy.delay(i) for i in range(5)] == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_search_all_inserts_polite_delay_between_requests():
    http, opener = make_http([load_fixture("openalex_page1.json")] * 3)
    sleeps = SleepRecorder()
    plan = make_plan(queries=["q1", "q2", "q3"], limit=2)
    litsearch.search_all(plan, CONFIG, http, litsearch.RetryPolicy(),
                         sleep_fn=sleeps, polite=0.5)
    assert len(opener.urls) == 3
    assert sleeps.calls == [0.5, 0.5]


def test_search_all_deduplicates_repeated_warnings():
    http, _ = make_http([load_fixture("crossref_page1.json")] * 2)
    plan = make_plan(queries=["q1", "q2"], sources=["crossref"], limit=3,
                     institution="Some Uni")
    _, _, warnings = litsearch.search_all(plan, litsearch.Config(), http,
                                          litsearch.RetryPolicy(),
                                          sleep_fn=lambda _: None)
    assert len(warnings) == len(set(warnings))


def test_provider_registry_has_expected_sources():
    assert set(litsearch.PROVIDERS) == {"openalex", "crossref"}
    assert litsearch.PROVIDERS["openalex"].requires_api_key is True
    assert litsearch.PROVIDERS["crossref"].requires_api_key is False


def test_search_all_reports_totals_per_source_and_query():
    http, _ = make_http([load_fixture("openalex_page1.json")])
    plan = make_plan(queries=["q1"], limit=2)
    _, totals, _ = litsearch.search_all(plan, CONFIG, http, litsearch.RetryPolicy(),
                                        sleep_fn=lambda _: None)
    assert totals == [("openalex · q1", 137)]
