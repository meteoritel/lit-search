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


# --------------------------------------------------------------------------- #
# 匹配语义：归一化 + 词首前缀 + '=词' / '*词*' 修饰
# --------------------------------------------------------------------------- #

def test_punctuation_is_normalized_so_phrases_survive_hyphens():
    """标题写作 'Nucleic Acid-Content'，检索词写作 'nucleic acid content'。"""
    text = "High and Low Nucleic Acid-Content Bacteria in Tibetan Ice Cores"
    assert litsearch.matches(text, [["nucleic acid content"]], []) is True
    assert litsearch.matches(text, [["nucleic acid"]], []) is True


def test_word_initial_prefix_rejects_mid_word_hits():
    """'sea' 不再命中 research / disease / increase 这类词中出现的 sea。"""
    assert litsearch.matches("Research on disease increase", [["sea"]], []) is False
    assert litsearch.matches("Sea surface temperature", [["sea"]], []) is True
    assert litsearch.matches("Seawater bacterioplankton", [["sea"]], []) is True


def test_default_prefix_still_matches_inflected_forms():
    assert litsearch.matches("Protists grazing on bacteria", [["protist"]], []) is True
    assert litsearch.matches("Eukaryotic picoplankton", [["eukaryot"]], []) is True


def test_equals_prefix_requires_a_whole_word():
    groups = [["=sea"]]
    assert litsearch.matches("Sea surface temperature", groups, []) is True
    assert litsearch.matches("Seawater bacterioplankton", groups, []) is False
    assert litsearch.matches("Seasonality of the study area", groups, []) is False


def test_star_wrapped_term_restores_substring_matching():
    """复合词（cyanobacteria）默认不命中，须显式写 '*bacteria*'。"""
    assert litsearch.matches("Cyanobacteria blooms", [["bacteria"]], []) is False
    assert litsearch.matches("Cyanobacteria blooms", [["*bacteria*"]], []) is True


def test_exclude_uses_the_same_matching_rules():
    assert litsearch.matches("A review of protists", [["protist"]], ["review"]) is False
    assert litsearch.matches("Previewing protists", [["protist"]], ["review"]) is True


def test_chinese_terms_fall_back_to_substring():
    assert litsearch.matches("海洋细菌的核酸含量", [["海洋"]], []) is True
    assert litsearch.matches("海洋细菌的核酸含量", [["核酸"]], []) is True
    assert litsearch.matches("淡水湖泊的细菌", [["海洋"]], []) is False


def test_apply_filters_uses_title_and_abstract():
    records = [
        make_record(title="Protist grazing", abstract="marine bacteria"),
        make_record(title="Viral shunt", abstract="coastal waters"),
    ]
    groups = litsearch._split_groups("protist;bacteria")
    out, _ = litsearch.apply_filters(records, groups, [])
    assert [r.title for r in out] == ["Protist grazing"]


def test_apply_filters_returns_copy_when_no_filters():
    records = [make_record(title="A")]
    out, stats = litsearch.apply_filters(records, [], [])
    assert out == records and out is not records
    assert stats.before == stats.after == 1


def test_apply_filters_reports_per_group_counts_on_the_same_input():
    """每个组都在同一输入集合上单独计数，用来判断是哪一组把结果卡掉的。"""
    records = [
        make_record(title="protist bacteria"),
        make_record(title="protist only"),
        make_record(title="bacteria only"),
        make_record(title="neither"),
    ]
    groups = litsearch._split_groups("protist;bacteria")
    out, stats = litsearch.apply_filters(records, groups, [])
    assert [r.title for r in out] == ["protist bacteria"]
    assert stats.before == 4
    assert stats.per_group == [2, 2]     # 各组单独能留 2 条
    assert stats.after == 1              # 取交集后只剩 1 条
    assert stats.passed_exclude == 4


def test_apply_filters_reports_exclude_removals():
    records = [
        make_record(title="protist grazing"),
        make_record(title="protist review"),
    ]
    out, stats = litsearch.apply_filters(records, [["protist"]], ["review"])
    assert [r.title for r in out] == ["protist grazing"]
    assert stats.excluded == 1
    assert stats.passed_exclude == 1
    assert stats.per_group == [1]        # 排除词先刷掉的，不重复计入组内
    assert stats.after == 1


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
