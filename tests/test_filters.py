"""相关性过滤与排序。"""

from __future__ import annotations

import litsearch
from conftest import make_record


def test_split_groups():
    assert litsearch._split_groups("a,b;c,d") == [["a", "b"], ["c", "d"]]
    assert litsearch._split_groups("a") == [["a"]]
    assert litsearch._split_groups("") == []
    assert litsearch._split_groups(" A , B ; ; C ") == [["a", "b"], ["c"]]


def test_split_or():
    assert litsearch._split_or("a, b ,c") == ["a", "b", "c"]
    assert litsearch._split_or("") == []


def test_require_is_or_within_group_and_and_across_groups():
    groups = litsearch._split_groups("amplicon,asv;protist,bacteria")
    assert litsearch.matches("Marine amplicon protist study", groups, []) is True
    assert litsearch.matches("Marine amplicon only", groups, []) is False
    assert litsearch.matches("protist only", groups, []) is False


def test_require_is_case_insensitive():
    groups = [["protist"]]
    assert litsearch.matches("MARINE PROTIST", groups, []) is True


def test_exclude_beats_require():
    groups = [["protist"]]
    assert litsearch.matches("protist review", groups, ["review"]) is False


def test_empty_filters_match_everything():
    assert litsearch.matches("anything", [], []) is True


def test_apply_filters_uses_title_and_abstract():
    records = [
        make_record(title="Protist grazing", abstract="marine bacteria"),
        make_record(title="Viral shunt", abstract="coastal waters"),
    ]
    groups = litsearch._split_groups("protist;bacteria")
    out = litsearch.apply_filters(records, groups, [])
    assert [r.title for r in out] == ["Protist grazing"]


def test_apply_filters_returns_copy_when_no_filters():
    records = [make_record(title="A")]
    out = litsearch.apply_filters(records, [], [])
    assert out == records and out is not records


def test_sort_by_citations():
    low = make_record(title="low", citation_count=1)
    high = make_record(title="high", citation_count=9)
    assert [r.title for r in litsearch.sort_records([low, high], "citations")] == ["high", "low"]


def test_sort_by_year():
    old = make_record(title="old", year=2001)
    new = make_record(title="new", year=2020)
    assert [r.title for r in litsearch.sort_records([old, new], "year")] == ["new", "old"]


def test_sort_relevance_puts_unscored_records_last():
    scored = make_record(title="scored", relevance_score=1.0)
    unscored = make_record(title="unscored", relevance_score=None, citation_count=999)
    out = litsearch.sort_records([unscored, scored], "relevance")
    assert [r.title for r in out] == ["scored", "unscored"]


def test_sort_relevance_orders_unscored_among_themselves_by_citations():
    a = make_record(title="a", relevance_score=None, citation_count=1)
    b = make_record(title="b", relevance_score=None, citation_count=7)
    out = litsearch.sort_records([a, b], "relevance")
    assert [r.title for r in out] == ["b", "a"]


def test_sort_handles_none_values_without_crashing():
    a = make_record(title="a", citation_count=None, year=None)
    b = make_record(title="b", citation_count=3, year=2020)
    assert [r.title for r in litsearch.sort_records([a, b], "citations")] == ["b", "a"]
    assert [r.title for r in litsearch.sort_records([a, b], "year")] == ["b", "a"]


def test_format_authors():
    assert litsearch.format_authors([]) == ""
    assert litsearch.format_authors(["A"]) == "A"
    assert litsearch.format_authors(["A", "B", "C"]) == "A; B; C"
    assert litsearch.format_authors(["A", "B", "C", "D"]) == "A et al."
