"""来源分页、身份保留、链接语义和请求策略。"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from dataclasses import asdict

import pytest

import litsearch
from conftest import SleepRecorder, load_fixture, make_http, make_plan, make_record


@pytest.mark.parametrize("dates,year,publication_date,warnings", [
    ({}, None, None, []),
    ({"issued": {"date-parts": [[None]]}}, None, None, ["issued.date-parts"]),
    ({"issued": {"date-parts": [[2024]]}}, 2024, "2024-01-01", []),
    ({"published": {"date-parts": [[2024, 5]]},
      "issued": {"date-parts": [[None]]}}, 2024, "2024-05-01", ["issued.date-parts"]),
    ({"published": {"date-parts": [[None]]},
      "issued": {"date-parts": [[2023, 5, 1]]}}, 2023, "2023-05-01", ["published.date-parts"]),
    ({"issued": {"date-parts": [[2024, None, 15]]}}, 2024, None, ["issued.date-parts"]),
    ({"issued": {"date-parts": [[None, 5, 15]]}}, None, None, ["issued.date-parts"]),
])
def test_crossref_nullable_dates_through_complete_search(dates, year, publication_date, warnings):
    body = json.dumps({"message": {"total-results": 1, "items": [
        {"DOI": "10.1000/date", "title": ["Date contract"], **dates},
    ]}}).encode()
    http, opener = make_http([body])
    result = litsearch.SearchClient(litsearch.Config(), http).search(
        make_plan(sources=["crossref"], limit=1))
    assert result.records[0].year == year
    assert result.records[0].publication_date == publication_date
    diagnostics = [d for d in result.report.diagnostics if d.code == "provider_warning"]
    assert [d.field for d in diagnostics] == warnings
    assert all(d.source == "crossref" and d.query == "q" and "10.1000/date" in d.message
               for d in diagnostics)
    assert result.report.queries[0].mapped_count == 1
    assert opener.remaining == []


@pytest.mark.parametrize("issued", [[], {"date-parts": ["2024"]},
                                    {"date-parts": [["2024"]]}, {"date-parts": [[True]]}])
def test_crossref_bad_dates_keep_network_error_and_identify_record(issued):
    body = json.dumps({"message": {"total-results": 1, "items": [
        {"DOI": "10.1000/bad-date", "issued": issued},
    ]}}).encode()
    http, _ = make_http([body])
    with pytest.raises(litsearch.NetworkError, match="10.1000/bad-date.*issued"):
        litsearch.SearchClient(litsearch.Config(), http).search(make_plan(sources=["crossref"]))


def test_nullable_dates_across_pages_and_sources(monkeypatch):
    monkeypatch.setattr(litsearch, "CROSSREF_PAGE_SIZE", 1)
    pages = [json.dumps({"message": {"total-results": 2, "items": [item],
                                    "next-cursor": "next"}}).encode() for item in [
        {"title": ["An identifiable work without a DOI"], "issued": {"date-parts": [[None]]}},
        {"DOI": "10.1/dated", "issued": {"date-parts": [[2024]]}},
    ]]
    pages.append(json.dumps({"hitCount": 1, "resultList": {"result": [
        {"id": "1", "source": "MED", "doi": "10.1/epmc"},
    ]}}).encode())
    http, opener = make_http(pages)
    result = litsearch.SearchClient(litsearch.Config(), http).search(
        make_plan(sources=["crossref", "europepmc"], limit=2))
    assert len(result.records) == 3
    assert result.report.queries[0].fetched_count == 2
    warning = next(d for d in result.report.diagnostics if d.field == "issued.date-parts")
    assert "第 1 条记录" in warning.message
    assert opener.remaining == []


def test_bad_crossref_doi_remains_a_network_error():
    http, _ = make_http([json.dumps({"message": {"total-results": 1, "items": [{"DOI": 42}]}}).encode()])
    with pytest.raises(litsearch.NetworkError, match="第 1 条记录"):
        litsearch.SearchClient(litsearch.Config(), http).search(make_plan(sources=["crossref"]))


def test_crossref_missing_titles_are_retained_with_one_diagnostic_across_pages(monkeypatch):
    monkeypatch.setattr(litsearch, "CROSSREF_PAGE_SIZE", 3)
    pages = [json.dumps({"message": {"total-results": 6, "items": items,
                                    "next-cursor": "next"}}).encode() for items in [
        [{"DOI": "10.1/missing"}, {"DOI": "10.1/null", "title": None},
         {"DOI": "10.1/empty", "title": []}],
        [{"DOI": "10.1/blank", "title": ["<p> </p>"]}, {"title": []},
         {"DOI": "10.1/named", "title": ["Named work"], "type": "grant"}],
    ]]
    http, opener = make_http(pages)
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["crossref"], limit=6, strict_filters=True, merge_policy="doi"))
    assert len(result.records) == 5
    assert sum(record.title is None for record in result.records) == 4
    warnings = [d for d in result.report.diagnostics if d.field == "title"]
    assert len(warnings) == 1
    assert warnings[0].code == "provider_warning"
    assert warnings[0].source == "crossref" and warnings[0].query == "q"
    assert "4 条" in warnings[0].message
    execution = result.report.queries[0]
    assert (execution.fetched_count, execution.mapped_count, execution.returned_count) == (6, 5, 5)
    assert next(record for record in result.records if record.doi == "10.1/named").work_type == "grant"
    assert sum(record["title"] is None for record in result.to_dict()["results"]) == 4
    for rendered in [litsearch.render_csv(result.records),
                     litsearch.render_markdown(result.report, result.records)]:
        assert "10.1/missing" in rendered
    assert opener.remaining == []


def test_cross_source_merge_can_fill_a_crossref_missing_title():
    http, _ = make_http([
        json.dumps({"message": {"total-results": 1, "items": [
            {"DOI": "10.1/shared", "is-referenced-by-count": 10},
        ]}}).encode(),
        json.dumps({"meta": {"count": 1}, "results": [
            {"id": "W123", "doi": "10.1/shared", "title": "Recovered title"},
        ]}).encode(),
    ])
    result = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http).search(
        make_plan(sources=["crossref", "openalex"], strict_filters=True))
    assert result.records[0].title == "Recovered title"
    assert result.records[0].source == "crossref"
    assert result.records[0].source_ids == {"crossref": ["10.1/shared"], "openalex": ["W123"]}
    assert next(d for d in result.report.diagnostics if d.field == "title").source == "crossref"


@pytest.mark.parametrize("source", ["openalex", "crossref"])
@pytest.mark.parametrize("fetch_sort", ["relevance", "citations", "year"])
def test_fetch_sort_preserves_query_filters_and_cursor_on_every_page(source, fetch_sort, monkeypatch):
    monkeypatch.setattr(litsearch, "CROSSREF_PAGE_SIZE", 1)
    if source == "crossref":
        pages = [json.dumps({"message": {"total-results": 2, "next-cursor": "cursor+next",
                                        "items": [{"DOI": f"10.1/{i}", "title": [f"Paper {i}"]}]}}).encode()
                 for i in range(2)]
    else:
        pages = [json.dumps({"meta": {"count": 2, "next_cursor": "cursor+next" if i == 0 else None},
                            "results": [{"id": f"W{i}", "title": f"Paper {i}"}]}).encode()
                 for i in range(2)]
    http, opener = make_http(pages)
    plan = make_plan(queries=['"marine protist" grazing'], sources=[source], limit=2,
                     year="2024", work_type="article", journal_issn="1234-5678", fetch_sort=fetch_sort)
    result = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http).search(plan)
    for index, url in enumerate(opener.urls):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["query" if source == "crossref" else "search"] == plan.queries
        assert params["cursor"] == ["*" if index == 0 else "cursor+next"]
        assert "2024" in params["filter"][0] and "1234-5678" in params["filter"][0]
        assert "article" in params["filter"][0]
        expected = ({"relevance": "score", "citations": "is-referenced-by-count", "year": "published"}
                    if source == "crossref" else
                    {"citations": "cited_by_count:desc", "year": "publication_date:desc"}).get(fetch_sort)
        assert params.get("sort") == ([expected] if expected else None)
    assert len(opener.urls) == 2
    assert [hit["rank"] for record in result.records for hit in record.retrieval_hits] == [1, 2]


def test_crossref_cursor_retrieves_beyond_the_single_request_maximum():
    pages = []
    for page in range(11):
        items = [{"DOI": f"10.1000/{page}-{i}", "title": [f"Work {page}-{i}"]}
                 for i in range(100)]
        pages.append(json.dumps({"message": {
            "total-results": 5000, "items": items, "next-cursor": f"cursor+{page}",
        }}).encode())
    http, opener = make_http(pages)
    client = litsearch.SearchClient(litsearch.Config(), http)
    result = client.search(make_plan(sources=["crossref"], limit=1100))
    assert len(result.records) == 1100
    assert result.report.queries[0].fetched_count == 1100
    assert result.report.queries[0].stop_reason == "limit"
    second = urllib.parse.parse_qs(urllib.parse.urlparse(opener.urls[1]).query)
    assert second["cursor"] == ["cursor+0"]
    assert all("offset=" not in url for url in opener.urls)
    for url in opener.urls:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert params["sort"] == ["score"]
        assert params["order"] == ["desc"]
    assert opener.remaining == []


def test_crossref_scan_budget_is_explicit_when_local_filters_reject_candidates():
    items = [{"DOI": f"10.1/{i}", "title": [f"T{i}"], "is-referenced-by-count": 0}
             for i in range(3)]
    body = json.dumps({"message": {"total-results": 50, "items": items,
                                    "next-cursor": "next"}}).encode()
    http, _ = make_http([body])
    result = litsearch.SearchClient(litsearch.Config(), http).search(
        make_plan(sources=["crossref"], limit=1, min_citations=5))
    assert result.records == []
    assert result.report.queries[0].fetched_count == 3
    assert result.report.queries[0].stop_reason == "scan_budget"


def test_crossref_rejects_missing_cursor_when_more_pages_are_needed():
    items = [{"DOI": f"10.1/{i}"} for i in range(100)]
    body = json.dumps({"message": {"total-results": 200, "items": items}}).encode()
    http, _ = make_http([body])
    with pytest.raises(litsearch.NetworkError, match="next-cursor"):
        litsearch.SearchClient(litsearch.Config(), http).search(
            make_plan(sources=["crossref"], limit=200))


def test_polite_delay_applies_between_pages():
    http, opener = make_http([load_fixture("openalex_page1.json"),
                              load_fixture("openalex_page2.json")])
    waits = SleepRecorder()
    http.polite, http.sleep = 0.5, waits
    client = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http)
    client.search(make_plan(limit=3))
    assert len(opener.urls) == 2
    assert waits.calls == [0.5]


@pytest.mark.parametrize("header,expected", [
    ("9", 9.0), ("Thu, 01 Jan 1970 00:00:12 GMT", 12.0), ("bad", 1.0), ("inf", 1.0),
])
def test_retry_after_controls_the_backoff(header, expected):
    error = urllib.error.HTTPError("https://example.org", 429, "rate limited",
                                   {"Retry-After": header}, io.BytesIO())
    http, _ = make_http([error, b"ok"])
    waits = SleepRecorder()
    policy = litsearch.RetryPolicy(sleep=waits, now=lambda: 0)
    assert litsearch.request_with_backoff(http, "https://example.org", policy) == b"ok"
    assert waits.calls == [expected]


def test_network_errors_redact_request_credentials():
    error = urllib.error.URLError("https://example.org?api_key=secret&query=q")
    http, _ = make_http([error])
    with pytest.raises(litsearch.NetworkError) as caught:
        litsearch.request_with_backoff(http, "https://example.org", litsearch.RetryPolicy(attempts=1))
    assert "secret" not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_title_merge_preserves_all_identities_without_mutating_inputs():
    left = make_record(doi="10.1/pre", title="Deep sea protist grazing study",
                       source_id="W123", raw_work_types={"openalex": ["preprint"]})
    right = make_record(doi="10.2/pub", title="Deep sea protist grazing study",
                        source="crossref", sources=["crossref"], source_id="10.2/pub",
                        raw_work_types={"crossref": ["journal-article"]})
    before = [asdict(left), asdict(right)]
    records, _ = litsearch.merge_records([left, right])
    assert records[0].doi_aliases == ["10.1/pre", "10.2/pub"]
    assert records[0].source_ids == {"openalex": ["W123"], "crossref": ["10.2/pub"]}
    assert records[0].raw_work_types == {"openalex": ["preprint"], "crossref": ["journal-article"]}
    assert records[0].merge_reasons == ["title"]
    assert [asdict(left), asdict(right)] == before


def test_conservative_merge_preserves_different_dois_and_records_title_association():
    records, stats = litsearch.merge_records([
        make_record(doi="10.1/pre", title="Deep sea protist grazing study"),
        make_record(doi="10.2/pub", title="Deep sea protist grazing study"),
    ], policy="doi")
    assert len(records) == 2
    assert stats.title_merged == 0
    assert {r.doi: r.related_dois for r in records} == {
        "10.1/pre": ["10.2/pub"], "10.2/pub": ["10.1/pre"],
    }


@pytest.mark.parametrize("policy", ["title", "doi"])
def test_title_identity_normalizes_unicode_case_and_punctuation_through_search(policy):
    http, _ = make_http([json.dumps({"message": {"total-results": 2, "items": [
        {"DOI": "10.1/a", "title": ["Ｒｅ-evaluation_of Marine Protists"]},
        {"DOI": "10.1/b", "title": ["Reevaluation of marine protists"]},
    ]}}).encode()])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["crossref"], merge_policy=policy))
    if policy == "title":
        assert len(result.records) == 1
        assert result.records[0].doi_aliases == ["10.1/a", "10.1/b"]
        assert result.report.stats.title_merged == 1
    else:
        assert {record.doi: record.related_dois for record in result.records} == {
            "10.1/a": ["10.1/b"], "10.1/b": ["10.1/a"],
        }


def test_repeated_source_identity_without_doi_is_merged():
    records, stats = litsearch.merge_records([
        make_record(title="First title", source_id="W123"),
        make_record(title="Second title", source_id="W123"),
    ], policy="doi")
    assert len(records) == 1
    assert stats.source_id_merged == 1


def test_primary_source_id_is_not_filled_from_a_different_source():
    records, _ = litsearch.merge_records([
        make_record(doi="10.1/a", source_id="", citation_count=50),
        make_record(doi="10.1/a", source="crossref", sources=["crossref"],
                    source_id="10.1/a", citation_count=10),
    ])
    assert records[0].source == "openalex"
    assert records[0].source_id == ""
    assert records[0].source_ids == {"crossref": ["10.1/a"]}


def test_openalex_distinguishes_readable_page_from_pdf():
    record = litsearch.PROVIDERS["openalex"].map_work({
        "id": "W123", "title": "T", "type": "article",
        "open_access": {"is_oa": True, "oa_url": "https://example.org/read"},
        "best_oa_location": {"landing_page_url": "https://example.org/read", "pdf_url": None},
    })
    assert record.oa_url == "https://example.org/read"
    assert record.landing_page_url == "https://example.org/read"
    assert record.pdf_url is None
    assert record.raw_work_types == {"openalex": ["article"]}


def test_crossref_type_is_normalized_and_pdf_does_not_imply_oa():
    record = litsearch.PROVIDERS["crossref"].map_item({
        "DOI": "10.1/a", "type": "journal-article",
        "link": [{"content-type": "application/pdf", "URL": "https://example.org/a.pdf"}],
    })
    assert record.work_type == "article"
    assert record.raw_work_types == {"crossref": ["journal-article"]}
    assert record.pdf_url == "https://example.org/a.pdf"
    assert record.is_open_access is None
    assert record.oa_url is None


def test_strict_filters_reject_overrides_before_network():
    http, opener = make_http([])
    client = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http)
    plan = make_plan(year="2024", raw_filter="from_publication_date:2020-01-01",
                     strict_filters=True)
    with pytest.raises(litsearch.UsageError):
        client.search(plan)
    explained = client.explain(plan)
    assert explained["sources"][0]["filter_execution"]["year"] == "overridden"
    assert explained["diagnostics"][0]["field"] == "year"
    assert opener.urls == []

    pub_year_plan = make_plan(year="2024", raw_filter="publication_year:2023", strict_filters=True)
    with pytest.raises(litsearch.UsageError):
        client.search(pub_year_plan)
    assert client.explain(pub_year_plan)["sources"][0]["filter_execution"]["year"] == "overridden"
    assert opener.urls == []


def test_crossref_unsupported_work_type_is_diagnosed_and_skipped():
    http, opener = make_http([json.dumps({"message": {"total-results": 0, "items": []}}).encode()])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], work_type="review")
    explained = client.explain(plan)
    assert explained["sources"][0]["filter_execution"]["work_type"] == "unsupported"
    assert explained["diagnostics"][0]["field"] == "work_type"
    result = client.search(plan)
    assert result.records == []
    assert "type:review" not in opener.urls[0]

    strict_http, strict_opener = make_http([])
    with pytest.raises(litsearch.UsageError):
        litsearch.SearchClient(litsearch.Config(), strict_http).search(
            make_plan(sources=["crossref"], work_type="review", strict_filters=True))
    assert strict_opener.urls == []


@pytest.mark.parametrize("author", [" A123 ", "https://openalex.org/A123"])
def test_crossref_marks_openalex_author_ids_as_unsupported(author):
    http, opener = make_http([])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], author=author, strict_filters=True)
    with pytest.raises(litsearch.UsageError):
        client.search(plan)
    assert client.explain(plan)["sources"][0]["filter_execution"]["author"] == "unsupported"
    assert opener.urls == []


def test_invalid_abstract_positions_are_a_source_error():
    body = json.dumps({"meta": {"count": 1}, "results": [{
        "title": "Paper", "abstract_inverted_index": {"word": ["1"]},
    }]}).encode()
    http, _ = make_http([body])
    client = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http)
    with pytest.raises(litsearch.NetworkError):
        client.search(make_plan(limit=1))
