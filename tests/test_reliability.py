"""外部输入、响应结构和文件交付的回归用例。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import litsearch
from conftest import make_http, make_plan, make_record


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for key in ("LITSEARCH_CONFIG", "OPENALEX_API_KEY", "LITSEARCH_OUTPUT_DIR"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("preset", [
    {"queries": "marine"}, {"queries": ["q"], "require": "marine"},
    {"queries": ["q"], "oa_only": "false"},
    {"queries": ["q"], "sort": "citatons"},
    {"queries": ["q"], "fetch_sort": "citatons"},
    {"queries": ["q"], "limti": 20}, {"queries": ["q"], "limit": True},
    {"queries": ["q"], "year": "2026-2020"}, {"queries": [""]},
])
def test_invalid_preset_is_a_config_error(preset):
    args = litsearch.build_parser().parse_args([])
    with pytest.raises(litsearch.ConfigError):
        litsearch.build_plan(args, preset, litsearch.Config())


@pytest.mark.parametrize("document", [
    'api_keys = ["bad"]', 'default_sources = "crossref"',
    '[presets]\nmarine = "bad"', 'outpt_dir = "output"',
])
def test_invalid_toml_structure_is_a_config_error(tmp_path, document):
    (tmp_path / "litsearch.toml").write_text(document, encoding="utf-8")
    with pytest.raises(litsearch.ConfigError):
        litsearch.load_config(tmp_path, env={})


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(litsearch.ConfigError, match="LITSEARCH_CONFIG"):
        litsearch.load_config(tmp_path, env={"LITSEARCH_CONFIG": "missing.toml"})


@pytest.mark.parametrize("body", [b"[]", b"{}", b'{"message":{"total-results":0}}',
    b'{"message":{"total-results":0,"items":{}}}',
    b'{"message":{"total-results":"0","items":[]}}',
    b'{"message":{"total-results":1,"items":[{"title":42}]}}',
    b'{"message":{"total-results":1,"items":[{"title":"Wrong shape"}]}}',
    b'{"message":{"total-results":1,"items":[{"DOI":"10.1/a","published":{"date-parts":["2024"]}}]}}',
    b'{"message":{"total-results":NaN,"items":[]}}',
])
def test_invalid_remote_shape_is_a_network_error(body):
    http, _ = make_http([body])
    with pytest.raises(litsearch.NetworkError):
        litsearch.PROVIDERS["crossref"].search(
            "q", make_plan(sources=["crossref"]), http,
            litsearch.RetryPolicy(), litsearch.Config())


def test_legitimate_empty_remote_result_is_successful():
    body = json.dumps({"message": {"total-results": 0, "items": []}}).encode()
    http, _ = make_http([body])
    result = litsearch.PROVIDERS["crossref"].search(
        "q", make_plan(sources=["crossref"]), http,
        litsearch.RetryPolicy(), litsearch.Config())
    assert result.total == 0
    assert result.records == []


def test_unicode_title_and_source_identity_are_preserved():
    records = [make_record(title="海洋细菌与原生生物相互作用"),
               make_record(title=None, source_id="W123")]
    merged, stats = litsearch.merge_records(records)
    assert len(merged) == 2
    assert stats.dropped_no_id == 0


def test_output_aliases_are_rejected_before_requests(tmp_path):
    args = litsearch.build_parser().parse_args([
        "q", "--output", "same.json", "--doi-list", "./same.json"])
    http, opener = make_http([])
    with pytest.raises(litsearch.UsageError, match="同一个文件"):
        litsearch.run(args, tmp_path, http, litsearch.RetryPolicy(), lambda _: None)
    assert opener.urls == []


def test_relative_paths_use_the_explicit_cwd(tmp_path):
    args = litsearch.build_parser().parse_args(["q", "--output", "list.json"])
    main, doi = litsearch.resolve_outputs(args, litsearch.Config(), make_plan(), tmp_path)
    assert main == tmp_path / "list.json"
    assert doi == tmp_path / "list.doi.txt"


def test_existing_outputs_require_explicit_overwrite(tmp_path):
    path = tmp_path / "list.md"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(litsearch.UsageError):
        litsearch.write_outputs(path, None, "new", "")
    assert path.read_text(encoding="utf-8") == "old"
    litsearch.write_outputs(path, None, "new", "", overwrite=True)
    assert path.read_text(encoding="utf-8") == "new"


def test_staging_failure_preserves_previous_files(tmp_path, monkeypatch):
    main, doi = tmp_path / "list.md", tmp_path / "list.doi.txt"
    main.write_text("old", encoding="utf-8")
    original = litsearch.tempfile.NamedTemporaryFile
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(None)
        if len(calls) == 2:
            raise PermissionError("simulated staging failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(litsearch.tempfile, "NamedTemporaryFile", fail_second)
    with pytest.raises(litsearch.OutputError) as error:
        litsearch.write_outputs(main, doi, "new", "DOI", overwrite=True)
    assert error.value.written == []
    assert main.read_text(encoding="utf-8") == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["list.md"]


def test_partial_commit_reports_written_paths(tmp_path, monkeypatch):
    main, doi = tmp_path / "list.md", tmp_path / "list.doi.txt"
    replace = litsearch.os.replace

    def fail_doi(source, target):
        if target == doi:
            raise PermissionError("simulated commit failure")
        replace(source, target)

    monkeypatch.setattr(litsearch.os, "replace", fail_doi)
    with pytest.raises(litsearch.OutputError) as error:
        litsearch.write_outputs(main, doi, "new", "DOI", overwrite=True)
    assert error.value.written == [main]
    assert main.read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["list.md"]


def test_concurrent_target_is_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "list.md"
    operation = "rename" if litsearch.os.name == "nt" else "link"
    commit = getattr(litsearch.os, operation)

    def competing_commit(source, destination):
        target.write_text("another process", encoding="utf-8")
        commit(source, destination)

    monkeypatch.setattr(litsearch.os, operation, competing_commit)
    with pytest.raises(litsearch.OutputError) as error:
        litsearch.write_outputs(target, None, "ours", "")
    assert error.value.written == []
    assert target.read_text(encoding="utf-8") == "another process"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["list.md"]


def test_main_maps_output_error(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise litsearch.OutputError("simulated failure", [Path("committed.md")])

    monkeypatch.setattr(litsearch, "run", fail)
    assert litsearch.main(["q"]) == litsearch.EXIT_OUTPUT
    assert capsys.readouterr().out.strip() == "committed.md"
