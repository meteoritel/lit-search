#!/usr/bin/env python3
"""litsearch — 多源文献检索 CLI（OpenAlex + Crossref），零第三方运行时依赖。

用法示例：
    # 基本检索：写 output/litsearch_<slug>_<日期>.md 与同名 .doi.txt
    python litsearch.py "marine protist prokaryote interaction" --year 2021-2026 --limit 30

    # 分组相关性过滤：--require 组内 OR、组间 AND，可重复
    python litsearch.py "marine protist prokaryote interaction" \
        --require "amplicon,asv,16s,18s" --require "protist,eukaryot,bacteria" \
        --exclude "review,meta-analysis" --limit 200

    # 用配置里的命名预设，并把 JSON 打到 stdout（不写文件）
    python litsearch.py --preset marine_amplicon --stdout

    # 同时用两个源（Crossref 默认关闭）
    python litsearch.py "grazing nanoflagellate" --source openalex,crossref

配置（逐键取首个命中：环境变量 → 用户级 config.toml → 项目 litsearch.toml → 项目 .env）：
    OPENALEX_API_KEY                  OpenAlex API key（必需）
    CROSSREF_MAILTO                   联系邮箱（可选，Crossref 礼貌池）
    LITSEARCH_OUTPUT_DIR              默认输出目录
    LITSEARCH_CONFIG                  指定用户级 config.toml 路径
    %APPDATA%\\litsearch\\config.toml   用户级结构化配置
    ./litsearch.toml                  项目级结构化配置（output_dir / [presets]）
    ./.env                            项目级扁平 KEY=VALUE

    结构化配置（output_dir / default_sources / [api_keys] / [presets]）只能来自 TOML；
    .env 只提供扁平键回退。同名 preset 以高优先级文件为准，不同名则叠加。

退出码：0 成功 / 2 参数错误 / 3 配置或 key 缺失 / 4 网络或 API 错误
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import html
import io
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable

__version__ = "0.2.0"

API_OPENALEX = "https://api.openalex.org"
API_CROSSREF = "https://api.crossref.org"
MAX_PER_PAGE = 100
CROSSREF_PAGE_SIZE = 100
CROSSREF_MAX_ROWS = 1000
POLITE_DELAY = 0.5
USER_AGENT = f"litsearch/{__version__}"

OPENALEX_SELECT = (
    "id,doi,title,publication_year,publication_date,cited_by_count,"
    "primary_location,authorships,abstract_inverted_index,open_access,type,"
    "relevance_score,language"
)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_CONFIG = 3
EXIT_NET = 4

DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)

CROSSREF_TYPE_MAP = {
    "article": "journal-article",
    "preprint": "posted-content",
}

# Crossref 缺少 relevance_score，且不支持 cited_by_count 服务端过滤
NO_RELEVANCE_SOURCES = frozenset({"crossref"})


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #

class LitsearchError(Exception):
    """所有可预期错误的基类，由 main() 映射为退出码。"""


class UsageError(LitsearchError):
    """参数组合非法 → 退出码 2。"""


class ConfigError(LitsearchError):
    """配置缺失或 key 缺失 → 退出码 3。"""


class NetworkError(LitsearchError):
    """网络或远端 API 错误 → 退出码 4。"""


# --------------------------------------------------------------------------- #
# 统一记录
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class Record:
    """跨源统一记录。缺失字段一律 None / []，绝不使用 "" 或 0。"""

    doi: str | None = None
    title: str | None = None
    year: int | None = None
    publication_date: str | None = None
    venue: str | None = None
    work_type: str | None = None
    citation_count: int | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    is_open_access: bool | None = None
    open_access_pdf: str | None = None
    language: str | None = None
    relevance_score: float | None = None
    source: str = ""
    sources: list[str] = field(default_factory=list)
    source_id: str = ""


RECORD_FIELDS = (
    "doi", "title", "year", "publication_date", "venue", "work_type",
    "citation_count", "abstract", "authors", "is_open_access",
    "open_access_pdf", "language", "relevance_score", "source", "sources",
    "source_id",
)

# 合并时用非空值回填的字段（sources 单独并集处理）
MERGE_FILL_FIELDS = RECORD_FIELDS


@dataclass(slots=True)
class MergeStats:
    total_in: int = 0
    dropped_no_id: int = 0
    no_doi: int = 0
    doi_merged: int = 0
    title_merged: int = 0
    total_out: int = 0


@dataclass(slots=True)
class ProviderResult:
    total: int
    records: list[Record]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QueryPlan:
    """已解析完 preset 与默认值的检索计划。"""

    queries: list[str]
    sources: list[str]
    year: str | None = None
    limit: int = 25
    min_citations: int | None = None
    author: str | None = None
    institution: str | None = None
    journal_issn: str | None = None
    work_type: str | None = None
    oa_only: bool | None = None
    language: str | None = None
    raw_filter: str | None = None
    require: list[list[str]] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    sort: str = "citations"
    title: str | None = None


@dataclass(slots=True)
class Report:
    plan: QueryPlan
    totals: list[tuple[str, int]]
    stats: MergeStats


# --------------------------------------------------------------------------- #
# HTTP 与重试
# --------------------------------------------------------------------------- #

@dataclass
class Http:
    """urllib 薄封装。opener 可注入，便于测试完全离线。"""

    user_agent: str = USER_AGENT
    timeout: float = 30.0
    opener: Callable[..., Any] = urllib.request.urlopen

    def get_bytes(self, url: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        with self.opener(req, timeout=self.timeout) as resp:
            return resp.read()

    def get_json(self, url: str) -> dict:
        return json.loads(self.get_bytes(url).decode("utf-8", errors="replace"))


@dataclass
class RetryPolicy:
    attempts: int = 4
    base: float = 1.0
    cap: float = 30.0
    sleep: Callable[[float], None] = time.sleep

    def delay(self, attempt: int) -> float:
        return min(self.cap, self.base * (2 ** attempt))


def request_with_backoff(http: Http, url: str, policy: RetryPolicy) -> bytes:
    """对 429/5xx 做指数退避重试；4xx（除 429）直接失败。"""
    last: Exception | None = None
    for attempt in range(max(1, policy.attempts)):
        try:
            return http.get_bytes(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS:
                hint = ""
                if exc.code == 429:
                    hint = "（429 通常是超出每日预算或访问过频，请检查 API key）"
                elif exc.code in (401, 403):
                    hint = "（请检查 API key 是否有效）"
                raise NetworkError(f"HTTP {exc.code}: {exc.reason}{hint}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt + 1 < max(1, policy.attempts):
            policy.sleep(policy.delay(attempt))
    hint = ""
    if isinstance(last, urllib.error.HTTPError) and last.code == 429:
        hint = "；429 通常是超出每日预算或访问过频，请检查 API key 是否有效"
    raise NetworkError(f"请求失败（已重试 {policy.attempts} 次）：{last}{hint}")


# --------------------------------------------------------------------------- #
# 摘要处理
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")


def reconstruct_abstract(inv_idx: dict | None) -> str | None:
    """OpenAlex 摘要为倒排索引 {word: [positions]}，重建为正常语序文本。"""
    if not inv_idx:
        return None
    slots: list[tuple[int, str]] = []
    for word, positions in inv_idx.items():
        for p in positions:
            slots.append((p, word))
    slots.sort()
    text = " ".join(w for _, w in slots).strip()
    return text or None


def strip_jats(text: str | None) -> str | None:
    """Crossref 摘要是 JATS XML，剥标签并解实体。"""
    if not text:
        return None
    plain = _TAG_RE.sub("", text)
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain or None


# --------------------------------------------------------------------------- #
# 归一化与合并
# --------------------------------------------------------------------------- #

def norm_title(title: str | None) -> str | None:
    """标题归一化：小写、去掉所有非字母数字。"""
    normalized = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
    return normalized or None


def norm_doi(doi: str | None) -> str | None:
    """DOI 归一化：小写、去掉 URL/前缀。"""
    if not doi:
        return None
    cleaned = doi.strip().lower()
    for prefix in DOI_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    cleaned = cleaned.strip()
    return cleaned or None


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _wins(a: Record, b: Record) -> bool:
    """被引高者胜；平手时 OpenAlex 优先（跨源被引数不可比）。"""
    ca, cb = a.citation_count or 0, b.citation_count or 0
    if ca != cb:
        return ca > cb
    a_oa = "openalex" in a.sources
    b_oa = "openalex" in b.sources
    if a_oa != b_oa:
        return a_oa
    return True


def _merge_pair(a: Record, b: Record) -> Record:
    base, other = (a, b) if _wins(a, b) else (b, a)
    out = replace(base)
    out.authors = list(base.authors)
    out.sources = sorted(set(base.sources) | set(other.sources))
    for name in MERGE_FILL_FIELDS:
        if _is_empty(getattr(out, name)) and not _is_empty(getattr(other, name)):
            setattr(out, name, getattr(other, name))
    if not out.authors and other.authors:
        out.authors = list(other.authors)
    if out.relevance_score is None:
        out.relevance_score = other.relevance_score
    elif other.relevance_score is not None:
        out.relevance_score = max(out.relevance_score, other.relevance_score)
    return out


def _collapse(records: Iterable[Record],
              keyfn: Callable[[Record], str | None]) -> list[Record]:
    """按 key 合并；key 为 None 的记录原样保留（不参与合并）。"""
    merged: dict[str, Record] = {}
    order: list[str] = []
    passthrough: list[Record] = []
    for rec in records:
        key = keyfn(rec)
        if key is None:
            passthrough.append(rec)
            continue
        if key in merged:
            merged[key] = _merge_pair(merged[key], rec)
        else:
            merged[key] = rec
            order.append(key)
    return [merged[k] for k in order] + passthrough


def merge_records(records: list[Record]) -> tuple[list[Record], MergeStats]:
    """两级去重：先 DOI（跨源同文），再归一化标题（preprint/正式版），迭代到不动点。"""
    stats = MergeStats(total_in=len(records))
    kept = [r for r in records if norm_doi(r.doi) or norm_title(r.title)]
    stats.dropped_no_id = len(records) - len(kept)
    stats.no_doi = sum(1 for r in kept if norm_doi(r.doi) is None)

    pool = kept
    while True:
        before = len(pool)
        pool = _collapse(pool, lambda r: norm_doi(r.doi))
        stats.doi_merged += before - len(pool)
        mid = len(pool)
        pool = _collapse(pool, lambda r: norm_title(r.title))
        stats.title_merged += mid - len(pool)
        if len(pool) == before:
            break
    stats.total_out = len(pool)
    return pool, stats


# --------------------------------------------------------------------------- #
# 相关性过滤与排序
# --------------------------------------------------------------------------- #

def _split_groups(spec: str) -> list[list[str]]:
    """'a1,a2;a3,a4' -> [['a1','a2'], ['a3','a4']]。"""
    groups: list[list[str]] = []
    for group in spec.split(";"):
        terms = [t.strip().lower() for t in group.split(",") if t.strip()]
        if terms:
            groups.append(terms)
    return groups


def _split_or(spec: str) -> list[str]:
    return [t.strip().lower() for t in spec.split(",") if t.strip()]


def matches(text: str, require_groups: list[list[str]],
            exclude_terms: list[str]) -> bool:
    """须命中所有 require 组（组内任一），且不命中任一 exclude 词。"""
    lowered = text.lower()
    if exclude_terms and any(t in lowered for t in exclude_terms):
        return False
    for group in require_groups:
        if not any(t in lowered for t in group):
            return False
    return True


def apply_filters(records: list[Record], require_groups: list[list[str]],
                  exclude_terms: list[str]) -> list[Record]:
    """在合并后的集合上执行 —— 绝不逐源执行，否则跨源召回不一致。"""
    if not require_groups and not exclude_terms:
        return list(records)
    out: list[Record] = []
    for rec in records:
        text = f"{rec.title or ''} {rec.abstract or ''}"
        if matches(text, require_groups, exclude_terms):
            out.append(rec)
    return out


def _sort_key(sort: str) -> Callable[[Record], tuple]:
    if sort == "year":
        return lambda r: (r.year or 0, r.citation_count or 0)
    if sort == "relevance":
        # 无 relevance_score 的源（Crossref）排到后段，内部按被引
        def key(r: Record) -> tuple:
            if r.relevance_score is not None:
                return (1, float(r.relevance_score))
            return (0, float(r.citation_count or 0))
        return key
    return lambda r: (r.citation_count or 0, r.year or 0)


def sort_records(records: list[Record], sort: str) -> list[Record]:
    return sorted(records, key=_sort_key(sort), reverse=True)


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #

class Provider:
    name = ""
    env_key = ""
    requires_api_key = False
    supports_cited_by_filter = False
    supports_relevance = False
    key_help = ""

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        raise NotImplementedError

    def build_url(self, *args, **kwargs) -> str:
        raise NotImplementedError


class OpenAlexProvider(Provider):
    name = "openalex"
    env_key = "OPENALEX_API_KEY"
    requires_api_key = True
    supports_cited_by_filter = True
    supports_relevance = True
    key_help = (
        "OpenAlex 自 2026 年起对匿名访问限流，必须提供 API key。\n"
        "  申请：https://openalex.org/settings/api\n"
        "  设置（任选其一）：\n"
        '    1) 环境变量   setx OPENALEX_API_KEY "你的key"   （重开终端生效）\n'
        "    2) 用户级配置 %APPDATA%\\litsearch\\config.toml  ->  "
        '[api_keys] 下写 openalex = "你的key"\n'
        "    3) 项目级     ./.env  ->  OPENALEX_API_KEY=你的key"
    )

    def structured_filters(self, plan: QueryPlan) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if plan.year:
            lo, _, hi = plan.year.partition("-")
            hi = hi or lo
            pairs.append(("from_publication_date", f"{lo.strip()}-01-01"))
            pairs.append(("to_publication_date", f"{hi.strip()}-12-31"))
        if plan.min_citations is not None:
            pairs.append(("cited_by_count", f">{max(0, plan.min_citations - 1)}"))
        if plan.author:
            value = plan.author.strip()
            if re.fullmatch(r"A\d+", value):
                pairs.append(("authorships.author.id", value))
            else:
                pairs.append(("raw_author_name.search", value))
        if plan.institution:
            value = plan.institution.strip()
            if re.fullmatch(r"https?://ror\.org/\w+", value):
                pairs.append(("institutions.ror", value.rstrip("/").rsplit("/", 1)[-1]))
            elif re.fullmatch(r"I\d+", value):
                pairs.append(("institutions.id", value))
            else:
                pairs.append(("raw_affiliation_strings.search", value))
        if plan.journal_issn:
            pairs.append(("primary_location.source.issn", plan.journal_issn.strip()))
        if plan.work_type:
            pairs.append(("type", plan.work_type.strip()))
        if plan.oa_only:
            pairs.append(("is_oa", "true"))
        if plan.language:
            pairs.append(("language", plan.language.strip()))
        return pairs

    def _raw_filter_keys(self, raw: str) -> set[str]:
        keys = set()
        for chunk in raw.split(","):
            key = chunk.split(":", 1)[0].strip()
            if key:
                keys.add(key)
        return keys

    def filter_string(self, plan: QueryPlan) -> tuple[str, list[str]]:
        """结构化 filter 在前，--oa-filter 追加在后；同字段键时 raw 优先并警告。"""
        warnings: list[str] = []
        raw = (plan.raw_filter or "").strip()
        raw_keys = self._raw_filter_keys(raw) if raw else set()
        parts = []
        for key, value in self.structured_filters(plan):
            if key in raw_keys:
                warnings.append(
                    f"openalex: --oa-filter 中的 {key} 覆盖了结构化参数，已忽略后者"
                )
                continue
            parts.append(f"{key}:{value}")
        if raw:
            parts.append(raw)
        return ",".join(parts), warnings

    def build_url(self, query: str, plan: QueryPlan, api_key: str,
                  cursor: str, per_page: int) -> str:
        filter_str, _ = self.filter_string(plan)
        params = {
            "search": query,
            "per_page": str(max(1, min(MAX_PER_PAGE, per_page))),
            "cursor": cursor,
            "select": OPENALEX_SELECT,
        }
        if filter_str:
            params["filter"] = filter_str
        if api_key:
            params["api_key"] = api_key
        return f"{API_OPENALEX}/works?{urllib.parse.urlencode(params)}"

    def map_work(self, work: dict) -> Record:
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        oa = work.get("open_access") or {}
        authors: list[str] = []
        for authorship in work.get("authorships") or []:
            name = ((authorship.get("author") or {}).get("display_name") or "").strip()
            if name:
                authors.append(name)
        return Record(
            doi=norm_doi(work.get("doi")),
            title=work.get("title"),
            year=work.get("publication_year"),
            publication_date=work.get("publication_date"),
            venue=source.get("display_name"),
            work_type=work.get("type"),
            citation_count=work.get("cited_by_count"),
            abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
            authors=authors,
            is_open_access=oa.get("is_oa"),
            open_access_pdf=oa.get("oa_url") or None,
            language=work.get("language"),
            relevance_score=work.get("relevance_score"),
            source=self.name,
            sources=[self.name],
            source_id=work.get("id") or "",
        )

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        _, warnings = self.filter_string(plan)
        api_key = cfg.api_keys.get(self.name, "")
        records: list[Record] = []
        total = 0
        cursor: str | None = "*"
        remaining = plan.limit
        # 循环条件里显式要求 cursor 非空：只有真拿到 next_cursor 才翻下一页，
        # 否则 None 会被拼进 URL。
        while remaining > 0 and cursor:
            url = self.build_url(query, plan, api_key, cursor,
                                 min(MAX_PER_PAGE, remaining))
            payload = json.loads(
                request_with_backoff(http, url, policy).decode("utf-8", "replace")
            )
            meta = payload.get("meta") or {}
            if total == 0:
                total = meta.get("count") or 0
            page = payload.get("results") or []
            for work in page:
                records.append(self.map_work(work))
            remaining -= len(page)
            if not page:          # 空页不减少 remaining，必须显式退出以免死循环
                break
            cursor = meta.get("next_cursor")
        # 远端若返回多于请求的条数（或首页超发），按 --limit 截断
        records = records[:plan.limit]
        return ProviderResult(total=total, records=records, warnings=warnings)


class CrossrefProvider(Provider):
    name = "crossref"
    env_key = "CROSSREF_API_KEY"
    requires_api_key = False
    supports_cited_by_filter = False
    supports_relevance = False

    def structured_filters(self, plan: QueryPlan) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if plan.year:
            lo, _, hi = plan.year.partition("-")
            hi = hi or lo
            pairs.append(("from-pub-date", f"{lo.strip()}-01-01"))
            pairs.append(("until-pub-date", f"{hi.strip()}-12-31"))
        if plan.journal_issn:
            pairs.append(("issn", plan.journal_issn.strip()))
        if plan.work_type:
            pairs.append(("type", CROSSREF_TYPE_MAP.get(plan.work_type,
                                                        plan.work_type)))
        return pairs

    def build_url(self, query: str, plan: QueryPlan, rows: int, offset: int,
                  mailto: str | None) -> str:
        params: dict[str, str] = {
            "query": query,
            "rows": str(max(1, min(CROSSREF_MAX_ROWS, rows))),
            "offset": str(max(0, offset)),
        }
        filters = [f"{k}:{v}" for k, v in self.structured_filters(plan)]
        if filters:
            params["filter"] = ",".join(filters)
        if plan.author:
            params["query.author"] = plan.author.strip()
        if plan.sort == "citations":
            params["sort"] = "is-referenced-by-count"
            params["order"] = "desc"
        elif plan.sort == "year":
            params["sort"] = "published"
            params["order"] = "desc"
        if mailto:
            params["mailto"] = mailto
        return f"{API_CROSSREF}/works?{urllib.parse.urlencode(params)}"

    def map_item(self, item: dict) -> Record | None:
        titles = item.get("title") or []
        title = titles[0] if titles else None
        doi = norm_doi(item.get("DOI"))
        if not title and not doi:
            return None
        # date-parts 是 [[Y, M, D]]，取内层数组；没有就是空列表，避免 Optional 空值判断
        date: list[Any] = []
        for date_key in ("published", "issued"):
            block = item.get(date_key) or {}
            parts = block.get("date-parts") or []
            if parts and parts[0]:
                date = list(parts[0])
                break
        numbers = [int(v) for v in date if isinstance(v, int)]
        year = numbers[0] if numbers else None
        pub_date = None
        if numbers:
            month = numbers[1] if len(numbers) > 1 else 1
            day = numbers[2] if len(numbers) > 2 else 1
            pub_date = f"{numbers[0]:04d}-{month:02d}-{day:02d}"
        containers = item.get("container-title") or []
        authors: list[str] = []
        for author in item.get("author") or []:
            name = " ".join(
                part for part in (author.get("given"), author.get("family")) if part
            ).strip()
            if not name:
                name = (author.get("name") or "").strip()
            if name:
                authors.append(name)
        pdf_url = None
        for link in item.get("link") or []:
            if "pdf" in (link.get("content-type") or "").lower():
                pdf_url = link.get("URL")
                break
        return Record(
            doi=doi,
            title=title,
            year=year,
            publication_date=pub_date,
            venue=containers[0] if containers else None,
            work_type=item.get("type"),
            citation_count=item.get("is-referenced-by-count"),
            abstract=strip_jats(item.get("abstract")),
            authors=authors,
            is_open_access=None,
            open_access_pdf=pdf_url,
            language=item.get("language") or None,
            relevance_score=None,
            source=self.name,
            sources=[self.name],
            source_id=doi or "",
        )

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        warnings: list[str] = []
        local_filter_needed = plan.min_citations is not None or bool(plan.language)
        overfetch = min(
            CROSSREF_MAX_ROWS,
            plan.limit * 3 if local_filter_needed else plan.limit,
        )
        if plan.institution:
            warnings.append("crossref: --institution 不受支持，已忽略（该源无机构过滤）")
        if plan.oa_only:
            warnings.append("crossref: --oa-only 不受支持，已忽略（该源无 OA 布尔字段）")

        records: list[Record] = []
        total = 0
        offset = 0
        fetched = 0
        while fetched < overfetch:
            rows = min(CROSSREF_PAGE_SIZE, overfetch - fetched)
            url = self.build_url(query, plan, rows, offset, cfg.mailto)
            payload = json.loads(
                request_with_backoff(http, url, policy).decode("utf-8", "replace")
            )
            message = payload.get("message") or {}
            if total == 0:
                total = message.get("total-results") or 0
            items = message.get("items") or []
            if not items:
                break
            fetched += len(items)
            offset += len(items)
            for item in items:
                record = self.map_item(item)
                if record is not None:
                    records.append(record)
            if offset >= total or len(items) < rows:
                break

        if plan.min_citations is not None:
            before = len(records)
            records = [r for r in records
                       if (r.citation_count or 0) >= plan.min_citations]
            removed = before - len(records)
            if removed:
                warnings.append(
                    f"crossref: --min-citations 为本地过滤（该源不支持按被引过滤），"
                    f"剔除 {removed} 条"
                )
        if plan.language:
            wanted = plan.language.strip().lower()
            before = len(records)
            records = [r for r in records
                       if (r.language or "").lower().startswith(wanted)]
            removed = before - len(records)
            warnings.append(
                f"crossref: --language 为本地过滤，剔除 {removed} 条"
                "（该源 language 字段稀疏，召回不可靠）"
            )
        records = records[:plan.limit]
        return ProviderResult(total=total, records=records, warnings=warnings)


PROVIDERS: dict[str, Provider] = {
    OpenAlexProvider.name: OpenAlexProvider(),
    CrossrefProvider.name: CrossrefProvider(),
}


def search_all(plan: QueryPlan, cfg: "Config", http: Http,
               policy: RetryPolicy, sleep_fn: Callable[[float], None] = time.sleep,
               polite: float = POLITE_DELAY) -> tuple[list[Record], list[tuple[str, int]], list[str]]:
    records: list[Record] = []
    totals: list[tuple[str, int]] = []
    warnings: list[str] = []
    first = True
    for source_name in plan.sources:
        provider = PROVIDERS[source_name]
        for query in plan.queries:
            if not first and polite > 0:
                sleep_fn(polite)
            first = False
            result = provider.search(query, plan, http, policy, cfg)
            totals.append((f"{source_name} · {query}", result.total))
            warnings.extend(result.warnings)
            records.extend(result.records)
    # 同样的警告只报一次（逐查询会重复 N 遍）
    seen: set[str] = set()
    unique: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return records, totals, unique


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def format_authors(authors: list[str], limit: int = 3) -> str:
    if not authors:
        return ""
    if len(authors) <= limit:
        return "; ".join(authors)
    return f"{authors[0]} et al."


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Report, records: list[Record]) -> str:
    plan = report.plan
    today = _dt.date.today().isoformat()
    heading = plan.title or (plan.queries[0] if plan.queries else "文献清单")
    lines: list[str] = [f"# {heading}", "", f"> 最近更新：{today}。", ""]

    lines.append("## 检索说明")
    lines.append("")
    lines.append(f"- 数据来源：{'、'.join(plan.sources)}")
    lines.append(f"- 年份范围：{plan.year or '不限'}")
    lines.append(f"- 每个查询抓取上限：{plan.limit} 条")
    if plan.min_citations is not None:
        lines.append(f"- 最低被引数：{plan.min_citations}")
    for label, value in (("作者", plan.author), ("机构", plan.institution),
                         ("期刊 ISSN", plan.journal_issn), ("类型", plan.work_type),
                         ("语言", plan.language)):
        if value:
            lines.append(f"- {label}：{value}")
    if plan.oa_only:
        lines.append("- 仅开放获取")
    if plan.raw_filter:
        lines.append(f"- 原始 filter：`{plan.raw_filter}`")
    if plan.require:
        groups = "；".join(" 或 ".join(g) for g in plan.require)
        lines.append(f"- 相关性分组过滤（须命中全部组）：{groups}")
    if plan.exclude:
        lines.append(f"- 排除词：{'、'.join(plan.exclude)}")
    lines.append(f"- 排序：{plan.sort}")
    lines.append("")

    lines.append("各查询命中数（远端原始 total）：")
    lines.append("")
    for label, total in report.totals:
        lines.append(f"- `{label}` → {total} 篇")
    lines.append("")

    stats = report.stats
    lines.append("合并与过滤：")
    lines.append("")
    lines.append(f"- 抓取到 {stats.total_in} 条")
    if stats.doi_merged:
        lines.append(f"- 按 DOI 合并掉 {stats.doi_merged} 条（跨源同一文章）")
    if stats.title_merged:
        lines.append(f"- 按标题合并掉 {stats.title_merged} 条（同一论文的不同版本）")
    if stats.dropped_no_id:
        lines.append(f"- 丢弃 {stats.dropped_no_id} 条（既无 DOI 也无标题）")
    lines.append(f"- 过滤后保留 **{len(records)} 篇**（{plan.sort} 降序）")
    lines.append("")

    multi_source = len(plan.sources) > 1
    columns = ["#", "年份", "标题", "作者", "期刊", "被引", "DOI", "开放获取"]
    if multi_source:
        columns.append("来源")
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join(["---"] * len(columns)) + "|")
    for index, rec in enumerate(records, 1):
        cells = [
            str(index),
            _cell(rec.year),
            _cell(rec.title),
            _cell(format_authors(rec.authors)),
            _cell(rec.venue),
            _cell(rec.citation_count if rec.citation_count is not None else ""),
            _cell(rec.doi),
            "是" if rec.is_open_access else "",
        ]
        if multi_source:
            cells.append(_cell("/".join(rec.sources)))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_csv(records: list[Record]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(RECORD_FIELDS))
    for rec in records:
        row = []
        for name in RECORD_FIELDS:
            value = getattr(rec, name)
            if isinstance(value, list):
                value = "; ".join(str(v) for v in value)
            row.append("" if value is None else value)
        writer.writerow(row)
    return buf.getvalue()


def render_json(report: Report, records: list[Record]) -> str:
    plan = report.plan
    payload = {
        "version": __version__,
        "generated": _dt.date.today().isoformat(),
        "queries": [{"label": label, "total_count": total}
                    for label, total in report.totals],
        "filters": {
            "year": plan.year,
            "limit": plan.limit,
            "min_citations": plan.min_citations,
            "author": plan.author,
            "institution": plan.institution,
            "journal_issn": plan.journal_issn,
            "work_type": plan.work_type,
            "oa_only": plan.oa_only,
            "language": plan.language,
            "oa_filter": plan.raw_filter,
            "require": plan.require,
            "exclude": plan.exclude,
            "sources": plan.sources,
            "sort": plan.sort,
        },
        "merge_stats": {
            "total_in": report.stats.total_in,
            "doi_merged": report.stats.doi_merged,
            "title_merged": report.stats.title_merged,
            "dropped_no_id": report.stats.dropped_no_id,
            "total_out": report.stats.total_out,
        },
        "count": len(records),
        "results": [
            {"source": rec.source,
             "sources": rec.sources,
             "source_id": rec.source_id,
             "doi": rec.doi,
             "title": rec.title,
             "year": rec.year,
             "publication_date": rec.publication_date,
             "venue": rec.venue,
             "work_type": rec.work_type,
             "citation_count": rec.citation_count,
             "relevance_score": rec.relevance_score,
             "is_open_access": rec.is_open_access,
             "open_access_pdf": rec.open_access_pdf,
             "language": rec.language,
             "authors": rec.authors,
             "abstract": rec.abstract}
            for rec in records
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_doi_list(records: list[Record]) -> tuple[str, int]:
    """每行一个裸 DOI，无表头。返回 (文本, 无 DOI 而被排除的条数)。"""
    dois: list[str] = []
    missing = 0
    for rec in records:
        doi = norm_doi(rec.doi)
        if doi:
            dois.append(doi)
        else:
            missing += 1
    return "\n".join(dois) + ("\n" if dois else ""), missing


def query_slug(queries: list[str]) -> str:
    base = queries[0] if queries else "query"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    slug = slug[:48].strip("-")
    return slug or "query"


# --------------------------------------------------------------------------- #
# 配置与预设
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    api_keys: dict[str, str] = field(default_factory=dict)
    mailto: str | None = None
    output_dir: Path | None = None
    default_sources: list[str] | None = None
    presets: dict[str, dict] = field(default_factory=dict)
    config_path: Path | None = None


def user_config_path() -> Path | None:
    if os.name == "nt":
        base = os.getenv("APPDATA")
        return Path(base) / "litsearch" / "config.toml" if base else None
    return Path.home() / ".config" / "litsearch" / "config.toml"


def parse_env_file(path: Path) -> dict[str, str]:
    """.env 是扁平 KEY=VALUE（tomllib 读不了），仅提供扁平回退。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def _first_not_none(*candidates: Any) -> Any:
    for candidate in candidates:
        if candidate is not None and candidate != "":
            return candidate
    return None


