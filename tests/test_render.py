"""渲染器、输出路径解析。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import litsearch
from conftest import make_plan, make_record


def make_report(plan, totals=None, **stats_overrides):
    stats = litsearch.MergeStats(total_in=1, total_out=1)
    for key, value in stats_overrides.items():
        setattr(stats, key, value)
    return litsearch.Report(plan=plan, totals=totals or [("openalex · q", 1)],
                            stats=stats)


SAMPLE = make_record(
    doi="10.1000/aaa", title="Marine protist grazing", year=2023,
    venue="Journal of Marine Ecology", citation_count=42,
    authors=["Alice Chen", "Bo Liu", "Cara Diaz", "Dan Eaves"],
    is_open_access=True, source="openalex", sources=["openalex"],
)


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

def test_markdown_has_author_column_and_no_source_column_for_single_source():
    plan = make_plan(queries=["q"], sources=["openalex"], title="My List")
    md = litsearch.render_markdown(make_report(plan), [SAMPLE])
    assert "# My List" in md
    assert "| 作者 |" in md
    assert "| 来源 |" not in md
    assert "Alice Chen et al." in md
    assert "10.1000/aaa" in md


def test_markdown_adds_source_column_only_for_multiple_sources():
    plan = make_plan(queries=["q"], sources=["openalex", "crossref"])
    rec = make_record(doi="10.1000/aaa", title="T", sources=["crossref", "openalex"])
    md = litsearch.render_markdown(make_report(plan), [rec])
    assert "| 来源 |" in md
    assert "crossref/openalex" in md


def test_markdown_escapes_pipes_and_newlines_in_cells():
    plan = make_plan(queries=["q"])
    rec = make_record(title="A|B", venue="V\nW", doi="10.1/x")
    md = litsearch.render_markdown(make_report(plan), [rec])
    assert "A\\|B" in md
    assert "V W" in md


def test_markdown_reports_filters_and_merge_counts():
    plan = make_plan(queries=["q"], sources=["openalex"], year="2021-2026",
                     min_citations=5, oa_only=True,
                     require=[["amplicon", "asv"]], exclude=["review"],
                     raw_filter="is_retracted:false")
    md = litsearch.render_markdown(
        make_report(plan, doi_merged=2, title_merged=1, dropped_no_id=3),
        [SAMPLE])
    assert "年份范围：2021-2026" in md
    assert "最低被引数：5" in md
    assert "仅开放获取" in md
    assert "amplicon 或 asv" in md
    assert "review" in md
    assert "is_retracted:false" in md
    assert "按 DOI 合并掉 2 条" in md
    assert "按标题合并掉 1 条" in md
    assert "丢弃 3 条" in md


def test_markdown_default_title_falls_back_to_first_query():
    plan = make_plan(queries=["marine protist"])
    md = litsearch.render_markdown(make_report(plan), [])
    assert md.splitlines()[0] == "# marine protist"


# --------------------------------------------------------------------------- #
# CSV / JSON / DOI 列表
# --------------------------------------------------------------------------- #

def test_csv_header_and_row_count():
    text = litsearch.render_csv([SAMPLE])
    header, *rows = text.splitlines()
    assert header.startswith("doi,title,year")
    assert len(rows) == 1
    assert "10.1000/aaa" in rows[0]
    assert "Alice Chen; Bo Liu; Cara Diaz; Dan Eaves" in rows[0]


def test_csv_joins_list_fields_with_semicolons():
    rec = make_record(title="T", sources=["crossref", "openalex"],
                      authors=["A", "B"])
    text = litsearch.render_csv([rec])
    assert "crossref; openalex" in text
    assert "A; B" in text


def test_json_payload_shape():
    plan = make_plan(queries=["q"], sources=["openalex"], year="2021-2026")
    payload = json.loads(litsearch.render_json(make_report(plan), [SAMPLE]))
    assert payload["version"] == litsearch.__version__
    assert payload["count"] == 1
    assert payload["queries"][0]["label"] == "openalex · q"
    assert payload["filters"]["year"] == "2021-2026"
    assert payload["merge_stats"]["total_in"] == 1
    assert payload["results"][0]["doi"] == "10.1000/aaa"
    assert payload["results"][0]["sources"] == ["openalex"]


def test_doi_list_excludes_records_without_doi():
    records = [make_record(doi="https://doi.org/10.1000/AAA"),
               make_record(doi=None, title="No doi")]
    text, missing = litsearch.render_doi_list(records)
    assert text == "10.1000/aaa\n"
    assert missing == 1


def test_doi_list_is_empty_when_nothing_qualifies():
    text, missing = litsearch.render_doi_list([make_record(doi=None)])
    assert text == ""
    assert missing == 1


def test_doi_list_of_empty_input():
    assert litsearch.render_doi_list([]) == ("", 0)


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #

def test_query_slug_is_lowercase_and_hyphenated():
    assert litsearch.query_slug(["Marine Protist: grazing!"]) == "marine-protist-grazing"


def test_query_slug_is_truncated_and_never_empty():
    assert len(litsearch.query_slug(["x" * 200])) <= 48
    assert litsearch.query_slug([""]) == "query"
    assert litsearch.query_slug([]) == "query"


def test_query_slug_prefers_title_over_the_first_query():
    assert litsearch.query_slug(["marine protist"], "HNA/LNA 海洋细菌 2022-2026") == \
        "hna-lna-海洋细菌-2022-2026"


def test_query_slug_keeps_chinese_instead_of_degenerating_to_query():
    """中文检索词以前会变成 'query'，同一天跑两个中文课题就会互相覆盖。"""
    slug = litsearch.query_slug(["海洋细菌"])
    assert slug == "海洋细菌"
    assert slug != litsearch.query_slug(["淡水湖泊"])


def test_query_slug_truncates_on_a_word_boundary():
    slug = litsearch.query_slug(["high nucleic acid low nucleic acid marine bacteria"])
    assert len(slug) <= 48
    assert not slug.endswith("bacter")          # 不能切出半个单词
    assert "high-nucleic-acid" in slug


def test_query_slug_uses_a_hash_when_there_is_no_word_character():
    """标题全是标点时要靠哈希区分，否则不同课题会撞成同一个文件名。"""
    a = litsearch.query_slug([], "---")
    b = litsearch.query_slug([], "!!!")
    assert a.startswith("query-") and b.startswith("query-")
    assert a != b


def test_resolve_outputs_uses_configured_output_dir(tmp_path):
    args = litsearch.build_parser().parse_args(["q"])
    plan = make_plan(queries=["marine protist"])
    cfg = litsearch.Config(output_dir=tmp_path / "custom")
    main_path, doi_path = litsearch.resolve_outputs(args, cfg, plan)
    assert main_path.parent == tmp_path / "custom"
    assert main_path.name.startswith("litsearch_marine-protist_")
    assert main_path.suffix == ".md"
    assert doi_path.name == main_path.stem + ".doi.txt"


def test_resolve_outputs_defaults_to_cwd_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = litsearch.build_parser().parse_args(["q"])
    main_path, _ = litsearch.resolve_outputs(args, litsearch.Config(),
                                            make_plan(queries=["q"]))
    assert main_path.parent == tmp_path / "output"


def test_resolve_outputs_prefers_explicit_cwd_over_process_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    args = litsearch.build_parser().parse_args(["q"])
    main_path, _ = litsearch.resolve_outputs(args, litsearch.Config(),
                                            make_plan(queries=["q"]), elsewhere)
    assert main_path.parent == elsewhere / "output"


def test_resolve_outputs_honours_explicit_paths(tmp_path):
    args = litsearch.build_parser().parse_args(
        ["q", "--output", str(tmp_path / "a.csv"), "--doi-list", str(tmp_path / "b.txt")])
    main_path, doi_path = litsearch.resolve_outputs(args, litsearch.Config(), make_plan())
    assert main_path == tmp_path / "a.csv"
    assert doi_path == tmp_path / "b.txt"


def test_resolve_outputs_returns_none_for_stdout():
    args = litsearch.build_parser().parse_args(["q", "--stdout"])
    assert litsearch.resolve_outputs(args, litsearch.Config(), make_plan()) == (None, None)


def test_resolve_outputs_rejects_an_unknown_suffix():
    """以前不认识的 suffix 会静默当 markdown，把 markdown 写进 a.txt。"""
    for suffix in (".txt", ".markdown", ""):
        args = litsearch.build_parser().parse_args(["q", "--output", "a" + suffix])
        with pytest.raises(litsearch.UsageError) as exc:
            litsearch.resolve_outputs(args, litsearch.Config(), make_plan())
        assert ".md" in str(exc.value)


def test_resolve_outputs_accepts_md_csv_json():
    for suffix in (".md", ".csv", ".json", ".CSV"):
        args = litsearch.build_parser().parse_args(["q", "--output", "a" + suffix])
        main_path, _ = litsearch.resolve_outputs(args, litsearch.Config(), make_plan())
        assert str(main_path).endswith(suffix)


def test_render_main_rejects_an_unknown_suffix():
    report = make_report(make_plan(queries=["q"]))
    with pytest.raises(litsearch.UsageError):
        litsearch.render_main(Path("a.txt"), report, [])
    assert litsearch.render_main(None, report, []).startswith("#")   # 无路径 → markdown


def test_write_outputs_creates_parent_dirs_and_returns_paths(tmp_path):
    main_path = tmp_path / "deep" / "nested" / "a.md"
    doi_path = tmp_path / "deep" / "nested" / "a.doi.txt"
    written = litsearch.write_outputs(main_path, doi_path, "MD", "10.1/x\n")
    assert written == [main_path, doi_path]
    assert main_path.read_text(encoding="utf-8") == "MD"
    assert doi_path.read_text(encoding="utf-8") == "10.1/x\n"


def test_write_outputs_skips_none_paths(tmp_path):
    written = litsearch.write_outputs(None, None, "MD", "X")
    assert written == []
