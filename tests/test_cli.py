"""端到端：退出码、写文件、--stdout、预设。全部离线。"""

from __future__ import annotations

import json

import pytest

import litsearch
from conftest import SleepRecorder, load_fixture, make_http


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "isolated-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    for name in ("LITSEARCH_CONFIG", "LITSEARCH_OUTPUT_DIR", "OPENALEX_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def run_cli(argv, responses, tmp_path, monkeypatch, key="test-key"):
    if key is not None:
        monkeypatch.setenv("OPENALEX_API_KEY", key)
    http, opener = make_http(responses)
    sleeps = SleepRecorder()
    args = litsearch.build_parser().parse_args(argv)
    code = litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), sleeps)
    return code, opener, sleeps


# --------------------------------------------------------------------------- #
# main() 的退出码（都在任何网络调用之前失败）
# --------------------------------------------------------------------------- #

def test_main_rejects_unknown_flag():
    assert litsearch.main(["--bogus"]) == litsearch.EXIT_ARGS


def test_main_requires_a_query():
    assert litsearch.main([]) == litsearch.EXIT_ARGS


def test_main_returns_config_error_without_a_key():
    assert litsearch.main(["query"]) == litsearch.EXIT_CONFIG


def test_main_returns_arg_error_for_unknown_source():
    assert litsearch.main(["query", "--source", "pubmed"]) == litsearch.EXIT_ARGS


def test_main_help_exits_zero(capsys):
    assert litsearch.main(["--help"]) == litsearch.EXIT_OK
    assert "litsearch" in capsys.readouterr().out


@pytest.mark.parametrize("error,expected", [
    (litsearch.UsageError, litsearch.EXIT_ARGS),
    (litsearch.ConfigError, litsearch.EXIT_CONFIG),
    (litsearch.NetworkError, litsearch.EXIT_NET),
])
def test_main_maps_errors_to_exit_codes(monkeypatch, error, expected):
    def boom(*args, **kwargs):
        raise error("boom")

    monkeypatch.setattr(litsearch, "run", boom)
    assert litsearch.main(["query"]) == expected


# --------------------------------------------------------------------------- #
# 端到端
# --------------------------------------------------------------------------- #

def test_run_writes_markdown_and_doi_list(tmp_path, monkeypatch):
    code, _, _ = run_cli(
        ["marine protist grazing", "--limit", "3", "--title", "Test List"],
        [load_fixture("openalex_page1.json"), load_fixture("openalex_page2.json")],
        tmp_path, monkeypatch)
    assert code == litsearch.EXIT_OK

    out_dir = tmp_path / "output"
    md_files = list(out_dir.glob("*.md"))
    doi_files = list(out_dir.glob("*.doi.txt"))
    assert len(md_files) == 1
    assert len(doi_files) == 1
    assert md_files[0].name.startswith("litsearch_marine-protist-grazing_")
    assert doi_files[0].name == md_files[0].stem + ".doi.txt"

    markdown = md_files[0].read_text(encoding="utf-8")
    assert "# Test List" in markdown
    assert "| 作者 |" in markdown
    assert "| 来源 |" not in markdown
    assert doi_files[0].read_text(encoding="utf-8").splitlines() == [
        "10.1000/aaa", "10.1000/ddd"]


def test_run_prints_written_paths_to_stdout(tmp_path, monkeypatch, capsys):
    run_cli(["q", "--limit", "2"], [load_fixture("openalex_page1.json")],
            tmp_path, monkeypatch)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0].endswith(".md")
    assert lines[1].endswith(".doi.txt")


def test_run_reports_records_without_doi_on_stderr(tmp_path, monkeypatch, capsys):
    run_cli(["q", "--limit", "3"],
            [load_fixture("openalex_page1.json"), load_fixture("openalex_page2.json")],
            tmp_path, monkeypatch)
    assert "无 DOI" in capsys.readouterr().err