def load_config(cwd: Path, env: dict[str, str] | None = None) -> Config:
    """逐键取首个命中：环境变量 → LITSEARCH_CONFIG → 用户级 config.toml
    → 项目根 litsearch.toml → 项目 .env。"""
    env = dict(os.environ if env is None else env)

    candidates: list[Path] = []
    override = env.get("LITSEARCH_CONFIG")
    if override:
        candidates.append(Path(override))
    resolved = user_config_path()
    if resolved:
        candidates.append(resolved)
    candidates.append(cwd / "litsearch.toml")

    documents: list[tuple[Path, dict]] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            documents.append((path, tomllib.loads(path.read_text(encoding="utf-8"))))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"配置文件无法解析：{path}（{exc}）") from exc

    def scalar(toml_key: str) -> Any:
        for _, candidate in documents:      # documents 已按优先级降序
            found = candidate.get(toml_key)
            if found is not None:
                return found
        return None

    api_section: dict[str, Any] = {}
    for _, loaded in documents:
        for source, credential in (loaded.get("api_keys") or {}).items():
            api_section.setdefault(source, credential)

    presets: dict[str, dict] = {}
    for _, loaded in reversed(documents):   # 低优先级先写，高优先级覆盖同名预设
        presets.update(loaded.get("presets") or {})

    dotenv = parse_env_file(cwd / ".env")

    api_keys: dict[str, str] = {}
    for name, provider in PROVIDERS.items():
        value = _first_not_none(
            env.get(provider.env_key),
            api_section.get(name),
            dotenv.get(provider.env_key),
        )
        if value:
            api_keys[name] = value

    mailto = _first_not_none(
        env.get("CROSSREF_MAILTO"), env.get("LITSEARCH_MAILTO"),
        scalar("mailto"), dotenv.get("CROSSREF_MAILTO"),
    )
    output_dir_raw = _first_not_none(
        env.get("LITSEARCH_OUTPUT_DIR"),
        scalar("output_dir"),
        dotenv.get("LITSEARCH_OUTPUT_DIR"),
    )
    default_sources: list[str] | None = None
    sources_default = scalar("default_sources")
    if sources_default:
        default_sources = list(sources_default)

    return Config(
        api_keys=api_keys,
        mailto=str(mailto) if mailto else None,
        output_dir=Path(str(output_dir_raw)).expanduser() if output_dir_raw else None,
        default_sources=default_sources,
        presets=presets,
        config_path=documents[0][0] if documents else None,
    )


