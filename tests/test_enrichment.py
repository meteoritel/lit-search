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


def test_frozen_real_metadata_restores_core_records_before_hard_filtering():
    directory = Path(__file__).parent / "fixtures"
    snapshot = json.loads((directory / "retrieval_replay.json").read_text(encoding="utf-8"))
    labels = json.loads((directory / "retrieval_gold.json").read_text(encoding="utf-8"))
    core = {r["doi"] for r in labels["records"] if r["tier"] == 1}

    def opener(request, timeout):
        if urlparse(request.full_url).hostname == "api.openalex.org":
            return io.BytesIO(json.dumps(snapshot["response"]).encode())
        query = parse_qs(urlparse(request.full_url).query)["query"][0]
        doi = query.removeprefix('DOI:"').removesuffix('"')
        abstract = snapshot["abstracts"].get(doi)
        items = [{"doi": doi, "abstractText": abstract}] if abstract else []
        return io.BytesIO(epmc(items))

    client = litsearch.SearchClient(litsearch.Config(api_keys={"openalex": "key"}),
                                  litsearch.Http(opener=opener, polite=0))
    plan = make_plan(limit=100, require=snapshot["require"], merge_policy="doi")
    before = client.search(plan)
    from dataclasses import replace
    after = client.search(replace(plan, enrich_abstracts=100))
    assert len(core & {r.doi for r in before.records}) == 4
    assert len(core & {r.doi for r in after.records}) == 7
