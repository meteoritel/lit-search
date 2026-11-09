"""文本清洗、结果诊断和概念规范化经过完整检索入口验证。"""

import json

import pytest

import litsearch
from conftest import make_http, make_plan


def crossref(items, total=None):
    return json.dumps({"message": {"total-results": len(items) if total is None else total,
                                   "items": items}}).encode()


@pytest.mark.parametrize("source", ["crossref", "europepmc"])
def test_plain_text_flows_through_filters_ranking_and_rendering(source):
    title = "&lt;i&gt;Marine&lt;/i&gt; bacteria α &amp; β"
    abstract = "<jats:p>p < 0.05 and size > 2</jats:p><p>CO<sub>2</sub> 海洋</p>"
    if source == "crossref":
        body = crossref([{"DOI": "10.1/a", "title": [title], "abstract": abstract}])
    else:
        body = json.dumps({"hitCount": 1, "resultList": {"result": [{
            "id": "1", "source": "MED", "doi": "10.1/a",
            "title": title, "abstractText": abstract,
        }]}}).encode()
    http, _ = make_http([body])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(
        sources=[source], require=[["marine bacteria"], ["CO2"]], exclude=["freshwater"], sort="hybrid"))
    record = result.records[0]
    assert record.title == "Marine bacteria α & β"
    assert record.abstract == "p < 0.05 and size > 2 CO2 海洋"
    assert record.ranking_score is not None
    for rendered in [litsearch.render_json(result.report, result.records),
                     litsearch.render_csv(result.records),
                     litsearch.render_markdown(result.report, result.records)]:
        assert record.title in rendered
        assert "&lt;" not in rendered


def test_cleaned_title_merge_preserves_identities_and_doi_policy():
    body = crossref([
        {"DOI": "10.1/a", "title": ["&lt;i&gt;Marine bacteria communities&lt;/i&gt;"]},
        {"DOI": "10.1/b", "title": ["Marine bacteria communities"]},
    ])
    for policy, count in [("title", 1), ("doi", 2)]:
        http, _ = make_http([body])
        result = litsearch.SearchClient(litsearch.Config(), http).search(
            make_plan(sources=["crossref"], merge_policy=policy))
        assert len(result.records) == count
        if policy == "title":
            assert result.records[0].source_ids == {"crossref": ["10.1/a", "10.1/b"]}
            assert result.records[0].merge_reasons == ["title"]


@pytest.mark.parametrize("text,expected", [
    (None, None), ("", None), ("<p></p>", None),
    ("a &lt; b and c &gt; d", "a < b and c > d"),
    ("<italic>x</italic><sup>2</sup> &amp; y", "x2 & y"),
    ("<h4>Importance</h4><ul><li>Marine</li><li>bacteria</li></ul>", "Importance Marine bacteria"),
    ("&amp;lt;i&amp;gt;x&amp;lt;/i&amp;gt;", "<i>x</i>"),
])
def test_text_parser_has_bounded_decoding_and_preserves_comparisons(text, expected):
    assert litsearch.strip_jats(text) == expected


@pytest.mark.parametrize("items,total,options,reason", [
    ([], 0, {}, "远端无命中"),
    ([{}], 1, {}, "映射未产生有效记录"),
    ([{"DOI": "10.1/a", "language": "fr"}], 1, {"language": "en"}, "来源本地条件筛空"),
    ([{"DOI": "10.1/a", "title": ["Freshwater"]}], 1, {"require": [["marine"]]}, "主题过滤后为空"),
])
def test_empty_results_explain_the_pipeline_stage(items, total, options, reason):
    http, _ = make_http([crossref(items, total)])
    result = litsearch.SearchClient(litsearch.Config(), http).search(
        make_plan(sources=["crossref"], **options))
    assert result.records == []
    diagnostics = [d for d in result.to_dict()["diagnostics"] if d["code"] == "empty_results"]
    assert reason in diagnostics[0]["message"]
    assert result.report.filters.before == (1 if options.get("require") else 0)


def test_duplicate_concepts_use_one_normalized_plan_without_mutating_input():
    concepts = [[" marine ", "ocean", "marine"], ["marine", "ocean"]]
    plan = make_plan(queries=[], sources=["crossref"], concepts=concepts, sort="hybrid")
    http, opener = make_http([crossref([{"DOI": "10.1/a", "title": ["Marine bacteria"]}])]*2)
    client = litsearch.SearchClient(litsearch.Config(), http)
    result = client.search(plan)
    assert result.report.plan.concepts == [["marine", "ocean"]]
    assert result.to_dict()["filters"]["concepts"] == [["marine", "ocean"]]
    assert client.explain(plan)["plan"]["concepts"] == [["marine", "ocean"]]
    assert len(opener.urls) == 2
    assert len(result.records[0].retrieval_hits) == 2
    assert concepts == [[" marine ", "ocean", "marine"], ["marine", "ocean"]]
    reversed_groups = litsearch.validate_plan(make_plan(concepts=[["a", "b"], ["b", "a"]]))
    assert reversed_groups.concepts == [["a", "b"], ["b", "a"]]


def test_result_convenience_properties_follow_records_and_are_read_only():
    http, _ = make_http([crossref([{"DOI": "10.1/a"}])])
    result = litsearch.SearchClient(litsearch.Config(), http).search(make_plan(sources=["crossref"]))
    assert result.count == result.to_dict()["count"] == 1
    assert result.version == result.to_dict()["version"] == litsearch.__version__
    result.records.clear()
    assert result.count == 0
    with pytest.raises(AttributeError):
        result.count = 42
    with pytest.raises(AttributeError):
        result.version = "other"
