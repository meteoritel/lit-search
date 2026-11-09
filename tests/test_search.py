"""Python 检索 Interface 与 CLI 的共用结果契约。"""

from __future__ import annotations

import json

import pytest

import litsearch
from conftest import load_fixture, make_http, make_plan


def make_client(responses, **config):
    http, opener = make_http(responses)
    client = litsearch.SearchClient(
        litsearch.Config(api_keys={"openalex": "test-key"}, **config), http,
        retry=litsearch.RetryPolicy(sleep=lambda _: None),
    )
    return client, opener


def test_search_returns_complete_pipeline_without_terminal_or_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    client, _ = make_client([load_fixture("openalex_page1.json")])
    plan = make_plan(limit=2, require=[["grazing"]], max_results=1)
    result = client.search(plan)
    assert [r.doi for r in result.records] == ["10.1000/aaa"]
    assert result.report.stats.total_in == 2
    assert result.report.filters.after == 1
    assert result.report.plan is not plan
    assert result.report.plan.require is not plan.require
    assert capsys.readouterr().out == ""
    assert capsys.readouterr().err == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("options", [
    {"queries": "q"}, {"limit": True}, {"sort": "bad"},
    {"oa_only": "false"}, {"require": ["marine"]}, {"sources": ["bad"]},
    {"strict_filters": "true"}, {"merge_policy": "bad"},
    {"enrich_abstracts": None}, {"enrich_abstracts": -1},
])
def test_python_inputs_are_validated_before_network(options):
    client, opener = make_client([])
    with pytest.raises(litsearch.UsageError):
        client.search(make_plan(**options))
    assert opener.urls == []


def test_json_contains_source_diagnostics_and_separate_query_identity():
    client, _ = make_client([load_fixture("crossref_page1.json")])
    result = client.search(make_plan(
        sources=["crossref"], queries=["marine · bacteria"], limit=3,
        institution="Example University", oa_only=True,
    ))
    payload = result.to_dict()
    assert payload == json.loads(litsearch.render_json(result.report, result.records))
    assert payload["schema_version"] == 1
    assert payload["queries"][0]["source"] == "crossref"
    assert payload["queries"][0]["query"] == "marine · bacteria"
    assert payload["queries"][0]["fetched_count"] == 3
    assert payload["queries"][0]["mapped_count"] == 2
    assert payload["queries"][0]["returned_count"] == 2
    assert payload["queries"][0]["filter_execution"]["oa_only"] == "unsupported"
    unsupported = [d for d in payload["diagnostics"] if d["code"] == "unsupported_filter"]
    assert {d["field"] for d in unsupported} == {"oa_only", "institution"}


def test_strict_filters_check_every_source_before_any_request():
    client, opener = make_client([])
    with pytest.raises(litsearch.UsageError, match="严格"):
        client.search(make_plan(sources=["openalex", "crossref"], oa_only=True,
                                strict_filters=True))
    assert opener.urls == []


def test_explain_is_offline_and_redacts_credentials():
    client, opener = make_client([])
    payload = client.explain(make_plan())
    assert "test-key" not in json.dumps(payload)
    assert payload["sources"][0]["key_configured"] is True
    assert opener.urls == []


def test_source_local_filter_counts_are_not_the_scanned_count():
    client, _ = make_client([load_fixture("crossref_page1.json")])
    result = client.search(make_plan(sources=["crossref"], min_citations=10, limit=3))
    execution = result.report.queries[0]
    assert execution.fetched_count == 3
    assert execution.mapped_count == 2
    assert execution.returned_count == 1


def test_cli_and_python_share_search_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENALEX_API_KEY", "test-key")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("LITSEARCH_CONFIG", raising=False)
    body = load_fixture("openalex_page1.json")
    client, _ = make_client([body])
    expected = client.search(make_plan(limit=2)).to_dict()
    http, _ = make_http([body])
    args = litsearch.build_parser().parse_args(["q", "--limit", "2", "--stdout"])
    assert litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_cli_explain_works_without_key_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("LITSEARCH_CONFIG", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    http, opener = make_http([])
    args = litsearch.build_parser().parse_args(["q", "--explain"])
    assert litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None) == 0
    assert json.loads(capsys.readouterr().out)["plan"]["queries"] == ["q"]
    assert opener.urls == []
    assert list(tmp_path.iterdir()) == []
