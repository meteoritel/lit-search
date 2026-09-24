"""Record schema、摘要重建、归一化助手。"""

from __future__ import annotations

import pytest

import litsearch


def test_reconstruct_abstract_orders_by_position():
    inv = {"grazing": [1], "Marine": [0], "bacteria": [2]}
    assert litsearch.reconstruct_abstract(inv) == "Marine grazing bacteria"


def test_reconstruct_abstract_empty_inputs():
    assert litsearch.reconstruct_abstract(None) is None
    assert litsearch.reconstruct_abstract({}) is None


def test_strip_jats_removes_tags_and_unescapes():
    raw = "<jats:p>Marine protist &amp; bacteria</jats:p>"
    assert litsearch.strip_jats(raw) == "Marine protist & bacteria"


def test_strip_jats_collapses_whitespace():
    raw = "<jats:title>Abstract</jats:title><jats:p>  two\n  lines </jats:p>"
    assert litsearch.strip_jats(raw) == "Abstract two lines"


def test_strip_jats_empty_inputs():
    assert litsearch.strip_jats(None) is None
    assert litsearch.strip_jats("") is None
    assert litsearch.strip_jats("<jats:p></jats:p>") is None


def test_norm_title_drops_punctuation_and_case():
    assert litsearch.norm_title("MARINE protist-grazing!") == "marineprotistgrazing"


def test_norm_title_none_when_nothing_remains():
    assert litsearch.norm_title("---") is None
    assert litsearch.norm_title(None) is None
    assert litsearch.norm_title("") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.1000/ABC", "10.1000/abc"),
        ("https://doi.org/10.1000/ABC", "10.1000/abc"),
        ("http://doi.org/10.1000/ABC", "10.1000/abc"),
        ("https://dx.doi.org/10.1000/ABC", "10.1000/abc"),
        ("doi:10.1000/ABC", "10.1000/abc"),
        ("  10.1000/ABC  ", "10.1000/abc"),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_norm_doi_variants(raw, expected):
    assert litsearch.norm_doi(raw) == expected


def test_record_defaults_are_none_not_empty():
    """缺字段必须是 None/[]，否则 --min-citations 0 与合并行为会跨源不一致。"""
    rec = litsearch.Record()
    assert rec.doi is None
    assert rec.citation_count is None
    assert rec.is_open_access is None
    assert rec.relevance_score is None
    assert rec.authors == []
    assert rec.sources == []
    assert rec.abstract is None


def test_record_fields_constant_matches_dataclass():
    from dataclasses import fields

    assert set(litsearch.RECORD_FIELDS) == {f.name for f in fields(litsearch.Record)}