def missing_key_message(provider: Provider) -> str:
    return (
        f"缺少 {provider.name} 的 API key。\n{provider.key_help}"
    )


def check_keys(sources: list[str], cfg: Config) -> None:
    for name in sources:
        provider = PROVIDERS[name]
        if provider.requires_api_key and not cfg.api_keys.get(name):
            raise ConfigError(missing_key_message(provider))


PRESET_ALIASES = {"type": "work_type", "queries": "queries", "source": "sources"}


def _preset_lookup(preset: dict | None, key: str) -> Any:
    if not preset:
        return None
    names = [key] + [alias for alias, target in PRESET_ALIASES.items()
                     if target == key]
    names += [alias for alias, target in PRESET_ALIASES.items() if alias == key]
    for name in names:
        if name in preset and preset[name] is not None:
            return preset[name]
    return None


def _resolve(cli_value: Any, preset: dict | None, key: str, default: Any) -> Any:
    if cli_value is not None:
        return cli_value
    from_preset = _preset_lookup(preset, key)
    return default if from_preset is None else from_preset


def build_plan(args: argparse.Namespace, preset: dict | None,
               cfg: Config) -> QueryPlan:
    # nargs="*" 的位置参数在缺省时给的是 []，必须当成「未给出」才能让 preset 生效
    cli_queries = args.query if args.query else None
    queries = list(_resolve(cli_queries, preset, "queries", []) or [])
    for raw in queries:
        if not str(raw).strip():
            raise UsageError("检索词为空")
    if not queries:
        raise UsageError("缺少检索词；或使用 --preset 指定配置中的命名预设")

    sources_raw = _resolve(args.source, preset, "sources", None)
    if sources_raw is None:
        sources_raw = cfg.default_sources or ["openalex"]
    if isinstance(sources_raw, str):
        raw_list = [s.strip().lower() for s in sources_raw.split(",") if s.strip()]
    else:
        raw_list = [str(s).strip().lower() for s in sources_raw if str(s).strip()]
    sources: list[str] = []
    for name in raw_list:                     # 去重但保序，避免重复发起请求
        if name not in sources:
            sources.append(name)
    unknown = [s for s in sources if s not in PROVIDERS]
    if unknown:
        raise UsageError(
            f"未知数据源：{', '.join(unknown)}（可用：{', '.join(PROVIDERS)}）"
        )
    if not sources:
        raise UsageError("至少需要一个数据源")

    limit_raw = _resolve(args.limit, preset, "limit", 25)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"--limit 必须是整数：{limit_raw!r}") from exc
    if limit < 1:
        raise UsageError("--limit 必须 >= 1")

    min_cit_raw = _resolve(args.min_citations, preset, "min_citations", None)
    min_citations = None
    if min_cit_raw is not None:
        try:
            min_citations = int(min_cit_raw)
        except (TypeError, ValueError) as exc:
            raise UsageError(f"--min-citations 必须是整数：{min_cit_raw!r}") from exc

    year = _resolve(args.year, preset, "year", None)
    if year is not None:
        year = str(year).strip()
        if not re.fullmatch(r"\d{4}(\s*-\s*\d{4})?", year):
            raise UsageError(f"--year 格式应为 2024 或 2021-2026：{year!r}")
        year = re.sub(r"\s+", "", year)

    require_specs = _resolve(args.require, preset, "require", []) or []
    exclude_specs = _resolve(args.exclude, preset, "exclude", []) or []
    require_groups: list[list[str]] = []
    for spec in require_specs:
        require_groups.extend(_split_groups(str(spec)))
    exclude_terms: list[str] = []
    for spec in exclude_specs:
        exclude_terms.extend(_split_or(str(spec)))

    return QueryPlan(
        queries=[str(q) for q in queries],
        sources=sources,
        year=year,
        limit=limit,
        min_citations=min_citations,
        author=_resolve(args.author, preset, "author", None),
        institution=_resolve(args.institution, preset, "institution", None),
        journal_issn=_resolve(args.journal_issn, preset, "journal_issn", None),
        work_type=_resolve(args.work_type, preset, "work_type", None),
        oa_only=bool(_resolve(args.oa_only, preset, "oa_only", False)),
        language=_resolve(args.language, preset, "language", None),
        raw_filter=_resolve(args.oa_filter, preset, "oa_filter", None),
        require=require_groups,
        exclude=exclude_terms,
        sort=str(_resolve(args.sort, preset, "sort", "citations")),
        title=_resolve(args.title, preset, "title", None),
    )


