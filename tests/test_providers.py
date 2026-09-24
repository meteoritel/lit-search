"""两个 Provider 的 URL 构造与字段映射（全部离线，基于手写 fixture）。"""

from __future__ import annotations

import json
import urllib.parse
from http.client import IncompleteRead

import pytest

import litsearch
from conftest import load_fixture, make_http, make_plan

OA = litsearch.PROVIDERS["openalex"]
CR = litsearch.PROVIDERS["crossref"]


def query_params(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


# --------------------------------------------------------------------------- #
# OpenAlex
# --------------------------------------------------------------------------- #

def test_openalex_url_carries_structured_filters():
    plan = make_plan(
        year="2021-2026", min_citations=5, journal_issn="1234-5678",
        work_type="article", oa_only=True, language="en", limit=10,
    )
    filt = query_params(OA.build_url("protist grazing", plan, "KEY", "*", 10))["filter"][0]
    assert "from_publication_date:2021-01-01" in filt
    assert "to_publication_date:2026-12-31" in filt
    assert "cited_by_count:>4" in filt
    assert "primary_location.source.issn:1234-5678" in filt
    assert "type:article" in filt
    assert "is_oa:true" in filt
    assert "language:en" in filt


def test_openalex_url_basics():
    plan = make_plan(limit=250)
    params = query_params(OA.build_url("q", plan, "KEY", "*", 100))
    assert params["search"] == ["q"]
    assert params["per_page"] == ["100"]
    assert params["cursor"] == ["*"]
    assert params["api_key"] == ["KEY"]
    assert params["select"] == [litsearch.OPENALEX_SELECT]


def test_openalex_single_year_becomes_full_range():
    plan = make_plan(year="2024")
    filt = query_params(OA.build_url("q", plan, "", "*", 10))["filter"][0]
    assert "from_publication_date:2024-01-01" in filt
    assert "to_publication_date:2024-12-31" in filt


def test_openalex_fetch_sort_maps_to_remote_sort():
    for fetch_sort, expected in (("citations", "cited_by_count:desc"),
                                 ("year", "publication_date:desc")):
        params = query_params(
            OA.build_url("q", make_plan(fetch_sort=fetch_sort), "K", "*", 10))
        assert params["sort"] == [expected]
    params = query_params(
        OA.build_url("q", make_plan(fetch_sort="relevance"), "K", "*", 10))
    assert "sort" not in params


def test_min_citations_zero_sends_no_citation_filter():
    """0 与 None 同义（不限）。原实现发 cited_by_count:>0，会丢掉全部零被引文献。"""
    for value in (None, 0):
        assert "cited_by_count" not in OA.filter_string(make_plan(min_citations=value))[0]
    assert "cited_by_count:>0" in OA.filter_string(make_plan(min_citations=1))[0]
    assert "cited_by_count:>4" in OA.filter_string(make_plan(min_citations=5))[0]


def test_decode_json_turns_non_json_body_into_network_error():
    """网关返回 HTML 错误页时必须是 NetworkError（退出码 4），不能漏出 JSONDecodeError。"""
    with pytest.raises(litsearch.NetworkError):
        litsearch.decode_json(b"<html>502 Bad Gateway</html>", "openalex")


def test_openalex_search_maps_non_json_body_to_network_error():
    plan = make_plan(limit=5)
    http, _ = make_http([b"<html>502 Bad Gateway</html>"])
    with pytest.raises(litsearch.NetworkError):
        OA.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())


def test_crossref_search_maps_non_json_body_to_network_error():
    plan = make_plan(sources=["crossref"], limit=5)
    http, _ = make_http([b"not json at all"])
    with pytest.raises(litsearch.NetworkError):
        CR.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())


def test_incomplete_read_is_retried_then_reported_as_network_error():
    """连接被截断（IncompleteRead）必须走重试 → NetworkError，而不是漏出裸异常。"""
    plan = make_plan(limit=5)
    policy = litsearch.RetryPolicy(attempts=2, sleep=lambda _: None)
    http, opener = make_http([IncompleteRead(b"x", 10)] * 2)
    with pytest.raises(litsearch.NetworkError):
        OA.search("q", plan, http, policy, litsearch.Config())
    assert len(opener.urls) == 2          # 确实重试了


