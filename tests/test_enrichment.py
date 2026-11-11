"""摘要补齐经过完整检索 Interface，所有响应来自离线 opener。"""

import json
import io
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import litsearch
from conftest import make_http, make_plan


def crossref(items):
    return json.dumps({"message": {"total-results": len(items), "items": items}}).encode()


def epmc(items):
    return json.dumps({"hitCount": len(items), "resultList": {"result": items}}).encode()


def test_abstract_is_filled_before_filtering_without_retrieval_vote():
    http, opener = make_http([
        crossref([{"DOI": "10.1/a", "title": ["Seasonal protist communities"]}]),
        epmc([{"doi": "10.1/a", "abstractText": "<p>Sampling in Daya Bay.</p>"}]),
    ])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=["crossref"], require=[["daya bay"]], enrich_abstracts=1))
    assert [r.doi for r in result.records] == ["10.1/a"]
    record = result.records[0]
    assert record.abstract == "Sampling in Daya Bay."
    assert record.abstract_source == "europepmc"
    assert record.sources == ["crossref"]
    assert result.to_dict()["enrichment"]["filled"] == 1
    params = parse_qs(urlparse(opener.urls[1]).query)
    assert params["query"] == ['DOI:"10.1/a"']
    assert params["resultType"] == ["core"]


def test_cache_budget_and_existing_abstract_are_respected():
    items = [{"DOI": "10.1/a", "title": ["First"]},
             {"DOI": "10.1/b", "title": ["Second"]},
             {"DOI": "10.1/c", "title": ["Third"], "abstract": "Existing"}]
    http, opener = make_http([
        crossref(items), epmc([{"doi": "10.1/a", "abstractText": "Recovered"}]),
        crossref(items), epmc([]),
    ])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], enrich_abstracts=1, merge_policy="doi")
    first = client.search(plan)
    assert first.report.enrichment.budget_skipped == 1
    assert first.records[-1].abstract == "Existing"
    second = client.search(plan)
    assert second.report.enrichment.cache_hits == 1
    assert second.report.enrichment.requested == 1
    assert second.report.enrichment.not_found == 1
    assert opener.remaining == []


def test_unrelated_doi_is_not_used_for_abstract_backfill():
    http, _ = make_http([
        crossref([{"DOI": "10.1/a"}]),
        epmc([{"doi": "10.1/other", "abstractText": "Wrong article"}]),
    ])
    result = litsearch.SearchClient(litsearch.Config(), http).search(
        make_plan(sources=["crossref"], enrich_abstracts=1))
    assert result.records[0].abstract is None
    assert result.report.enrichment.not_found == 1


def test_enrichment_network_failure_is_not_an_empty_success():
    http, _ = make_http([crossref([{"DOI": "10.1/a"}]), 400])
    with pytest.raises(litsearch.NetworkError):
        litsearch.SearchClient(litsearch.Config(), http).search(
            make_plan(sources=["crossref"], enrich_abstracts=1))


def test_enrichment_malformed_doi_is_a_network_error():
    http, _ = make_http([
        crossref([{"DOI": "10.1/a"}]),
        epmc([{"doi": 123, "abstractText": "Invalid identity"}]),
    ])
    with pytest.raises(litsearch.NetworkError, match="doi"):
        litsearch.SearchClient(litsearch.Config(), http).search(
            make_plan(sources=["crossref"], enrich_abstracts=1))


def test_requested_counts_new_dois_below_budget_and_caches_found_and_missing():
    records = [{"DOI": "10.1/a", "title": ["First missing abstract"]},
               {"DOI": "10.1/b", "title": ["Second missing abstract"]},
               {"DOI": "10.1/c", "title": ["Existing abstract"], "abstract": "Existing"},
               {"title": ["Record without a DOI"]}]
    http, opener = make_http([
        crossref(records), epmc([{"doi": "10.1/a", "abstractText": "Recovered"}]),
        crossref(records),
    ])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], enrich_abstracts=10, merge_policy="doi")
    assert client.search(plan).to_dict()["enrichment"] == {
        "requested": 2, "cache_hits": 0, "filled": 1, "not_found": 1, "budget_skipped": 0,
    }
    assert client.search(plan).to_dict()["enrichment"] == {
        "requested": 0, "cache_hits": 2, "filled": 1, "not_found": 1, "budget_skipped": 0,
    }
    assert len(opener.urls) == 3
    assert opener.remaining == []


def test_enrichment_retry_does_not_consume_another_doi_budget():
    http, opener = make_http([
        crossref([{"DOI": "10.1/a", "title": ["Retry abstract"]}]),
        429, epmc([{"doi": "10.1/a", "abstractText": "Recovered"}]),
    ])
    waits = []
    client = litsearch.SearchClient(litsearch.Config(), http, retry=litsearch.RetryPolicy(sleep=waits.append))
    result = client.search(make_plan(
        sources=["crossref"], enrich_abstracts=1))
    assert result.report.enrichment.requested == 1
    assert result.report.enrichment.filled == 1
    assert len(opener.urls) == 3
    assert len(waits) == 1


