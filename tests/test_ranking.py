"""跨查询融合与字段精排通过 SearchClient 验证。"""

import json
import math

import pytest

import litsearch
from conftest import load_fixture, make_http, make_plan


def page(items):
    return json.dumps({"message": {"total-results": len(items), "items": items}}).encode()


def item(doi, title, score=1, **extra):
    return {"DOI": doi, "title": [title], "score": score, **extra}


def test_rrf_rewards_independent_queries_and_deduplicates_repeated_queries():
    http, opener = make_http([
        page([item("10.1/a", "First unique", 1000), item("10.1/shared", "Shared evidence", 1)]),
        page([item("10.1/b", "Second unique", 10000), item("10.1/shared", "Shared evidence", 2)]),
    ])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        queries=["first", "second", "first"], sources=["crossref"], sort="rrf"))
    assert result.records[0].doi == "10.1/shared"
    assert result.records[0].ranking_score == pytest.approx(2 / 62)
    assert {hit["query"] for hit in result.records[0].retrieval_hits} == {"first", "second"}
    assert len(opener.urls) == 2
    assert result.records[0].relevance_score == 2


def test_retrieval_ranks_are_global_across_pages():
    http, _ = make_http([load_fixture("openalex_page1.json"), load_fixture("openalex_page2.json")])
    result = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http).search(
        make_plan(limit=3, sort="rrf"))
    assert sorted(r.retrieval_hits[0]["rank"] for r in result.records) == [1, 2, 3]


@pytest.mark.parametrize("sort", ["bm25", "hybrid"])
def test_field_ranking_prefers_title_evidence_and_explains_missing_abstract(sort):
    http, _ = make_http([page([
        item("10.1/noise", "Ocean circulation", 1000, **{"is-referenced-by-count": 500}),
        item("10.1/target", "Protist seasonal communities", 1,
             abstract="Sampling marine protists every month."),
    ])])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        queries=["protist seasonal"], sources=["crossref"], sort=sort))
    assert result.records[0].doi == "10.1/target"
    assert result.records[0].ranking_evidence["matched_groups"]
    assert result.records[1].ranking_evidence["abstract_missing"] is True


def test_field_scoped_require_and_exclude():
    http, _ = make_http([page([
        item("10.1/a", "Protist community", abstract="Seasonal sampling"),
        item("10.1/b", "Physical oceanography", abstract="Protist seasonal sampling"),
    ])])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["crossref"], require=[["title:protist"], ["abstract:seasonal"]],
        exclude=["title:physical"], sort="bm25"))
    assert [r.doi for r in result.records] == ["10.1/a"]
    assert result.report.filters.per_group == [1, 1]


@pytest.mark.parametrize("sort", ["bm25", "hybrid"])
def test_topic_ranking_prefers_complete_concept_evidence(sort):
    response = page([
        item("10.1/partial", "Protist protist protist protist", 1000),
        item("10.1/complete", "Protist seasonal marine sampling", 1),
    ])
    http, _ = make_http([response, response])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        queries=[], sources=["crossref"], sort=sort,
        concepts=[["protist"], ["seasonal"], ["marine"]]))
    assert result.records[0].doi == "10.1/complete"


def test_crossref_malformed_score_is_reported():
    http, _ = make_http([page([item("10.1/a", "Paper", "bad")])])
    with pytest.raises(litsearch.NetworkError, match="relevance_score"):
        litsearch.SearchClient(litsearch.Config(), http).search(make_plan(sources=["crossref"]))


@pytest.mark.parametrize("term,expected", [
    ("marine", math.log(1.6) * (2 + 2.2 / 3.1)),
    ("title:marine", 2 * math.log(8 / 3)),
])
def test_bm25_uses_filtered_pool_union_df_and_field_lengths_before_truncation(term, expected):
    http, _ = make_http([page([
        item("10.1/a", "Marine protist", abstract="marine study"),
        item("10.1/b", "Protist study", abstract="marine"),
        item("10.1/c", "Other paper"),
        item("10.1/excluded", "Unrelated work", abstract="marine"),
    ])])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        queries=[], concepts=[[term]], sources=["crossref"], exclude=["title:unrelated"],
        sort="bm25", max_results=1))
    assert result.report.filters.after == 3
    assert result.to_dict()["truncated"] == 2
    record = result.records[0]
    assert record.doi == "10.1/a"
    assert record.ranking_evidence["bm25"] == pytest.approx(expected)
    assert record.ranking_score == pytest.approx(expected)


def test_hybrid_uses_dense_lexical_ranks_for_equal_scores():
    http, _ = make_http([page([
        item("10.1/a", "Protist alpha"), item("10.1/b", "Protist beta"),
        item("10.1/c", "Other gamma"),
    ])])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        queries=["protist"], sources=["crossref"], sort="hybrid"))
    records = {record.doi: record for record in result.records}
    assert records["10.1/a"].ranking_evidence["bm25"] == records["10.1/b"].ranking_evidence["bm25"]
    assert records["10.1/a"].ranking_score == pytest.approx(2 / 61)
    assert records["10.1/b"].ranking_score == pytest.approx(1 / 62 + 1 / 61)
    assert records["10.1/c"].ranking_score == 0


@pytest.mark.parametrize("sort,expected", [
    ("rrf", 0.5 / 61), ("bm25", math.log(4 / 3)), ("hybrid", 1 / 61),
])
def test_ancillary_penalty_only_changes_final_score(sort, expected):
    http, _ = make_http([page([item("10.1/a", "Supplementary material to protist dynamics")])])
    record = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        queries=["protist"], sources=["crossref"], sort=sort)).records[0]
    assert record.ranking_evidence["record_flags"] == ["ancillary"]
    assert record.ranking_evidence["rrf"] == pytest.approx(1 / 61)
    assert record.ranking_evidence["bm25"] == pytest.approx(2 * math.log(4 / 3))
    assert record.ranking_score == pytest.approx(expected)


def test_final_sort_preserves_remote_requests_and_candidate_identities(monkeypatch):
    monkeypatch.setattr(litsearch, "CROSSREF_PAGE_SIZE", 1)
    pages = [json.dumps({"message": {"total-results": 2, "next-cursor": "next", "items": [record]}}).encode()
             for record in [item("10.1/a", "Protist alpha", 10), item("10.1/b", "Protist beta", 1)]]
    requests = None
    for sort in ["citations", "year", "relevance", "rrf", "bm25", "hybrid"]:
        http, opener = make_http(pages)
        result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
            queries=["protist"], sources=["crossref"], limit=2, sort=sort))
        assert {record.doi for record in result.records} == {"10.1/a", "10.1/b"}
        if requests is None:
            requests = opener.urls
        assert opener.urls == requests