# --------------------------------------------------------------------------- #
# 输出路径
# --------------------------------------------------------------------------- #

def resolve_outputs(args: argparse.Namespace, cfg: Config, plan: QueryPlan,
                    cwd: Path | None = None) -> tuple[Path | None, Path | None]:
    """返回 (主输出路径, DOI 列表路径)；--stdout 时两者均为 None。"""
    if args.stdout:
        return None, None

    base_dir = Path.cwd() if cwd is None else cwd
    out_dir = cfg.output_dir or (base_dir / "output")
    if args.output:
        main_path = Path(args.output).expanduser()
    else:
        stamp = _dt.date.today().isoformat()
        main_path = out_dir / f"litsearch_{query_slug(plan.queries)}_{stamp}.md"

    if args.doi_list:
        doi_path = Path(args.doi_list).expanduser()
    else:
        doi_path = main_path.with_name(main_path.stem + ".doi.txt")
    return main_path, doi_path


def write_outputs(main_path: Path | None, doi_path: Path | None,
                  main_text: str, doi_text: str) -> list[Path]:
    written: list[Path] = []
    for path, text in ((main_path, main_text), (doi_path, doi_text)):
        if path is None:
            continue
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        written.append(path)
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

EPILOG = """\
过滤语法：
  --require "a,b" --require "c,d"   组内 OR、组间 AND，须命中全部组
  --exclude "x,y"                   命中任一即剔除

默认输出：
  <output_dir>/litsearch_<检索词slug>_<日期>.md   人读清单
  <同目录>/litsearch_<检索词slug>_<日期>.doi.txt  每行一个裸 DOI，供 Zotero 等导入
  output_dir 取自配置，未配置则为 <当前目录>/output

示例：
  litsearch "marine protist prokaryote interaction" --year 2021-2026 --limit 30
  litsearch --preset marine_amplicon --stdout
  litsearch "grazing nanoflagellate" --source openalex,crossref --oa-only
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litsearch",
        description="多源文献检索工具（OpenAlex + Crossref），输出人读清单与 DOI 列表",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument("query", nargs="*", default=None,
                        help="自由文本检索词，可给多个（合并去重）；可用 --preset 代替")
    parser.add_argument("--preset", default=None,
                        help="使用配置文件 [presets] 中的命名检索预设")
    parser.add_argument("--source", default=None,
                        help="数据源，逗号分隔（默认 openalex）；可选 openalex,crossref")
    parser.add_argument("--year", default=None, help="年份过滤：2024 或 2021-2026")
    parser.add_argument("--limit", type=int, default=None,
                        help="每个查询抓取条数上限（默认 25，可超过 100，自动分页）")
    parser.add_argument("--min-citations", type=int, default=None, help="最低被引数")
    parser.add_argument("--author", default=None, help="作者（姓名或 OpenAlex 作者 ID）")
    parser.add_argument("--institution", default=None,
                        help="机构（名称、OpenAlex 机构 ID 或 ROR 链接）")
    parser.add_argument("--journal-issn", default=None, help="期刊 ISSN")
    parser.add_argument("--type", dest="work_type", default=None,
                        help="文献类型，如 article / preprint / review")
    parser.add_argument("--language", default=None, help="语言，如 en / zh")
    parser.add_argument("--oa-only", action=argparse.BooleanOptionalAction,
                        default=None, help="仅开放获取（--no-oa-only 关闭）")
    parser.add_argument("--oa-filter", default=None, metavar="STR",
                        help="直接透传给 OpenAlex 的原始 filter 字符串（逃生舱，优先级最高）")
    parser.add_argument("--require", action="append", default=None, metavar="A,B",
                        help="相关性分组过滤，可重复；组内逗号分隔为 OR，组间为 AND")
    parser.add_argument("--exclude", action="append", default=None, metavar="X,Y",
                        help="排除词，可重复；命中任一即剔除")
    parser.add_argument("--sort", choices=["citations", "year", "relevance"],
                        default=None, help="排序（默认 citations）")
    parser.add_argument("--title", default=None, help="Markdown 清单标题")
    parser.add_argument("--output", default=None,
                        help="覆盖主输出路径；后缀 .md/.csv/.json 决定格式")
    parser.add_argument("--doi-list", dest="doi_list", default=None,
                        help="覆盖 DOI 列表输出路径")
    parser.add_argument("--stdout", action="store_true",
                        help="把 JSON 打到 stdout，不写任何文件")
    parser.add_argument("--version", action="version",
                        version=f"litsearch {__version__}")
    return parser


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 输出，避免非 ASCII 字符报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def render_main(main_path: Path | None, report: Report,
                records: list[Record]) -> str:
    suffix = main_path.suffix.lower() if main_path else ".md"
    if suffix == ".csv":
        return render_csv(records)
    if suffix == ".json":
        return render_json(report, records)
    return render_markdown(report, records)


def run(args: argparse.Namespace, cwd: Path, http: Http,
        policy: RetryPolicy, sleep_fn: Callable[[float], None]) -> int:
    if args.stdout and (args.output or args.doi_list):
        raise UsageError("--stdout 与 --output / --doi-list 不能同时使用")

    cfg = load_config(cwd)
    preset_name = args.preset
    preset = None
    if preset_name:
        preset = cfg.presets.get(preset_name)
        if preset is None:
            available = "、".join(sorted(cfg.presets)) or "（无）"
            raise ConfigError(
                f"配置中没有名为 {preset_name!r} 的预设；已定义：{available}"
            )

    plan = build_plan(args, preset, cfg)
    check_keys(plan.sources, cfg)

    raw_records, totals, warnings = search_all(
        plan, cfg, http, policy, sleep_fn=sleep_fn
    )
    merged, stats = merge_records(raw_records)
    filtered = apply_filters(merged, plan.require, plan.exclude)
    ordered = sort_records(filtered, plan.sort)
    report = Report(plan=plan, totals=totals, stats=stats)

    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)

    if args.stdout:
        print(render_json(report, ordered))
        print(
            f"[ok] 共 {len(ordered)} 篇（抓取 {stats.total_in} 条，"
            f"DOI 合并 {stats.doi_merged}，标题合并 {stats.title_merged}）",
            file=sys.stderr,
        )
        return EXIT_OK

    main_path, doi_path = resolve_outputs(args, cfg, plan, cwd)
    doi_text, missing_doi = render_doi_list(ordered)
    written = write_outputs(
        main_path, doi_path,
        render_main(main_path, report, ordered), doi_text,
    )
    if missing_doi:
        print(f"[warn] {missing_doi} 条无 DOI，未写入 DOI 列表", file=sys.stderr)
    for path in written:
        print(str(path))
    print(
        f"[ok] 共 {len(ordered)} 篇（抓取 {stats.total_in} 条，"
        f"DOI 合并 {stats.doi_merged}，标题合并 {stats.title_merged}，"
        f"丢弃无标识 {stats.dropped_no_id}）",
        file=sys.stderr,
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_ARGS

    http = Http()
    policy = RetryPolicy()
    try:
        return run(args, Path.cwd(), http, policy, time.sleep)
    except UsageError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_ARGS
    except ConfigError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except NetworkError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_NET
    except KeyboardInterrupt:
        print("[error] 已中断", file=sys.stderr)
        return EXIT_NET


if __name__ == "__main__":
    sys.exit(main())