def test_bounded_batches_keep_doi_budget_cache_counts_and_retrieval_votes():
    records = [{"DOI": f"10.1/{index}", "title": [f"Missing abstract {index}"]}
               for index in range(12)]
    http, opener = make_http([
        crossref(records), epmc([{"doi": "10.1/0", "abstractText": "First"}]),
        epmc([{"doi": "10.1/10", "abstractText": "Second"}]),
        crossref(records), epmc([{"doi": "10.1/11", "abstractText": "Third"}]),
        crossref(records),
    ])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], enrich_abstracts=11, merge_policy="doi")
    first = client.search(plan)
    assert first.to_dict()["enrichment"] == {
        "requested": 11, "cache_hits": 0, "filled": 2, "not_found": 9, "budget_skipped": 1,
    }
    first_query = parse_qs(urlparse(opener.urls[1]).query)["query"][0]
    assert first_query == " OR ".join(f'DOI:"10.1/{index}"' for index in range(10))
    assert parse_qs(urlparse(opener.urls[2]).query)["query"] == ['DOI:"10.1/10"']
    assert client.search(plan).to_dict()["enrichment"] == {
        "requested": 1, "cache_hits": 11, "filled": 3, "not_found": 9, "budget_skipped": 0,
    }
    assert parse_qs(urlparse(opener.urls[4]).query)["query"] == ['DOI:"10.1/11"']
    plan.enrich_abstracts = 0
    unfilled = client.search(plan)
    assert unfilled.report.enrichment == litsearch.EnrichmentStats()
    assert all(record.abstract is None for record in unfilled.records)
    for record in first.records:
        assert record.sources == ["crossref"]
        assert len(record.retrieval_hits) == 1
        assert record.retrieval_hits[0]["source"] == "crossref"
    assert opener.remaining == []


def test_batch_reads_all_pages_and_only_fills_matching_empty_dois():
    records = [{"DOI": "10.1/a", "title": ["First"]}, {"DOI": "10.1/b", "title": ["Second"]},
               {"DOI": "10.1/c", "title": ["Existing"], "abstract": "Original"}]
    first_page = {"hitCount": 4, "nextCursorMark": "cursor+next", "resultList": {"result": [
        {"doi": "https://doi.org/10.1/A", "abstractText": None},
        {"doi": "10.1/c", "abstractText": "Do not replace"},
    ]}}
    last_page = {"hitCount": 4, "resultList": {"result": [
        {"doi": "10.1/b", "abstractText": "<p>Recovered B</p>"},
        {"doi": "10.1/a", "abstractText": "Recovered A"},
    ]}}
    http, opener = make_http([crossref(records), json.dumps(first_page).encode(),
                              json.dumps(last_page).encode(), crossref(records)])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], enrich_abstracts=2)
    first = client.search(plan)
    assert {record.doi: record.abstract for record in first.records} == {
        "10.1/a": "Recovered A", "10.1/b": "Recovered B", "10.1/c": "Original",
    }
    assert all(record.abstract_source == "europepmc" for record in first.records[:2])
    assert parse_qs(urlparse(opener.urls[1]).query)["cursorMark"] == ["*"]
    assert parse_qs(urlparse(opener.urls[2]).query)["cursorMark"] == ["cursor+next"]
    assert client.search(plan).report.enrichment.cache_hits == 2
    assert opener.remaining == []


@pytest.mark.parametrize("cursor", [None, "", "*"])
def test_incomplete_batch_response_fails_without_caching_missing_abstracts(cursor):
    records = [{"DOI": "10.1/a", "title": ["First"]}, {"DOI": "10.1/b", "title": ["Second"]}]
    incomplete = {"hitCount": 2, "nextCursorMark": cursor, "resultList": {"result": [
        {"doi": "10.1/a", "abstractText": "Partial result"},
    ]}}
    complete = [{"doi": "10.1/a", "abstractText": "Complete A"},
                {"doi": "10.1/b", "abstractText": "Complete B"}]
    http, opener = make_http([crossref(records), json.dumps(incomplete).encode(),
                              crossref(records), epmc(complete)])
    client = litsearch.SearchClient(litsearch.Config(), http)
    plan = make_plan(sources=["crossref"], enrich_abstracts=2)
    with pytest.raises(litsearch.NetworkError, match="nextCursorMark"):
        client.search(plan)
    result = client.search(plan)
    assert result.report.enrichment.requested == 2
    assert result.report.enrichment.cache_hits == 0
    assert result.report.enrichment.filled == 2
    assert opener.remaining == []


@pytest.mark.parametrize("item", [
    {"doi": 123, "abstractText": "Invalid DOI"},
    {"doi": "10.1/a", "abstractText": ["Invalid abstract"]},
])
def test_batch_invalid_metadata_remains_a_network_error(item):
    records = [{"DOI": "10.1/a", "title": ["First"]}, {"DOI": "10.1/b", "title": ["Second"]}]
    http, _ = make_http([crossref(records), epmc([item])])
    with pytest.raises(litsearch.NetworkError):
        litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
            sources=["crossref"], enrich_abstracts=2))


def test_frozen_real_metadata_restores_core_records_before_hard_filtering():
    directory = Path(__file__).parent / "fixtures"
    snapshot = json.loads((directory / "retrieval_replay.json").read_text(encoding="utf-8"))
    labels = json.loads((directory / "retrieval_gold.json").read_text(encoding="utf-8"))
    core = {r["doi"] for r in labels["records"] if r["tier"] == 1}

    def opener(request, timeout):
        if urlparse(request.full_url).hostname == "api.openalex.org":
            return io.BytesIO(json.dumps(snapshot["response"]).encode())
        query = parse_qs(urlparse(request.full_url).query)["query"][0]
        dois = [term.removeprefix('DOI:"').removesuffix('"') for term in query.split(" OR ")]
        items = [{"doi": doi, "abstractText": snapshot["abstracts"][doi]}
                 for doi in dois if snapshot["abstracts"].get(doi)]
        return io.BytesIO(epmc(items))

    client = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}),
                                  litsearch.Http(opener=opener, polite=0))
    plan = make_plan(limit=100, require=snapshot["require"], merge_policy="doi")
    before = client.search(plan)
    from dataclasses import replace
    after = client.search(replace(plan, enrich_abstracts=100))
    assert len(core & {r.doi for r in before.records}) == 4
    assert len(core & {r.doi for r in after.records}) == 7
