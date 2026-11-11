"""端到端：退出码、写文件、--stdout、预设。全部离线。"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_crossref_nullable_date_succeeds_without_year(tmp_path, monkeypatch, capsys):
    body = json.dumps({"message": {"total-results": 1, "items": [
        {"DOI": "10.1/null-date", "issued": {"date-parts": [[None]]}},
    ]}}).encode()
    code, _, _ = run_cli(["marine bacteria", "--source", "crossref", "--stdout"],
                          [body], tmp_path, monkeypatch, key=None)
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["results"][0]["year"] is None
    assert "10.1/null-date" in captured.err


def test_empty_mixed_sources_keep_json_and_stderr_diagnostics_in_sync(tmp_path, monkeypatch, capsys):
    crossref = json.dumps({"message": {"total-results": 4, "items": [
        {"DOI": f"10.1/{i}", "language": "fr"} for i in range(3)],
        "next-cursor": "next"}}).encode()
    europepmc = b'{"hitCount":0,"resultList":{"result":[]}}'
    code, opener, _ = run_cli(["q", "--source", "crossref,europepmc", "--limit", "1",
                              "--language", "en", "--stdout"],
                             [crossref, europepmc], tmp_path, monkeypatch, key=None)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["results"] == []
    assert len(opener.urls) == 2
    empty = [d for d in payload["diagnostics"] if d["code"] == "empty_results"]
    assert [d["source"] for d in empty] == ["crossref", "europepmc"]
    assert "来源本地条件筛空" in empty[0]["message"]
    assert "远端无命中" in empty[1]["message"]
    window = next(d for d in payload["diagnostics"] if d["code"] == "fetch_window_limited")
    assert "scan_budget" in window["message"]
    assert all(d["message"] in captured.err for d in [*empty, window])


@pytest.mark.parametrize("configured", [False, True])
def test_explain_resolves_output_directory_without_filesystem_side_effects(
    configured, tmp_path, monkeypatch, capsys,
):
    if configured:
        directory = tmp_path / "configuration"
        directory.mkdir()
        config = directory / "search.toml"
        config.write_text('output_dir = "results"\n', encoding="utf-8")
        monkeypatch.setenv("LITSEARCH_CONFIG", str(config))
        expected = directory / "results"
    else:
        expected = tmp_path / "output"
    # 已存在的非目录目标也可解释；解释不执行输出目标检查。
    expected.write_text("existing", encoding="utf-8")
    code, opener, _ = run_cli(["q", "--explain"], [], tmp_path, monkeypatch, key=None)
    output = json.loads(capsys.readouterr().out)["output"]
    assert code == 0
    assert output["directory"] == str(expected.resolve())
    assert output["origin"] == (str(config) if configured else "default")
    assert expected.read_text(encoding="utf-8") == "existing"
    assert opener.urls == []


def test_source_cli_forces_utf8_even_with_cp936_defaults(tmp_path):
    source = Path(litsearch.__file__).resolve()
    env = dict(os.environ, PYTHONIOENCODING="cp936", PYTHONUTF8="0")
    process = subprocess.run([sys.executable, str(source), "--不存在的参数"],
                             cwd=tmp_path, env=env, capture_output=True)
    assert process.returncode == litsearch.EXIT_ARGS
    assert "不存在的参数" in process.stderr.decode("utf-8", errors="strict")
    explanation = subprocess.run([sys.executable, str(source), "海洋细菌", "--explain"],
                                 cwd=tmp_path, env=env, capture_output=True, check=True)
    assert json.loads(explanation.stdout.decode("utf-8"))["plan"]["queries"] == ["海洋细菌"]


@pytest.mark.parametrize("encoding", [None, "cp936"])
def test_csv_stdout_matches_file_bytes_without_blank_records(tmp_path, encoding):
    source = Path(litsearch.__file__).resolve()
    body = json.dumps({"message": {"total-results": 1, "items": [{
        "DOI": "10.1000/csv", "title": ['A "marine", 海洋 paper'],
        "container-title": ['Marine "Journal",\nLetters'],
    }]}}).encode()
    script = f"""