def test_incomplete_read_then_success_recovers():
    plan = make_plan(limit=2)          # 只取首页（fixture 首页 2 条），不再翻页
    policy = litsearch.RetryPolicy(attempts=3, sleep=lambda _: None)
    http, opener = make_http([IncompleteRead(b"x", 1),
                              load_fixture("openalex_page1.json")])
    result = OA.search("q", plan, http, policy, litsearch.Config())
    assert result.records
    assert len(opener.urls) == 2


def test_openalex_author_and_institution_dispatch_id_vs_name():
    by_id = make_plan(author="A123", institution="I456")
    filt = OA.filter_string(by_id)[0]
    assert "authorships.author.id:A123" in filt
    assert "institutions.id:I456" in filt

    by_name = make_plan(author="Jane Smith", institution="University of X")
    filt2 = OA.filter_string(by_name)[0]
    assert "raw_author_name.search:Jane Smith" in filt2
    assert "raw_affiliation_strings.search:University of X" in filt2


def test_openalex_ror_url_is_normalised_to_ror_id():
    plan = make_plan(institution="https://ror.org/03yrm5c26")
    assert "institutions.ror:03yrm5c26" in OA.filter_string(plan)[0]


def test_oa_filter_overrides_structured_and_warns():
    plan = make_plan(year="2024", raw_filter="from_publication_date:2019-01-01")
    filt, warnings = OA.filter_string(plan)
    assert "from_publication_date:2019-01-01" in filt
    assert "from_publication_date:2024-01-01" not in filt
    assert any("覆盖" in w for w in warnings)


def test_oa_filter_appends_when_no_conflict():
    plan = make_plan(year="2024", raw_filter="is_retracted:false")
    filt, warnings = OA.filter_string(plan)
    assert filt.endswith("is_retracted:false")
    assert "from_publication_date:2024-01-01" in filt
    assert warnings == []


def test_openalex_map_work_full_record():
    body = json.loads(load_fixture("openalex_page1.json"))
    rec = OA.map_work(body["results"][0])
    assert rec.doi == "10.1000/aaa"
    assert rec.title == "Marine protist grazing on bacteria"
    assert rec.year == 2023
    assert rec.publication_date == "2023-05-01"
    assert rec.citation_count == 42
    assert rec.venue == "Journal of Marine Ecology"
    assert rec.abstract == "Marine protist grazing on bacteria"
    assert len(rec.authors) == 4
    assert rec.is_open_access is True
    assert rec.open_access_pdf == "https://example.org/a.pdf"
    assert rec.relevance_score == 12.5
    assert rec.source == "openalex"
    assert rec.sources == ["openalex"]
    assert rec.source_id == "https://openalex.org/W1001"


def test_openalex_map_work_missing_fields_are_none():
    body = json.loads(load_fixture("openalex_page1.json"))
    rec = OA.map_work(body["results"][1])
    assert rec.doi is None
    assert rec.abstract is None
    assert rec.is_open_access is False


# --------------------------------------------------------------------------- #
# Crossref
# --------------------------------------------------------------------------- #

def test_crossref_url_uses_query_not_bibliographic():
    plan = make_plan(sources=["crossref"], year="2020-2022", work_type="article",
                     author="Jane Smith", journal_issn="1234-5678")
    params = query_params(CR.build_url("grazing", plan, 50, 0, "me@example.com"))
    assert params["query"] == ["grazing"]
    assert "query.bibliographic" not in params
    assert params["query.author"] == ["Jane Smith"]
    assert params["rows"] == ["50"]
    assert params["offset"] == ["0"]
    assert params["mailto"] == ["me@example.com"]
    filt = params["filter"][0]
    assert "from-pub-date:2020-01-01" in filt
    assert "until-pub-date:2022-12-31" in filt
    assert "type:journal-article" in filt
    assert "issn:1234-5678" in filt


