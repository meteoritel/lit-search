"""三层配置回退、预设展开与参数校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

import litsearch

TOML = """\
output_dir = "from-toml"
mailto = "toml@example.com"
default_sources = ["openalex"]

[api_keys]
openalex = "from-toml"

[presets.marine]
queries = ["marine protist"]
year = "2021-2026"
limit = 7
require = ["amplicon,asv", "protist,bacteria"]
sort = "year"
"""

DOTENV = "OPENALEX_API_KEY=from-dotenv\nLITSEARCH_OUTPUT_DIR=from-dotenv\n"


@pytest.fixture(autouse=True)
def isolate_user_config(tmp_path, monkeypatch):
    """确保测试绝不会命中本机真实的用户级配置。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "isolated-appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("LITSEARCH_CONFIG", raising=False)


# --------------------------------------------------------------------------- #
# 三层回退
# --------------------------------------------------------------------------- #

def test_env_beats_toml_beats_dotenv(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(TOML, encoding="utf-8")
    (tmp_path / ".env").write_text(DOTENV, encoding="utf-8")
    base_env = {"LITSEARCH_CONFIG": str(config_path)}

    cfg = litsearch.load_config(tmp_path, env=base_env)
    assert cfg.api_keys["openalex"] == "from-toml"
    assert cfg.output_dir == Path("from-toml")
    assert cfg.mailto == "toml@example.com"
    assert cfg.config_path == config_path
    assert cfg.default_sources == ["openalex"]

    cfg_env = litsearch.load_config(tmp_path, env=dict(
        base_env, OPENALEX_API_KEY="from-env", LITSEARCH_OUTPUT_DIR="from-env"))
    assert cfg_env.api_keys["openalex"] == "from-env"
    assert cfg_env.output_dir == Path("from-env")


def test_dotenv_is_the_last_fallback(tmp_path):
    (tmp_path / ".env").write_text(DOTENV, encoding="utf-8")
    cfg = litsearch.load_config(tmp_path, env={})
    assert cfg.api_keys["openalex"] == "from-dotenv"
    assert cfg.output_dir == Path("from-dotenv")
    assert cfg.config_path is None


def test_missing_config_everywhere_is_not_an_error(tmp_path):
    cfg = litsearch.load_config(tmp_path, env={})
    assert cfg.api_keys == {}
    assert cfg.presets == {}
    assert cfg.output_dir is None
    assert cfg.default_sources is None


def test_broken_toml_raises_config_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("a = [1, 2", encoding="utf-8")
    with pytest.raises(litsearch.ConfigError):
        litsearch.load_config(tmp_path, env={"LITSEARCH_CONFIG": str(bad)})


def test_parse_env_file_handles_quotes_comments_and_blanks(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# c\n\nA=1\nB="two"\nC=\'three\'\nD=has=equals\n', encoding="utf-8")
    assert litsearch.parse_env_file(path) == {
        "A": "1", "B": "two", "C": "three", "D": "has=equals"}


def test_parse_env_file_missing_file_returns_empty(tmp_path):
    assert litsearch.parse_env_file(tmp_path / "nope.env") == {}


def test_project_local_litsearch_toml_is_used_without_a_user_config(tmp_path):
    (tmp_path / "litsearch.toml").write_text(
        'output_dir = "from-project"\n[presets.p]\nqueries = ["q"]\n',
        encoding="utf-8")
    cfg = litsearch.load_config(tmp_path, env={})
    assert cfg.output_dir == Path("from-project")
    assert "p" in cfg.presets


def test_project_local_presets_layer_under_the_user_config(tmp_path):
    (tmp_path / "litsearch.toml").write_text(
        '[presets.shared]\nqueries = ["from-project"]\n'
        '[presets.only_project]\nqueries = ["p"]\n',
        encoding="utf-8")
    user = tmp_path / "user.toml"
    user.write_text('[presets.shared]\nqueries = ["from-user"]\n', encoding="utf-8")
    cfg = litsearch.load_config(tmp_path, env={"LITSEARCH_CONFIG": str(user)})
    assert cfg.presets["shared"]["queries"] == ["from-user"]   # 高优先级覆盖同名
    assert cfg.presets["only_project"]["queries"] == ["p"]     # 项目级仍然可见


# --------------------------------------------------------------------------- #
# key 检查
# --------------------------------------------------------------------------- #

def test_check_keys_raises_for_openalex_without_key():
    with pytest.raises(litsearch.ConfigError, match="API key"):
        litsearch.check_keys(["openalex"], litsearch.Config())


def test_check_keys_allows_crossref_without_key():
    litsearch.check_keys(["crossref"], litsearch.Config())


def test_missing_key_message_is_actionable():
    message = litsearch.missing_key_message(litsearch.PROVIDERS["openalex"])
    assert "openalex.org/settings/api" in message
    assert "OPENALEX_API_KEY" in message


# --------------------------------------------------------------------------- #
# 预设展开与优先级
# --------------------------------------------------------------------------- #

PRESET = {
    "queries": ["marine protist"],
    "year": "2021-2026",
    "limit": 7,
    "require": ["amplicon,asv", "protist,bacteria"],
    "sort": "year",
}


def test_preset_expands_when_cli_is_silent():
    args = litsearch.build_parser().parse_args([])
    plan = litsearch.build_plan(args, PRESET, litsearch.Config())
    assert plan.queries == ["marine protist"]
    assert plan.limit == 7
    assert plan.year == "2021-2026"
    assert plan.sort == "year"
    assert plan.require == [["amplicon", "asv"], ["protist", "bacteria"]]


def test_cli_overrides_preset_and_replaces_lists_wholesale():
    args = litsearch.build_parser().parse_args(
        ["other query", "--limit", "3", "--sort", "citations", "--require", "x,y"])
    plan = litsearch.build_plan(args, PRESET, litsearch.Config())
    assert plan.queries == ["other query"]
    assert plan.limit == 3
    assert plan.sort == "citations"
    assert plan.require == [["x", "y"]]
    assert plan.year == "2021-2026"          # 未被覆盖的仍来自 preset


def test_preset_type_alias_maps_to_work_type():
    args = litsearch.build_parser().parse_args([])
    plan = litsearch.build_plan(args, {"queries": ["q"], "type": "article"},
                                litsearch.Config())
    assert plan.work_type == "article"


def test_preset_source_alias():
    args = litsearch.build_parser().parse_args([])
    plan = litsearch.build_plan(args, {"queries": ["q"], "source": ["crossref"]},
                                litsearch.Config())
    assert plan.sources == ["crossref"]


def test_preset_oa_only_can_be_switched_off_from_cli():
    args = litsearch.build_parser().parse_args(["--no-oa-only"])
    plan = litsearch.build_plan(args, {"queries": ["q"], "oa_only": True},
                                litsearch.Config())
    assert plan.oa_only is False


def test_config_default_sources_used_when_cli_is_silent():
    args = litsearch.build_parser().parse_args(["q"])
    plan = litsearch.build_plan(args, None, litsearch.Config(default_sources=["crossref"]))
    assert plan.sources == ["crossref"]


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #

def test_missing_query_is_a_usage_error():
    args = litsearch.build_parser().parse_args([])
    with pytest.raises(litsearch.UsageError):
        litsearch.build_plan(args, None, litsearch.Config())


def test_unknown_source_is_a_usage_error():
    args = litsearch.build_parser().parse_args(["q", "--source", "pubmed"])
    with pytest.raises(litsearch.UsageError, match="pubmed"):
        litsearch.build_plan(args, None, litsearch.Config())


@pytest.mark.parametrize("bad", ["20x4", "2020-", "-2020", "2020-2021-2022"])
def test_bad_year_is_a_usage_error(bad):
    args = litsearch.build_parser().parse_args(["q", "--year", bad])
    with pytest.raises(litsearch.UsageError):
        litsearch.build_plan(args, None, litsearch.Config())


def test_year_whitespace_is_normalised():
    args = litsearch.build_parser().parse_args(["q", "--year", "2021 - 2026"])
    plan = litsearch.build_plan(args, None, litsearch.Config())
    assert plan.year == "2021-2026"


def test_limit_must_be_positive():
    args = litsearch.build_parser().parse_args(["q", "--limit", "0"])
    with pytest.raises(litsearch.UsageError):
        litsearch.build_plan(args, None, litsearch.Config())


def test_source_list_is_deduplicated_by_lowercasing():
    args = litsearch.build_parser().parse_args(["q", "--source", "OpenAlex, crossref"])
    plan = litsearch.build_plan(args, None, litsearch.Config())
    assert plan.sources == ["openalex", "crossref"]