import io, sys
sys.path.insert(0, {str(source.parent)!r})
import litsearch
transport = litsearch.Http(opener=lambda request, timeout: io.BytesIO({body!r}), polite=0)
litsearch.Http = lambda: transport
raise SystemExit(litsearch.main(sys.argv[1:]))
"""
    env = dict(os.environ, PYTHONUTF8="0")
    env.pop("PYTHONIOENCODING", None)
    if encoding:
        env["PYTHONIOENCODING"] = encoding
    command = [sys.executable, "-c", script, "q", "--source", "crossref", "--limit", "1"]
    output = subprocess.run([*command, "--stdout", "--stdout-format", "csv"],
                            cwd=tmp_path, env=env, capture_output=True, check=True)
    path = tmp_path / "结果.csv"
    subprocess.run([*command, "--output", str(path)], cwd=tmp_path, env=env,
                   capture_output=True, check=True)
    assert output.stdout == path.read_bytes()
    assert b"\r\r\n" not in output.stdout
    assert output.stdout.endswith(b"\r\n")
    assert not output.stdout.endswith(b"\r\n\r\n")
    rows = list(csv.DictReader(io.StringIO(output.stdout.decode("utf-8"), newline="")))
    assert len(rows) == 1
    assert rows[0]["title"] == 'A "marine", 海洋 paper'
    assert rows[0]["venue"] == 'Marine "Journal",\nLetters'
    assert "[ok]" in output.stderr.decode("utf-8")


@pytest.mark.parametrize("target_kind,overwrite,expected", [
    ("file", False, "覆盖文件请使用 --overwrite"),
    ("directory", False, "输出目标是目录"),
    ("directory", True, "输出目标是目录"),
    ("parent_file", True, "输出父路径不是目录"),
])
def test_output_target_errors_are_precise_and_precede_network(
    tmp_path, monkeypatch, capsys, target_kind, overwrite, expected,
):
    path = tmp_path / "result.md"
    if target_kind == "directory":
        path.mkdir()
    elif target_kind == "file":
        path.write_text("previous", encoding="utf-8")
    else:
        parent = tmp_path / "parent"
        parent.write_text("previous", encoding="utf-8")
        path = parent / "result.md"
    http, opener = make_http([])
    monkeypatch.setattr(litsearch, "Http", lambda: http)
    argv = ["q", "--source", "crossref", "--output", str(path)]
    if overwrite:
        argv.append("--overwrite")
    assert litsearch.main(argv) == litsearch.EXIT_ARGS
    captured = capsys.readouterr()
    assert expected in captured.err
    assert str(path if target_kind != "parent_file" else path.parent) in captured.err
    if target_kind == "directory":
        assert "--overwrite" not in captured.err
        assert path.is_dir()
    elif target_kind == "file":
        assert path.read_text(encoding="utf-8") == "previous"
    assert captured.out == ""
    assert opener.urls == []


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
    assert "OpenAlex + Crossref + Europe PMC" in capsys.readouterr().out


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


@pytest.mark.parametrize("spec", ["", " ", ", ,"])
@pytest.mark.parametrize("option", ["--require", "--concept"])
def test_empty_group_is_rejected_before_network(spec, option, tmp_path):
    args = litsearch.build_parser().parse_args(["q", option, spec, "--stdout"])
    http, opener = make_http([])
    with pytest.raises(litsearch.UsageError, match=option.removeprefix("--")):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)
    assert opener.urls == []


@pytest.mark.parametrize("spec", ["", " ", ", ,"])
def test_empty_exclude_is_a_noop(spec, tmp_path, monkeypatch, capsys):
    code, _, _ = run_cli(["q", "--source", "crossref", "--exclude", spec, "--stdout"],
                         [load_fixture("crossref_page1.json")], tmp_path, monkeypatch, key=None)
    payload = json.loads(capsys.readouterr().out)
    assert code == litsearch.EXIT_OK
    assert payload["filters"]["exclude"] == []
    assert payload["filter_stats"]["before"] == payload["filter_stats"]["after"]


def test_strict_explain_reports_capabilities_but_search_rejects_before_network(tmp_path, capsys):
    http, opener = make_http([])
    argv = ["q", "--source", "crossref", "--institution", "Institute", "--strict-filters"]
    args = litsearch.build_parser().parse_args([*argv, "--explain"])
    assert litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None) == litsearch.EXIT_OK
    explanation = json.loads(capsys.readouterr().out)
    assert explanation["sources"][0]["filter_execution"]["institution"] == "unsupported"
    assert explanation["diagnostics"][0]["code"] == "unsupported_filter"
    args = litsearch.build_parser().parse_args(argv)
    with pytest.raises(litsearch.UsageError, match="严格"):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)
    assert opener.urls == []


def test_missing_title_warning_is_shared_by_json_and_stderr(tmp_path, monkeypatch, capsys):
    body = json.dumps({"message": {"total-results": 1, "items": [{"DOI": "10.1/untitled"}]}}).encode()
    code, _, _ = run_cli(["q", "--source", "crossref", "--stdout"], [body],
                         tmp_path, monkeypatch, key=None)
    captured = capsys.readouterr()
    warning = next(d for d in json.loads(captured.out)["diagnostics"] if d["field"] == "title")
    assert code == litsearch.EXIT_OK
    assert warning["message"] in captured.err


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
    # --title 现在优先作为文件名片段（可读，且中/英文都能用）
    assert md_files[0].name.startswith("litsearch_test-list_")
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


def test_run_honours_max_results(tmp_path, monkeypatch, capsys):
    """--limit 决定抓多少候选，--max-results 决定最终出多少条。"""
    code, _, _ = run_cli(["q", "--limit", "2", "--max-results", "1"],
                         [load_fixture("openalex_page1.json")],
                         tmp_path, monkeypatch)
    assert code == litsearch.EXIT_OK
    assert "--max-results" in capsys.readouterr().err
    markdown = next((tmp_path / "output").glob("*.md")).read_text(encoding="utf-8")
    assert "过滤后保留 **1 篇**" in markdown
    assert "另有 1 篇被 --max-results 1 截断" in markdown


def test_run_warns_when_the_fetch_window_was_truncated(tmp_path, monkeypatch, capsys):
    """远端 137 篇，只考察前 2 篇 —— 召回天花板不能是隐形的。"""
    run_cli(["q", "--limit", "2"], [load_fixture("openalex_page1.json")],
            tmp_path, monkeypatch)
    err = capsys.readouterr().err
    assert "远端共 137 篇" in err
    assert "只考察了前 2 篇" in err


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


def test_run_stdout_format_md_gives_a_markdown_table(tmp_path, monkeypatch, capsys):
    code, _, _ = run_cli(["q", "--limit", "1", "--stdout", "--stdout-format", "md"],
                         [load_fixture("openalex_page1.json")], tmp_path, monkeypatch)
    assert code == litsearch.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("# q")
    assert "| 年份 |" in out
    assert not (tmp_path / "output").exists()


def test_run_stdout_format_doi_gives_bare_dois(tmp_path, monkeypatch, capsys):
    code, _, _ = run_cli(["q", "--limit", "1", "--stdout", "--stdout-format", "doi"],
                         [load_fixture("openalex_page1.json")], tmp_path, monkeypatch)
    assert code == litsearch.EXIT_OK
    assert capsys.readouterr().out.strip() == "10.1000/aaa"


def test_run_stdout_format_needs_stdout(tmp_path):
    with pytest.raises(litsearch.UsageError):
        litsearch.run(litsearch.build_parser().parse_args(["q", "--stdout-format", "md"]),
                      tmp_path, litsearch.Http(), litsearch.RetryPolicy(), SleepRecorder())


def test_run_rejects_an_unknown_output_suffix_before_any_request(tmp_path):
    """后缀写错要在联网之前失败；且命令行的错优先于「缺 key」报出。

    这里刻意不设 OPENALEX_API_KEY（autouse 的 isolate 会清掉它）：
    若顺序反了，读到的会是 ConfigError（退出码 3）而不是 UsageError（退出码 2）。
    """
    http, opener = make_http([])                     # 空队列：一旦发请求就会报错
    args = litsearch.build_parser().parse_args(["q", "--output", str(tmp_path / "a.txt")])
    with pytest.raises(litsearch.UsageError):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), SleepRecorder())
    assert opener.urls == []


def test_run_rejects_a_reversed_year_range_before_any_request(tmp_path):
    http, opener = make_http([])
    args = litsearch.build_parser().parse_args(["q", "--year", "2026-2021"])
    with pytest.raises(litsearch.UsageError):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), SleepRecorder())
    assert opener.urls == []


def test_run_filename_uses_the_first_query_when_no_title(tmp_path, monkeypatch):
    run_cli(["marine protist", "--limit", "2"],
            [load_fixture("openalex_page1.json")], tmp_path, monkeypatch)
    assert next((tmp_path / "output").glob("*.md")).name.startswith(
        "litsearch_marine-protist_")


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
