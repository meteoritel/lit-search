"""Europe PMC 的字段映射、分页、来源限制与主题查询。"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

import litsearch
from conftest import make_http, make_plan


def item(identifier, **fields):
    return {"id": str(identifier), "source": "MED", "doi": f"10.1/{identifier}",
            "title": "Marine protist communities", "pubYear": "2024",
            "abstractText": "Seasonal sampling in Daya Bay.", "language": "eng", **fields}


def page(items, total=None, cursor=None):
    return json.dumps({"hitCount": len(items) if total is None else total,
                       "resultList": {"result": items}, "nextCursorMark": cursor}).encode()


def test_mapping_and_encoded_cursor_through_full_search():
    http, opener = make_http([
        page([item(i) for i in range(100)], total=101, cursor="cursor+next"),
        page([item(100, citedByCount=5, isOpenAccess="Y", pubTypeList={"pubType": ["Journal Article"]})], total=101),
    ])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["europepmc"], limit=101, year="2021-2026", sort="rrf", merge_policy="doi"))
    second = parse_qs(urlparse(opener.urls[1]).query)
    assert second["cursorMark"] == ["cursor+next"]
    assert "PUB_YEAR:[2021 TO 2026]" in second["query"][0]
    record = next(r for r in result.records if r.doi == "10.1/100")
    assert record.source_id == "MED:100"
    assert record.retrieval_hits[0]["rank"] == 101
    assert record.abstract_source == "europepmc"
    assert record.is_open_access is True
    assert record.language == "en"
    assert record.work_type == "article"
    assert record.citation_count == 5


def test_strict_unsupported_conditions_are_rejected_before_any_source_request():
    http, opener = make_http([])
    with pytest.raises(litsearch.UsageError, match="严格"):
        litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}), http).search(
            make_plan(sources=["openalex", "europepmc"], work_type="article", strict_filters=True))
    assert opener.urls == []


def test_local_filters_use_a_bounded_scan():
    http, _ = make_http([page([item(i, citedByCount=0) for i in range(3)], total=50)])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["europepmc"], limit=1, min_citations=10))
    assert result.records == []
    assert result.report.queries[0].fetched_count == 3
    assert result.report.queries[0].stop_reason == "scan_budget"


@pytest.mark.parametrize("payload", [
    {"hitCount": "bad", "resultList": {"result": []}},
    {"hitCount": 1, "resultList": {"result": [{"doi": "10.1/no-id"}]}},
    {"hitCount": 1, "resultList": {"result": [item(1, pubYear=True)]}},
])
def test_malformed_source_responses_fail_explicitly(payload):
    http, _ = make_http([json.dumps(payload).encode()])
    with pytest.raises(litsearch.NetworkError):
        litsearch.SearchClient(litsearch.Config(), http).search(make_plan(sources=["europepmc"]))


def test_topic_queries_compile_per_source_and_explain_offline():
    http, opener = make_http([])
    plan = make_plan(queries=[], sources=["openalex", "crossref", "europepmc"], concepts=[
        ["South China Sea", "Daya Bay"], ["protist", "ciliate"], ["seasonal"],
    ])
    explanation = litsearch.SearchClient(litsearch.Config(), http).explain(plan)
    queries = explanation["compiled_queries"]
    assert any(" OR " in row["query"] for row in queries if row["source"] == "openalex")
    assert all(" OR " not in row["query"] and " AND " not in row["query"]
               for row in queries if row["source"] == "crossref")
    assert all("TITLE_ABS:" in row["query"] for row in queries if row["source"] == "europepmc")
    assert any("seasonal" not in row["query"] for row in queries)
    assert opener.urls == []


def test_concepts_are_soft_ranking_evidence():
    http, _ = make_http([page([item(1)]), page([item(1), item(2, title="Physical oceanography", abstractText=None)])])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["europepmc"], queries=[], concepts=[["Daya Bay"], ["protist"], ["seasonal"]], sort="hybrid"))
    assert len(result.records) == 2
    assert result.records[0].doi == "10.1/1"