def test_run_require_is_applied_after_merge_not_per_source(tmp_path, monkeypatch):
    """OpenAlex 与 Crossref 命中同一篇；require 只在合并结果上跑，
    所以 Crossref 那份缺摘要不会导致该论文被误删。"""
    code, _, _ = run_cli(
        ["q", "--source", "openalex,crossref", "--limit", "3",
         "--require", "grazing", "--title", "Filtered"],
        [load_fixture("openalex_page1.json"), load_fixture("openalex_page2.json"),
         load_fixture("crossref_page1.json")],
        tmp_path, monkeypatch)
    assert code == litsearch.EXIT_OK
    markdown = next((tmp_path / "output").glob("*.md")).read_text(encoding="utf-8")
    assert "Marine protist grazing on bacteria" in markdown
    assert "Nanoflagellate bacterivory" not in markdown
    assert "Viral shunt" not in markdown
    assert "| 来源 |" in markdown
    assert "crossref/openalex" in markdown


def test_run_stdout_prints_json_and_writes_nothing(tmp_path, monkeypatch, capsys):
    code, _, _ = run_cli(["q", "--limit", "1", "--stdout"],
                         [load_fixture("openalex_page1.json")], tmp_path, monkeypatch)
    assert code == litsearch.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["results"][0]["doi"] == "10.1000/aaa"
    assert payload["results"][0]["source"] == "openalex"
    assert not (tmp_path / "output").exists()


def test_run_stdout_conflicts_with_output(tmp_path):
    args = litsearch.build_parser().parse_args(["q", "--stdout", "--output", "x.md"])
    http, _ = make_http([])
    with pytest.raises(litsearch.UsageError):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)


def test_run_stdout_conflicts_with_doi_list(tmp_path):
    args = litsearch.build_parser().parse_args(["q", "--stdout", "--doi-list", "x.txt"])
    http, _ = make_http([])
    with pytest.raises(litsearch.UsageError):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)


def test_run_csv_escape_hatch(tmp_path, monkeypatch):
    out = tmp_path / "list.csv"
    run_cli(["q", "--limit", "2", "--output", str(out)],
            [load_fixture("openalex_page1.json")], tmp_path, monkeypatch)
    header, *rows = out.read_text(encoding="utf-8").splitlines()
    assert header.startswith("doi,title,year")
    assert len(rows) == 2
    assert (tmp_path / "list.doi.txt").is_file()


def test_run_json_escape_hatch(tmp_path, monkeypatch):
    out = tmp_path / "list.json"
    run_cli(["q", "--limit", "2", "--output", str(out)],
            [load_fixture("openalex_page1.json")], tmp_path, monkeypatch)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert {"doi", "title", "authors", "sources"} <= set(payload["results"][0])


def test_run_honours_project_local_litsearch_toml(tmp_path, monkeypatch):
    custom = tmp_path / "custom_out"
    (tmp_path / "litsearch.toml").write_text(
        f'output_dir = "{custom.as_posix()}"\n', encoding="utf-8")
    run_cli(["q", "--limit", "2"], [load_fixture("openalex_page1.json")],
            tmp_path, monkeypatch)
    assert len(list(custom.glob("*.md"))) == 1
    assert not (tmp_path / "output").exists()


def test_run_unknown_preset_is_a_config_error(tmp_path):
    args = litsearch.build_parser().parse_args(["q", "--preset", "nope"])
    http, _ = make_http([])
    with pytest.raises(litsearch.ConfigError, match="nope"):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)


def test_run_expands_a_preset_from_project_local_toml(tmp_path, monkeypatch):
    (tmp_path / "litsearch.toml").write_text(
        '[presets.marine]\nqueries = ["preset query"]\nlimit = 2\n', encoding="utf-8")
    (tmp_path / ".env").write_text("OPENALEX_API_KEY=from-dotenv\n", encoding="utf-8")
    code, opener, _ = run_cli(["--preset", "marine"],
                              [load_fixture("openalex_page1.json")],
                              tmp_path, monkeypatch, key=None)
    assert code == litsearch.EXIT_OK
    assert "search=preset+query" in opener.urls[0]


def test_run_propagates_network_errors(tmp_path, monkeypatch):
    http, _ = make_http([400])
    args = litsearch.build_parser().parse_args(["q", "--limit", "2"])
    monkeypatch.setenv("OPENALEX_API_KEY", "test-key")
    with pytest.raises(litsearch.NetworkError):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)
