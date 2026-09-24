"""两级去重：DOI 跨源合并 + 归一化标题版本合并。"""

from __future__ import annotations

import litsearch
from conftest import make_record


def test_doi_merge_across_sources_prefers_higher_citations():
    oa = make_record(doi="10.1000/aaa", title="Marine protist grazing",
                     citation_count=42, abstract="openalex abstract",
                     source="openalex", sources=["openalex"])
    cr = make_record(doi="https://doi.org/10.1000/AAA",
                     title="MARINE PROTIST GRAZING ON BACTERIA.",
                     citation_count=39, source="crossref", sources=["crossref"])
    merged, stats = litsearch.merge_records([oa, cr])
    assert len(merged) == 1
    assert stats.doi_merged == 1
    assert stats.title_merged == 0
    assert merged[0].citation_count == 42
    assert merged[0].source == "openalex"
    assert merged[0].sources == ["crossref", "openalex"]
    assert merged[0].abstract == "openalex abstract"


def test_fill_missing_backfills_from_the_loser():
    base = make_record(doi="10.1000/x", title="T", citation_count=50,
                       abstract=None, venue=None, authors=[])
    other = make_record(doi="10.1000/x", title="T", citation_count=1,
                        abstract="found elsewhere", venue="Journal",
                        authors=["Someone"])
    merged, _ = litsearch.merge_records([base, other])
    assert merged[0].abstract == "found elsewhere"
    assert merged[0].venue == "Journal"
    assert merged[0].authors == ["Someone"]


def test_openalex_wins_tie_on_citations():
    cr = make_record(doi="10.1000/x", title="T", citation_count=5,
                     venue="from-crossref", source="crossref", sources=["crossref"])
    oa = make_record(doi="10.1000/x", title="T", citation_count=5,
                     venue=None, source="openalex", sources=["openalex"])
    merged, _ = litsearch.merge_records([cr, oa])
    assert merged[0].source == "openalex"
    assert merged[0].venue == "from-crossref"


def test_title_merge_collapses_preprint_and_published():
    pre = make_record(doi="10.1000/pre", title="Deep sea protists of the abyss", citation_count=1)
    pub = make_record(doi="10.2000/pub", title="Deep-sea protists of the abyss!", citation_count=9)
    merged, stats = litsearch.merge_records([pre, pub])
    assert len(merged) == 1
    assert stats.doi_merged == 0
    assert stats.title_merged == 1
    assert merged[0].doi == "10.2000/pub"


def test_transitive_chain_reaches_a_fixed_point():
    a = make_record(doi="10.1/a", title="Alpha protist grazing study")
    b = make_record(doi="10.1/b", title="Alpha protist grazing study")
    c = make_record(doi="10.1/b", title="Beta bacterioplankton survey")
    merged, _ = litsearch.merge_records([a, b, c])
    assert len(merged) == 1


def test_record_without_doi_survives_by_title():
    merged, stats = litsearch.merge_records([make_record(doi=None, title="Only a title")])
    assert len(merged) == 1
    assert stats.no_doi == 1
    assert stats.dropped_no_id == 0


def test_dropped_when_neither_doi_nor_title():
    records = [make_record(doi=None, title=None),
               make_record(doi="10.1/x", title="Kept")]
    merged, stats = litsearch.merge_records(records)
    assert len(merged) == 1
    assert stats.dropped_no_id == 1
    assert stats.total_in == 2
    assert stats.total_out == 1


def test_distinct_records_are_not_merged():
    records = [make_record(doi="10.1/a", title="Alpha protist grazing study"),
               make_record(doi="10.1/b", title="Beta bacterioplankton survey")]
    merged, stats = litsearch.merge_records(records)
    assert len(merged) == 2
    assert stats.doi_merged == 0
    assert stats.title_merged == 0


def test_merge_is_idempotent():
    records = [make_record(doi="10.1/a", title="Alpha protist grazing study"),
               make_record(doi="10.1/a", title="Alpha protist grazing study"),
               make_record(doi=None, title="Beta bacterioplankton survey")]
    once, _ = litsearch.merge_records(records)
    twice, _ = litsearch.merge_records(once)
    assert len(once) == len(twice) == 2


def test_generic_short_titles_are_not_title_merged():
    """'Editorial' / 'Correction' 这类通用短标题不能跨论文合并，否则静默丢数据。"""
    editorial_a = make_record(doi="10.1/a", title="Editorial")
    editorial_b = make_record(doi="10.2/b", title="Editorial")
    merged, stats = litsearch.merge_records([editorial_a, editorial_b])
    assert len(merged) == 2
    assert stats.title_merged == 0


def test_title_merge_still_works_at_the_length_threshold():
    """归一化后正好等于阈值（15）的真实短标题仍要能合并。"""
    assert len(litsearch.norm_title("Deep sea protists")) == 15
    a = make_record(doi="10.1/a", title="Deep sea protists", citation_count=1)
    b = make_record(doi="10.2/b", title="Deep-sea protists!", citation_count=9)
    merged, stats = litsearch.merge_records([a, b])
    assert len(merged) == 1
    assert stats.title_merged == 1