def test_crossref_fetch_sort_mapping():
    plan = make_plan(sources=["crossref"], fetch_sort="citations")
    params = query_params(CR.build_url("q", plan, 10, 0, None))
    assert params["sort"] == ["is-referenced-by-count"]
    assert params["order"] == ["desc"]

    plan_year = make_plan(sources=["crossref"], fetch_sort="year")
    params2 = query_params(CR.build_url("q", plan_year, 10, 0, None))
    assert params2["sort"] == ["published"]

    plan_rel = make_plan(sources=["crossref"], fetch_sort="relevance")
    assert "sort" not in query_params(CR.build_url("q", plan_rel, 10, 0, None))


def test_display_sort_does_not_change_the_remote_request():
    """--sort 只影响展示顺序，抓取哪一批由 --fetch-sort 决定。"""
    by_citations = make_plan(sources=["crossref"], sort="citations")
    by_relevance = make_plan(sources=["crossref"], sort="relevance")
    assert (CR.build_url("q", by_citations, 10, 0, None)
            == CR.build_url("q", by_relevance, 10, 0, None))
    oa_cit = make_plan(sort="citations")
    oa_rel = make_plan(sort="relevance")
    assert (OA.build_url("q", oa_cit, "K", "*", 10)
            == OA.build_url("q", oa_rel, "K", "*", 10))


def test_crossref_map_item_full_record():
    items = json.loads(load_fixture("crossref_page1.json"))["message"]["items"]
    rec = CR.map_item(items[0])
    assert rec.doi == "10.1000/aaa"
    assert rec.title == "MARINE PROTIST GRAZING ON BACTERIA."
    assert rec.year == 2023
    assert rec.publication_date == "2023-05-01"
    assert rec.citation_count == 39
    assert rec.abstract == "Marine protist grazing on bacteria across shelf seas."
    assert rec.authors == ["Alice Chen", "Bo Liu"]
    assert rec.venue == "Journal of Marine Ecology"
    assert rec.open_access_pdf == "https://example.org/a.pdf"
    assert rec.relevance_score is None      # Crossref 无此字段
    assert rec.is_open_access is None      # Crossref 无此字段
    assert rec.source == "crossref"


def test_crossref_map_item_falls_back_to_issued_and_partial_date():
    items = json.loads(load_fixture("crossref_page1.json"))["message"]["items"]
    rec = CR.map_item(items[1])
    assert rec.year == 2021
    assert rec.publication_date == "2021-01-01"
    assert rec.abstract is None
    assert rec.authors == ["Dana Kim"]


def test_crossref_map_item_returns_none_without_title_and_doi():
    items = json.loads(load_fixture("crossref_page1.json"))["message"]["items"]
    assert CR.map_item(items[2]) is None


def test_crossref_search_warns_on_unsupported_params():
    plan = make_plan(sources=["crossref"], institution="Some Uni", oa_only=True)
    http, _ = make_http([load_fixture("crossref_page1.json")])
    result = CR.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())
    assert any("--institution" in w for w in result.warnings)
    assert any("--oa-only" in w for w in result.warnings)


def test_crossref_min_citations_is_local_filter_and_warns():
    plan = make_plan(sources=["crossref"], limit=10, min_citations=10)
    http, opener = make_http([load_fixture("crossref_page1.json")])
    result = CR.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())
    assert [r.doi for r in result.records] == ["10.1000/aaa"]
    assert any("本地过滤" in w for w in result.warnings)
    assert len(opener.urls) == 1


def test_crossref_language_is_local_filter_and_warns():
    plan = make_plan(sources=["crossref"], limit=10, language="en")
    http, _ = make_http([load_fixture("crossref_page1.json")])
    result = CR.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())
    assert all((r.language or "").startswith("en") for r in result.records)
    assert any("--language" in w for w in result.warnings)


def test_crossref_total_comes_from_message():
    plan = make_plan(sources=["crossref"], limit=5)
    http, _ = make_http([load_fixture("crossref_page1.json")])
    result = CR.search("q", plan, http, litsearch.RetryPolicy(), litsearch.Config())
    assert result.total == 88
