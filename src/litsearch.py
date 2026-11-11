#!/usr/bin/env python3
"""多源文献检索，Python 3.12+ 标准库运行时。

CLI：python litsearch.py --help
Python：SearchClient(Config(...)).search(QueryPlan(...))
结果走 stdout，诊断走 stderr；退出码 0/2/3/4/5 分别对应成功、参数、配置、网络和输出。
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import functools
import hashlib
import html
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, fields, replace
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

__version__ = "0.5.0"

__all__ = [
    "Config", "QueryPlan", "Record", "SearchClient", "SearchResult", "Report",
    "Diagnostic", "QueryExecution", "Http", "RetryPolicy", "LitsearchError",
    "UsageError", "ConfigError", "NetworkError", "OutputError", "load_config", "main",
]

API_OPENALEX = "https://api.openalex.org"
API_CROSSREF = "https://api.crossref.org"
API_EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_PER_PAGE = 100
CROSSREF_PAGE_SIZE = 100
CROSSREF_MAX_ROWS = 1000
ABSTRACT_BATCH_SIZE = 10
POLITE_DELAY = 0.5
USER_AGENT = f"litsearch/{__version__}"
DEFAULT_LIMIT = 25
DEFAULT_SORT = "citations"
DEFAULT_FETCH_SORT = "relevance"
DEFAULT_MERGE_POLICY = "title"
SCHEMA_VERSION = 1

OPENALEX_SELECT = (
    "id,doi,title,publication_year,publication_date,cited_by_count,"
    "primary_location,authorships,abstract_inverted_index,open_access,type,"
    "relevance_score,language,best_oa_location"
)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# 归一化标题短于此长度时不参与标题合并：'Editorial'、'Erratum'、'Correction'、
# 'Front matter'、'Author index' 这类通用标题会把毫不相干的论文错误并成一条
# （静默丢数据）。取 15 是因为 'deepseaprotists'（Deep sea protists）这类真实短标题
# 归一化后正好 15，要保住它们；而上面那些通用标题都短于 15。
TITLE_MERGE_MIN_CHARS = 15

# 传输层瞬时故障，值得重试：连接被截断（IncompleteRead 是 HTTPException 的子类）、
# 超时、DNS 失败等。用具名导入而不是 import http.client，避免与形参 http 撞名。
TRANSPORT_ERRORS = (
    urllib.error.URLError, HTTPException, TimeoutError, OSError,
)

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_CONFIG = 3
EXIT_NET = 4
EXIT_OUTPUT = 5

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

CROSSREF_SUPPORTED_TYPES = frozenset({
    "book", "book-chapter", "book-part", "book-section", "book-series", "book-set",
    "book-track", "component", "database", "dataset", "dissertation", "edited-book",
    "grant", "journal", "journal-article", "journal-issue", "journal-volume",
    "monograph", "other", "peer-review", "posted-content", "proceedings",
    "proceedings-article", "proceedings-series", "reference-book", "reference-entry",
    "report", "report-component", "report-series", "standard",
})

UNSUPPORTED_FLAG_LABELS = {
    "institution": "--institution",
    "oa_only": "--oa-only",
    "raw_filter": "--oa-filter",
    "work_type": "--type",
    "fetch_sort": "--fetch-sort",
    "author": "--author OpenAlex ID",
}

# 远端抓取排序：决定「取到哪一批候选」。默认 relevance 即来源自身的相关度序。
# 与 QueryPlan.sort（只改展示顺序）是两个独立旋钮 —— 只设 --sort citations 时，
# 被引排序只作用在「相关度最高的 N 条」这个切片内部，并不是全网最高被引的 N 条。
FETCH_SORT_CHOICES = ("relevance", "citations", "year")
SORT_CHOICES = (*FETCH_SORT_CHOICES, "rrf", "bm25", "hybrid")
RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75
TITLE_WEIGHT = 2.0
ANCILLARY_FACTOR = 0.5
FETCH_SORT_OPENALEX = {
    "citations": "cited_by_count:desc",
    "year": "publication_date:desc",
}
FETCH_SORT_CROSSREF = {
    "relevance": "score",
    "citations": "is-referenced-by-count",
    "year": "published",
}

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


class OutputError(LitsearchError):
    """输出失败；written 保存本次已经提交的文件。"""

    def __init__(self, message: str, written: Iterable[Path] = ()) -> None:
        super().__init__(message)
        self.written = list(written)


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
    source_ids: dict[str, list[str]] = field(default_factory=dict)
    doi_aliases: list[str] = field(default_factory=list)
    raw_work_types: dict[str, list[str]] = field(default_factory=dict)
    landing_page_url: str | None = None
    pdf_url: str | None = None
    oa_url: str | None = None
    merge_reasons: list[str] = field(default_factory=list)
    related_dois: list[str] = field(default_factory=list)
    abstract_source: str | None = None
    retrieval_hits: list[dict[str, Any]] = field(default_factory=list)
    ranking_score: float | None = None
    ranking_evidence: dict[str, Any] = field(default_factory=dict)


RECORD_FIELDS = tuple(f.name for f in fields(Record))

# 合并时用非空值回填的字段（sources 单独并集处理）
MERGE_COMBINED_FIELDS = frozenset({
    "sources", "source_ids", "doi_aliases", "raw_work_types", "merge_reasons", "related_dois",
    "source", "source_id", "retrieval_hits", "ranking_score", "ranking_evidence",
})
MERGE_FILL_FIELDS = tuple(name for name in RECORD_FIELDS if name not in MERGE_COMBINED_FIELDS)


@dataclass(slots=True)
class MergeStats:
    total_in: int = 0
    dropped_no_id: int = 0
    no_doi: int = 0
    doi_merged: int = 0
    title_merged: int = 0
    total_out: int = 0
    source_id_merged: int = 0


@dataclass(slots=True)
class FilterStats:
    """相关性过滤的分阶段计数。

    每个 require 组单独统计「只靠这一组能留下多少条」，输入集合相同，所以能直接看出
    是哪个组把结果卡掉了 —— 比只看最终一个数字好排查得多。
    """

    before: int = 0
    excluded: int = 0
    passed_exclude: int = 0
    per_group: list[int] = field(default_factory=list)
    after: int = 0


@dataclass(slots=True)
class Diagnostic:
    code: str
    message: str
    source: str | None = None
    query: str | None = None
    field: str | None = None


@dataclass(slots=True)
class QueryExecution:
    source: str
    query: str
    remote_total: int
    fetched_count: int
    mapped_count: int
    returned_count: int
    stop_reason: str
    filter_execution: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderResult:
    total: int
    records: list[Record]
    warnings: list[str] = field(default_factory=list)
    fetched_count: int = 0
    mapped_count: int = 0
    stop_reason: str = "exhausted"
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass(slots=True)
class QueryPlan:
    """统一检索意图，由检索入口校验和规范化。"""

    queries: list[str]
    sources: list[str]
    year: str | None = None
    limit: int = DEFAULT_LIMIT
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
    sort: str = DEFAULT_SORT
    fetch_sort: str = DEFAULT_FETCH_SORT
    max_results: int | None = None
    title: str | None = None
    strict_filters: bool = False
    merge_policy: str = DEFAULT_MERGE_POLICY
    enrich_abstracts: int = 0
    concepts: list[list[str]] = field(default_factory=list)


@dataclass(slots=True)
class EnrichmentStats:
    requested: int = 0
    cache_hits: int = 0
    filled: int = 0
    not_found: int = 0
    budget_skipped: int = 0


@dataclass(slots=True)
class Report:
    plan: QueryPlan
    totals: list[tuple[str, int]]
    stats: MergeStats
    filters: FilterStats = field(default_factory=FilterStats)
    truncated: int = 0
    queries: list[QueryExecution] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    generated: str = field(default_factory=lambda: _dt.date.today().isoformat())
    enrichment: EnrichmentStats = field(default_factory=EnrichmentStats)


@dataclass(slots=True)
class SearchResult:
    records: list[Record]
    report: Report

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def version(self) -> str:
        return __version__

    def to_dict(self) -> dict[str, Any]:
        return _result_payload(self.report, self.records)


# --------------------------------------------------------------------------- #
# HTTP 与重试
# --------------------------------------------------------------------------- #

@dataclass
class Http:
    """urllib 薄封装。opener 可注入，便于测试完全离线。"""

    user_agent: str = USER_AGENT
    timeout: float = 30.0
    opener: Callable[..., Any] = urllib.request.urlopen
    polite: float = POLITE_DELAY
    sleep: Callable[[float], None] = time.sleep
    _has_requested: bool = field(default=False, init=False, repr=False)

    def get_bytes(self, url: str) -> bytes:
        if self._has_requested and self.polite > 0:
            self.sleep(self.polite)
        self._has_requested = True
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        with self.opener(req, timeout=self.timeout) as resp:
            return resp.read()

@dataclass
class RetryPolicy:
    attempts: int = 4
    base: float = 1.0
    cap: float = 30.0
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time

    def delay(self, attempt: int) -> float:
        return min(self.cap, self.base * (2 ** attempt))

    def retry_after(self, value: str | None) -> float:
        if not value:
            return 0.0
        try:
            seconds = float(value)
            return max(0.0, seconds) if math.isfinite(seconds) else 0.0
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - self.now())
            except (TypeError, ValueError, OverflowError):
                return 0.0


def _redact_credentials(message: str) -> str:
    return re.sub(r"(?i)(api[_-]?key|access_token|token|mailto)(=|%3[dD])[^&\s'\"]+",
                  r"\1\2[redacted]", message)


def request_with_backoff(http: Http, url: str, policy: RetryPolicy) -> bytes:
    """对 429/5xx 做指数退避重试；4xx（除 429）直接失败。"""
    last: Exception | None = None
    for attempt in range(max(1, policy.attempts)):
        retry_after = 0.0
        try:
            return http.get_bytes(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS:
                hint = ""
                if exc.code in (401, 403):
                    hint = "（请检查 API key 是否有效）"
                raise NetworkError(f"HTTP {exc.code}: {_redact_credentials(str(exc.reason))}{hint}") from exc
            last = exc
            retry_after = policy.retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        except TRANSPORT_ERRORS as exc:
            last = exc
        if attempt + 1 < max(1, policy.attempts):
            policy.sleep(max(policy.delay(attempt), retry_after))
    hint = ""
    if isinstance(last, urllib.error.HTTPError) and last.code == 429:
        hint = "；429 通常是超出每日预算或访问过频，请检查 API key 是否有效"
    raise NetworkError(f"请求失败（已尝试 {max(1, policy.attempts)} 次）："
                       f"{_redact_credentials(str(last))}{hint}")


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"非法 JSON 数值：{token}")


def decode_json(payload: bytes, source: str) -> dict:
    """外部响应必须是 UTF-8 编码的 JSON object。"""
    try:
        decoded = json.loads(payload.decode("utf-8"), parse_constant=_reject_json_constant)
        if not isinstance(decoded, dict):
            raise NetworkError(f"{source}: 响应根节点必须是 JSON object")
        return decoded
    except (ValueError, UnicodeDecodeError) as exc:
        raise NetworkError(
            f"{source}: 远端返回的不是合法 JSON（{exc}）"
        ) from exc


# --------------------------------------------------------------------------- #
# 摘要处理
# --------------------------------------------------------------------------- #

class _PlainTextParser(HTMLParser):
    _blocks = frozenset({"p", "div", "br", "sec", "title", "list", "list-item",
                         "abstract", "section", "h1", "h2", "h3", "h4", "h5", "h6",
                         "ul", "ol", "li", "table", "tr", "td", "th"})
    _inline = frozenset({"i", "b", "em", "strong", "sub", "sup", "italic", "bold",
                         "roman", "sc", "span", "a", "xref", "ext-link", "underline"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.removeprefix("jats:")
        if name in self._blocks:
            self.parts.append(" ")
        elif name not in self._inline:
            self.parts.append(self.get_starttag_text() or f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        name = tag.removeprefix("jats:")
        if name in self._blocks:
            self.parts.append(" ")
        elif name not in self._inline:
            self.parts.append(f"</{tag}>")


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
    """解一层编码标签并提取已知 HTML/JATS 标签文本；保留科学比较符。"""
    if not text:
        return None
    parser = _PlainTextParser()
    parser.feed(html.unescape(text))
    parser.close()
    plain = "".join(parser.parts)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain or None


# --------------------------------------------------------------------------- #
# 归一化与合并
# --------------------------------------------------------------------------- #

def norm_title(title: str | None) -> str | None:
    """标题归一化：小写、去掉所有非字母数字。"""
    normalized = "".join(
        char for char in unicodedata.normalize("NFKC", title or "").casefold()
        if char.isalnum()
    )
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


def _merge_pair(a: Record, b: Record, reason: str) -> Record:
    base, other = (a, b) if _wins(a, b) else (b, a)
    out = replace(base)
    out.authors = list(base.authors)
    out.sources = sorted(set(base.sources) | set(other.sources))
    for name in ("source_ids", "raw_work_types"):
        left, right = getattr(base, name), getattr(other, name)
        setattr(out, name, {source: sorted(set(left.get(source, [])) | set(right.get(source, [])))
                            for source in left.keys() | right.keys()})
    for name in ("doi_aliases", "merge_reasons", "related_dois"):
        setattr(out, name, sorted(set(getattr(base, name)) | set(getattr(other, name))))
    out.merge_reasons = sorted(set(out.merge_reasons) | {reason})
    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in [*base.retrieval_hits, *other.retrieval_hits]:
        key = (hit["source"], hit["query"])
        if key not in hits or hit["rank"] < hits[key]["rank"]:
            hits[key] = dict(hit)
    out.retrieval_hits = [hits[key] for key in sorted(hits)]
    for name in MERGE_FILL_FIELDS:
        if _is_empty(getattr(out, name)) and not _is_empty(getattr(other, name)):
            setattr(out, name, getattr(other, name))
    if not out.authors and other.authors:
        out.authors = list(other.authors)
    if base.abstract:
        out.abstract_source = base.abstract_source or base.source or None
    elif other.abstract:
        out.abstract_source = other.abstract_source or other.source or None
    if out.relevance_score is None:
        out.relevance_score = other.relevance_score
    elif other.relevance_score is not None:
        out.relevance_score = max(out.relevance_score, other.relevance_score)
    return out


def _collapse(records: Iterable[Record],
              keyfn: Callable[[Record], str | None], reason: str) -> list[Record]:
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
            merged[key] = _merge_pair(merged[key], rec, reason)
        else:
            merged[key] = rec
            order.append(key)
    return [merged[k] for k in order] + passthrough


def title_merge_key(rec: Record) -> str | None:
    """标题合并用的键。太短的通用标题返回 None，不参与合并、原样保留。"""
    key = norm_title(rec.title)
    if key is None or len(key) < TITLE_MERGE_MIN_CHARS:
        return None
    return key


def _record_with_provenance(record: Record) -> Record:
    copied = Record(**asdict(record))
    if copied.abstract and not copied.abstract_source:
        copied.abstract_source = copied.source or None
    if copied.source and copied.source_id:
        identifiers = copied.source_ids.setdefault(copied.source, [])
        if copied.source_id not in identifiers:
            identifiers.append(copied.source_id)
    doi = norm_doi(copied.doi)
    if doi and doi not in copied.doi_aliases:
        copied.doi_aliases.append(doi)
    return copied


def merge_records(records: list[Record], policy: str = "title") -> tuple[list[Record], MergeStats]:
    """按 DOI、来源身份去重，并按策略处理标题合并或关联。"""
    stats = MergeStats(total_in=len(records))
    if policy not in ("title", "doi"):
        raise UsageError("merge_policy 必须是 title 或 doi")
    kept = [r for r in records if norm_doi(r.doi) or norm_title(r.title)
            or (r.source and r.source_id)]
    stats.dropped_no_id = len(records) - len(kept)
    stats.no_doi = sum(1 for r in kept if norm_doi(r.doi) is None)

    pool = [_record_with_provenance(r) for r in kept]
    while True:
        before = len(pool)
        pool = _collapse(pool, lambda r: norm_doi(r.doi), "doi")
        stats.doi_merged += before - len(pool)
        mid = len(pool)
        pool = _collapse(pool, lambda r: f"{r.source}:{r.source_id}"
                         if r.source and r.source_id else None, "source_id")
        stats.source_id_merged += mid - len(pool)
        if policy == "title":
            mid = len(pool)
            pool = _collapse(pool, title_merge_key, "title")
            stats.title_merged += mid - len(pool)
        if len(pool) == before:
            break
    stats.total_out = len(pool)
    if policy == "doi":
        by_title: dict[str, list[Record]] = {}
        for record in pool:
            key = title_merge_key(record)
            if key:
                by_title.setdefault(key, []).append(record)
        for group in by_title.values():
            if len(group) < 2:
                continue
            aliases = {doi for record in group for doi in record.doi_aliases}
            for record in group:
                record.related_dois = sorted(set(record.related_dois) | (aliases - set(record.doi_aliases)))
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


# 把标点（连字符、斜杠、括号…）统一成空格，这样检索词 'nucleic acid content'
# 也能匹配上标题里写作 'Nucleic Acid-Content' 的论文。\w 在 str 模式下含 CJK，
# 所以中文本不会被拆开。
_WORD_SPLIT_RE = re.compile(r"\W+", re.UNICODE)


def _normalize_text(text: str) -> str:
    return _WORD_SPLIT_RE.sub(" ", text.lower()).strip()


@functools.lru_cache(maxsize=None)
def _compile_term(term: str) -> re.Pattern[str] | None:
    """把单个检索词编译成匹配模式。

    - 不加修饰：词首前缀匹配。'protist' 命中 protists，'sea' 不命中
      research / disease / increase（这些 'sea' 出现在词中而非词首）。
    - '=词'   ：整词（或整短语）精确匹配。短词、易混词用这个，如 '=sea'
      只命中独立的 sea，不命中 seasonality / seawater。
    - '*词*'  ：任意位置子串匹配。生物学的复合词
      （cyanobacteria、bacterioplankton）需要它。
    - 含非 ASCII（中文等无空格文本）：退回子串匹配。
    """
    raw = term.strip().lower()
    if not raw:
        return None
    if not raw.isascii():
        return re.compile(re.escape(raw))
    if raw.startswith("*") and raw.endswith("*") and len(raw) > 2:
        body = _normalize_text(raw.strip("*"))
        return re.compile(re.escape(body)) if body else None
    exact = raw.startswith("=")
    if exact:
        raw = raw[1:]
    body = _normalize_text(raw)
    if not body:
        return None
    return re.compile(r"(?<!\w)" + re.escape(body) + (r"(?!\w)" if exact else r"\w*"))


def _term_hits(term: str, normalized: str) -> bool:
    pattern = _compile_term(term)
    return pattern is not None and pattern.search(normalized) is not None


def matches(text: str, require_groups: list[list[str]],
            exclude_terms: list[str]) -> bool:
    """须命中所有 require 组（组内任一），且不命中任一 exclude 词。

    在归一化后的文本上匹配（标点→空格、小写），单词匹配规则见 _compile_term。
    """
    normalized = _normalize_text(text)
    if exclude_terms and any(_term_hits(t, normalized) for t in exclude_terms):
        return False
    for group in require_groups:
        if not any(_term_hits(t, normalized) for t in group):
            return False
    return True


def _record_text(rec: Record) -> str:
    return f"{rec.title or ''} {rec.abstract or ''}"


def _split_field_term(term: str) -> tuple[str, str]:
    scope, separator, body = term.partition(":")
    return (scope, body) if separator and scope in {"title", "abstract"} else ("any", term)


def _text_fields(record: Record) -> dict[str, str]:
    return {"title": _normalize_text(record.title or ""),
            "abstract": _normalize_text(record.abstract or ""),
            "any": _normalize_text(_record_text(record))}


def _field_hits(term: str, texts: dict[str, str]) -> bool:
    scope, body = _split_field_term(term)
    return _term_hits(body, texts[scope])


def apply_filters(records: list[Record], require_groups: list[list[str]],
                  exclude_terms: list[str]) -> tuple[list[Record], FilterStats]:
    """在合并后的集合上执行 —— 绝不逐源执行，否则跨源召回不一致。

    同时返回分阶段计数：每个 require 组都在**同一个输入集合**上单独统计，
    所以能直接看出是哪个组把结果卡掉的，不必再写脚本排查。
    """
    stats = FilterStats(before=len(records))
    if not require_groups and not exclude_terms:
        stats.passed_exclude = stats.after = len(records)
        return list(records), stats

    texts = [_text_fields(rec) for rec in records]
    excluded = [any(_field_hits(term, text) for term in exclude_terms) for text in texts]
    stats.excluded = sum(excluded)
    stats.passed_exclude = len(records) - stats.excluded
    for group in require_groups:
        stats.per_group.append(sum(
            1 for flag, text in zip(excluded, texts)
            if not flag and any(_field_hits(term, text) for term in group)
        ))
    out = [rec for rec, text, flag in zip(records, texts, excluded)
           if not flag and all(any(_field_hits(term, text) for term in group)
                               for group in require_groups)]
    stats.after = len(out)
    return out, stats


def _sort_key(sort: str) -> Callable[[Record], tuple]:
    if sort == "year":
        return lambda r: (r.year or 0, r.citation_count or 0)
    if sort == "relevance":
        # 缺少来源相关分的记录排到后段，内部按被引。
        def key(r: Record) -> tuple:
            if r.relevance_score is not None:
                return (1, float(r.relevance_score))
            return (0, float(r.citation_count or 0))
        return key
    return lambda r: (r.citation_count or 0, r.year or 0)


def sort_records(records: list[Record], sort: str) -> list[Record]:
    return sorted(records, key=_sort_key(sort), reverse=True)


def _ranking_groups(plan: QueryPlan) -> list[list[str]]:
    if plan.concepts:
        return plan.concepts
    if plan.require:
        return plan.require
    stopwords = {"and", "or", "not", "the", "of", "in", "for", "on", "a", "an", "with"}
    terms = dict.fromkeys(_normalize_text(" ".join(plan.queries)).split())
    return [[term] for term in terms if term not in stopwords]


def _lexical_scores(records: list[Record], groups: list[list[str]]) -> tuple[list[float], list[dict]]:
    texts = [_text_fields(record) for record in records]
    lengths = [{field: len(text[field].split()) for field in ("title", "abstract")} for text in texts]
    averages = {field: sum(length[field] for length in lengths) / len(records) or 1.0
                for field in ("title", "abstract")}
    scores = [0.0] * len(records)
    evidence: list[dict] = [{"matched_groups": []} for _ in records]
    for group in groups:
        group_scores = [0.0] * len(records)
        matched: list[dict[str, list[str]]] = [{} for _ in records]
        for term in dict.fromkeys(group):
            scope, body = _split_field_term(term)
            pattern = _compile_term(body)
            if pattern is None:
                continue
            term_fields = (scope,) if scope != "any" else ("title", "abstract")
            frequencies = [{field: len(pattern.findall(text[field])) for field in term_fields}
                           for text in texts]
            df = sum(any(counts.values()) for counts in frequencies)
            idf = math.log1p((len(records) - df + 0.5) / (df + 0.5))
            for index, counts in enumerate(frequencies):
                score = 0.0
                for field_name, count in counts.items():
                    if not count:
                        continue
                    normalization = 1 - BM25_B + BM25_B * lengths[index][field_name] / averages[field_name]
                    weight = TITLE_WEIGHT if field_name == "title" else 1.0
                    score += weight * idf * count * (BM25_K1 + 1) / (count + BM25_K1 * normalization)
                    matched[index].setdefault(field_name, []).append(term)
                group_scores[index] = max(group_scores[index], score)
        for index, score in enumerate(group_scores):
            scores[index] += score
            if matched[index]:
                evidence[index]["matched_groups"].append(matched[index])
    return scores, evidence


def rank_records(records: list[Record], plan: QueryPlan) -> list[Record]:
    if not records or plan.sort not in {"rrf", "bm25", "hybrid"}:
        return sort_records(records, plan.sort)
    groups = _ranking_groups(plan)
    lexical, evidence = _lexical_scores(records, groups)
    rrf = []
    for record in records:
        ranks: dict[tuple[str, str], int] = {}
        for hit in record.retrieval_hits:
            key = (hit["source"], hit["query"])
            ranks[key] = min(ranks.get(key, hit["rank"]), hit["rank"])
        rrf.append(sum(1 / (RRF_K + rank) for rank in ranks.values()))
    scores = rrf if plan.sort == "rrf" else lexical
    if plan.sort == "hybrid":
        scores = [0.0] * len(records)
        for values in (rrf, lexical):
            # 并列值共享名次，避免输入顺序产生额外相关性证据。
            rank_by_score = {value: index + 1 for index, value in enumerate(sorted(set(values), reverse=True))}
            for index, value in enumerate(values):
                if value > 0:
                    scores[index] += 1 / (RRF_K + rank_by_score[value])
    ranked = []
    for index, record in enumerate(records):
        title = _normalize_text(record.title or "")
        flags = ["ancillary"] if any(title.startswith(prefix) for prefix in (
            "supplementary material", "reply on rc", "faculty opinions recommendation", "comment on ",
        )) else []
        details = {**evidence[index], "rrf": rrf[index], "bm25": lexical[index],
                   "abstract_missing": not bool(record.abstract), "record_flags": flags}
        coverage = len(evidence[index]["matched_groups"]) / len(groups) if groups else 1.0
        details["concept_coverage"] = coverage
        score = scores[index] if plan.sort == "rrf" else scores[index] * coverage
        score = score * ANCILLARY_FACTOR if flags else score
        ranked.append(replace(record, ranking_score=score, ranking_evidence=details))
    return sorted(ranked, key=lambda r: (-(r.ranking_score or 0), -(r.citation_count or 0),
                                         norm_doi(r.doi) or r.source_id or norm_title(r.title) or ""))


def _add_retrieval_hit(record: Record, query: str, rank: int) -> None:
    record.retrieval_hits.append({"source": record.source, "query": query,
                                  "rank": rank, "score": record.relevance_score})


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #

def _response_page(payload: dict, source: str) -> tuple[dict, list[dict]]:
    block_name, items_name, count_name = (
        ("meta", "results", "count") if source == "openalex"
        else ("message", "items", "total-results")
    )
    block = payload.get(block_name)
    if not isinstance(block, dict):
        raise NetworkError(f"{source}: 响应缺少有效的 {block_name}")
    count = block.get(count_name)
    items = payload.get(items_name) if source == "openalex" else block.get(items_name)
    if type(count) is not int or count < 0:
        raise NetworkError(f"{source}: {count_name} 必须是非负整数")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise NetworkError(f"{source}: {items_name} 必须是 object 数组")
    return block, items


def _epmc_page(payload: dict) -> list[dict]:
    count = payload.get("hitCount")
    block = payload.get("resultList")
    if type(count) is not int or count < 0 or not isinstance(block, dict):
        raise NetworkError("europepmc: 响应缺少有效的 hitCount/resultList")
    items = block.get("result")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise NetworkError("europepmc: result 必须是 object 数组")
    return items


def _epmc_url(query: str, parameters: dict[str, str]) -> str:
    return API_EUROPEPMC + "?" + urllib.parse.urlencode({
        "query": query, "format": "json", "resultType": "core", **parameters,
    })


def _map_record(mapper: Callable[[dict], Record | None], item: dict, source: str) -> Record | None:
    if source == "crossref":
        for key in ("title", "container-title"):
            value = item.get(key)
            if value is not None and (not isinstance(value, list)
                                      or any(not isinstance(v, str) for v in value)):
                raise NetworkError(f"{source}: {key} 必须是字符串数组或 null")
    if source == "openalex" and item.get("abstract_inverted_index") is not None:
        inverted = item["abstract_inverted_index"]
        if not isinstance(inverted, dict) or any(
            not isinstance(positions, list) or any(type(p) is not int or p < 0 for p in positions)
            for positions in inverted.values()
        ):
            raise NetworkError(f"{source}: 摘要倒排位置必须是非负整数数组")
    try:
        record = mapper(item)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise NetworkError(f"{source}: 文献字段结构错误（{type(exc).__name__}）") from exc
    if record is None:
        return None
    for key in ("year", "citation_count"):
        value = getattr(record, key)
        if value is not None and type(value) is not int:
            raise NetworkError(f"{source}: {key} 必须是整数或 null")
    for key in ("doi", "title", "publication_date", "venue", "work_type", "abstract",
                "language", "open_access_pdf", "landing_page_url", "pdf_url", "oa_url"):
        value = getattr(record, key)
        if value is not None and not isinstance(value, str):
            raise NetworkError(f"{source}: {key} 必须是字符串或 null")
    if record.is_open_access is not None and type(record.is_open_access) is not bool:
        raise NetworkError(f"{source}: is_open_access 必须是布尔值或 null")
    if record.relevance_score is not None and type(record.relevance_score) not in (int, float):
        raise NetworkError(f"{source}: relevance_score 必须是数值或 null")
    if record.relevance_score is not None and not math.isfinite(record.relevance_score):
        raise NetworkError(f"{source}: relevance_score 必须是有限数值")
    if not isinstance(record.source_id, str):
        raise NetworkError(f"{source}: source_id 必须是字符串")
    return record


class Provider:
    name = ""
    env_key = ""
    requires_api_key = False
    key_help = ""

    def compile_query(self, query: str, plan: QueryPlan) -> str:
        return query

    def filter_execution(self, plan: QueryPlan) -> dict[str, str]:
        return {key: "remote" for key in (
            "year", "min_citations", "author", "institution", "journal_issn",
            "work_type", "oa_only", "language", "raw_filter",
        ) if getattr(plan, key)}

    def diagnostics(self, plan: QueryPlan, query: str) -> list[Diagnostic]:
        return []

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        raise NotImplementedError

class OpenAlexProvider(Provider):
    name = "openalex"
    env_key = "OPENALEX_API_KEY"
    requires_api_key = True
    key_help = (
        "本工具使用 OpenAlex 检索时要求提供 API key。\n"
        "  申请：https://openalex.org/settings/api\n"
        "  设置（任选其一）：\n"
        + (
            '    1) 环境变量   setx OPENALEX_API_KEY "你的key"   （重开终端生效）\n'
            "    2) 用户级配置 %APPDATA%\\litsearch\\config.toml  ->  "
            if os.name == "nt" else
            '    1) 环境变量   export OPENALEX_API_KEY="你的key"\n'
            "    2) 用户级配置 ~/.config/litsearch/config.toml  ->  "
        )
        + '[api_keys] 下写 openalex = "你的key"\n'
        "    3) 项目级     ./.env  ->  OPENALEX_API_KEY=你的key\n"
        "  免 key 试用：追加 --source crossref 或 --source europepmc"
    )
    STRUCTURED_FILTER_KEYS: dict[str, set[str]] = {
        "year": {"from_publication_date", "to_publication_date", "publication_year"},
        "min_citations": {"cited_by_count"},
        "author": {"authorships.author.id", "raw_author_name.search"},
        "institution": {"institutions.id", "institutions.ror", "raw_affiliation_strings.search"},
        "journal_issn": {"primary_location.source.issn"},
        "work_type": {"type"},
        "oa_only": {"is_oa", "open_access.is_oa"},
        "language": {"language"},
    }

    def diagnostics(self, plan: QueryPlan, query: str) -> list[Diagnostic]:
        return [Diagnostic("filter_override", message, self.name, query, field_name)
                for field_name, message in self._resolve_filters(plan)[1]]

    def filter_execution(self, plan: QueryPlan) -> dict[str, str]:
        modes = super().filter_execution(plan)
        for field_name, _ in self._resolve_filters(plan)[1]:
            if field_name in modes:
                modes[field_name] = "overridden"
        return modes

    def _structured_filter_entries(self, plan: QueryPlan) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        if plan.year:
            lo, _, hi = plan.year.partition("-")
            hi = hi or lo
            entries.append(("year", "from_publication_date", f"{lo.strip()}-01-01"))
            entries.append(("year", "to_publication_date", f"{hi.strip()}-12-31"))
        # OpenAlex 使用严格大于比较；减一以保持与本地 >= 过滤一致。
        if plan.min_citations:
            entries.append(("min_citations", "cited_by_count", f">{plan.min_citations - 1}"))
        if plan.author:
            value = plan.author.strip()
            if re.fullmatch(r"A\d+", value):
                entries.append(("author", "authorships.author.id", value))
            else:
                entries.append(("author", "raw_author_name.search", value))
        if plan.institution:
            value = plan.institution.strip()
            if re.fullmatch(r"https?://ror\.org/\w+", value):
                entries.append(("institution", "institutions.ror", value.rstrip("/").rsplit("/", 1)[-1]))
            elif re.fullmatch(r"I\d+", value):
                entries.append(("institution", "institutions.id", value))
            else:
                entries.append(("institution", "raw_affiliation_strings.search", value))
        if plan.journal_issn:
            entries.append(("journal_issn", "primary_location.source.issn", plan.journal_issn.strip()))
        if plan.work_type:
            entries.append(("work_type", "type", plan.work_type.strip()))
        if plan.oa_only:
            entries.append(("oa_only", "is_oa", "true"))
        if plan.language:
            entries.append(("language", "language", plan.language.strip()))
        return entries

    def structured_filters(self, plan: QueryPlan) -> list[tuple[str, str]]:
        return [(key, value) for _, key, value in self._structured_filter_entries(plan)]

    def _raw_filter_keys(self, raw: str) -> set[str]:
        keys = set()
        for chunk in raw.split(","):
            key = chunk.split(":", 1)[0].strip()
            if key:
                keys.add(key)
        return keys

    def _overridden_by(self, field_name: str, remote_key: str, raw_keys: set[str]) -> str | None:
        if remote_key in raw_keys:
            return remote_key
        if field_name == "year":
            return "publication_year" if "publication_year" in raw_keys else None
        hits = raw_keys & self.STRUCTURED_FILTER_KEYS[field_name]
        return sorted(hits)[0] if hits else None

    def _resolve_filters(self, plan: QueryPlan) -> tuple[str, list[tuple[str, str]]]:
        """结构化 filter 在前，--oa-filter 追加在后；语义同组或同键时 raw 优先并警告。"""
        overrides: dict[str, str] = {}
        raw = (plan.raw_filter or "").strip()
        raw_keys = self._raw_filter_keys(raw) if raw else set()
        parts: list[str] = []
        for field_name, remote_key, value in self._structured_filter_entries(plan):
            raw_hit = self._overridden_by(field_name, remote_key, raw_keys)
            if raw_hit:
                overrides.setdefault(
                    field_name,
                    f"openalex: --oa-filter 中的 {raw_hit} 覆盖了 {field_name} 条件，已忽略后者",
                )
                continue
            parts.append(f"{remote_key}:{value}")
        if raw:
            parts.append(raw)
        return ",".join(parts), list(overrides.items())

    def filter_string(self, plan: QueryPlan) -> tuple[str, list[str]]:
        filter_str, overrides = self._resolve_filters(plan)
        return filter_str, [message for _, message in overrides]

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
        remote_sort = FETCH_SORT_OPENALEX.get(plan.fetch_sort)
        if remote_sort:
            params["sort"] = remote_sort
        if api_key:
            params["api_key"] = api_key
        return f"{API_OPENALEX}/works?{urllib.parse.urlencode(params)}"

    def map_work(self, work: dict) -> Record:
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        oa = work.get("open_access") or {}
        best = work.get("best_oa_location") or {}
        doi = norm_doi(work.get("doi"))
        authors: list[str] = []
        for authorship in work.get("authorships") or []:
            name = ((authorship.get("author") or {}).get("display_name") or "").strip()
            if name:
                authors.append(name)
        return Record(
            doi=doi,
            title=work.get("title"),
            year=work.get("publication_year"),
            publication_date=work.get("publication_date"),
            venue=source.get("display_name"),
            work_type=work.get("type"),
            citation_count=work.get("cited_by_count"),
            abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
            abstract_source=self.name if work.get("abstract_inverted_index") else None,
            authors=authors,
            is_open_access=oa.get("is_oa"),
            open_access_pdf=oa.get("oa_url") or None,
            language=work.get("language"),
            relevance_score=work.get("relevance_score"),
            source=self.name,
            sources=[self.name],
            source_id=work.get("id") or "",
            source_ids={self.name: [work["id"]]} if work.get("id") else {},
            doi_aliases=[doi] if doi else [],
            raw_work_types={self.name: [work["type"]]} if work.get("type") else {},
            landing_page_url=best.get("landing_page_url") or primary.get("landing_page_url"),
            pdf_url=best.get("pdf_url") or primary.get("pdf_url"),
            oa_url=oa.get("oa_url") or None,
        )

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        _, warnings = self.filter_string(plan)
        api_key = cfg.api_keys.get(self.name, "")
        records: list[Record] = []
        total = 0
        cursor: str | None = "*"
        remaining = plan.limit
        fetched = 0
        # 循环条件里显式要求 cursor 非空：只有真拿到 next_cursor 才翻下一页，
        # 否则 None 会被拼进 URL。
        while remaining > 0 and cursor:
            url = self.build_url(query, plan, api_key, cursor,
                                 min(MAX_PER_PAGE, remaining))
            payload = decode_json(request_with_backoff(http, url, policy), self.name)
            meta, page = _response_page(payload, self.name)
            fetched += len(page)
            if total == 0:
                total = meta.get("count") or 0
            for index, work in enumerate(page, fetched - len(page) + 1):
                record = _map_record(self.map_work, work, self.name)
                if record is not None:
                    _add_retrieval_hit(record, query, index)
                    records.append(record)
            remaining -= len(page)
            if not page:          # 空页不减少 remaining，必须显式退出以免死循环
                break
            next_cursor = meta.get("next_cursor")
            if next_cursor is not None and (not isinstance(next_cursor, str)
                                            or next_cursor == cursor):
                raise NetworkError("openalex: next_cursor 必须是新的字符串或 null")
            cursor = next_cursor
        # 远端若返回多于请求的条数（或首页超发），按 --limit 截断
        mapped = len(records)
        records = records[:plan.limit]
        stop = "limit" if fetched >= plan.limit and fetched < total else "exhausted"
        return ProviderResult(total, records, warnings, fetched, mapped, stop)


def _crossref_date(item: dict, diagnostics: list[Diagnostic]) -> tuple[int | None, str | None]:
    selected: list[int | None] = []
    for key in ("published", "issued"):
        block = item.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise NetworkError(f"{key} 必须是 object 或 null")
        parts = block.get("date-parts")
        if parts is None:
            continue
        if not isinstance(parts, list) or any(
            not isinstance(part, list) or any(n is not None and type(n) is not int for n in part)
            for part in parts
        ):
            raise NetworkError(f"{key}.date-parts 必须是整数或 null 组成的嵌套数组")
        if any(n is None for part in parts for n in part):
            diagnostics.append(Diagnostic(
                "provider_warning", f"{key}.date-parts 含空值，保留可用年份，缺失日期按 null 处理",
                source="crossref", field=f"{key}.date-parts",
            ))
        if not selected and parts and parts[0] and parts[0][0] is not None:
            selected = parts[0]
    if not selected:
        return None, None
    year = selected[0]
    month = selected[1] if len(selected) > 1 else 1
    day = selected[2] if len(selected) > 2 else 1
    publication_date = (f"{year:04d}-{month:02d}-{day:02d}"
                        if year is not None and month is not None and day is not None else None)
    return year, publication_date


class CrossrefProvider(Provider):
    name = "crossref"
    env_key = "CROSSREF_API_KEY"
    requires_api_key = False

    def _resolved_work_type(self, work_type: str | None) -> str | None:
        if not work_type:
            return None
        mapped = CROSSREF_TYPE_MAP.get(work_type.strip(), work_type.strip())
        return mapped if mapped in CROSSREF_SUPPORTED_TYPES else None

    def filter_execution(self, plan: QueryPlan) -> dict[str, str]:
        modes = super().filter_execution(plan)
        for key in ("institution", "oa_only", "raw_filter"):
            if key in modes:
                modes[key] = "unsupported"
        for key in ("min_citations", "language"):
            if key in modes:
                modes[key] = "local"
        if plan.author and re.fullmatch(r"A\d+", plan.author):
            modes["author"] = "unsupported"
        if plan.work_type and self._resolved_work_type(plan.work_type) is None:
            modes["work_type"] = "unsupported"
        return modes

    def diagnostics(self, plan: QueryPlan, query: str) -> list[Diagnostic]:
        result: list[Diagnostic] = []
        for key, mode in self.filter_execution(plan).items():
            if mode != "unsupported":
                continue
            label = UNSUPPORTED_FLAG_LABELS[key]
            msg = (f"crossref: {label} ({plan.work_type}) 不受支持，已忽略（仅支持 article/preprint 或 Crossref 原生类型）"
                   if key == "work_type" else f"crossref: {label} 不受支持，已忽略")
            result.append(Diagnostic("unsupported_filter", msg, self.name, query, key))
        return result

    def structured_filters(self, plan: QueryPlan) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if plan.year:
            lo, _, hi = plan.year.partition("-")
            hi = hi or lo
            pairs.append(("from-pub-date", f"{lo.strip()}-01-01"))
            pairs.append(("until-pub-date", f"{hi.strip()}-12-31"))
        if plan.journal_issn:
            pairs.append(("issn", plan.journal_issn.strip()))
        resolved_type = self._resolved_work_type(plan.work_type)
        if resolved_type:
            pairs.append(("type", resolved_type))
        return pairs

    def build_url(self, query: str, plan: QueryPlan, rows: int, offset: int | str,
                  mailto: str | None) -> str:
        params: dict[str, str] = {
            "query": query,
            "rows": str(max(1, min(CROSSREF_MAX_ROWS, rows))),
        }
        params["cursor" if isinstance(offset, str) else "offset"] = (
            offset if isinstance(offset, str) else str(max(0, offset))
        )
        filters = [f"{k}:{v}" for k, v in self.structured_filters(plan)]
        if filters:
            params["filter"] = ",".join(filters)
        if plan.author and not re.fullmatch(r"A\d+", plan.author.strip()):
            params["query.author"] = plan.author.strip()
        remote_sort = FETCH_SORT_CROSSREF.get(plan.fetch_sort)
        if remote_sort:
            params["sort"] = remote_sort
            params["order"] = "desc"
        if mailto:
            params["mailto"] = mailto
        return f"{API_CROSSREF}/works?{urllib.parse.urlencode(params)}"

    def map_item(self, item: dict, *, diagnostics: list[Diagnostic] | None = None) -> Record | None:
        year, pub_date = _crossref_date(item, diagnostics if diagnostics is not None else [])
        titles = item.get("title") or []
        title = strip_jats(titles[0]) if titles else None
        doi = norm_doi(item.get("DOI"))
        if not title and not doi:
            return None
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
            work_type={"journal-article": "article", "posted-content": "preprint"}.get(
                item.get("type") or "", item.get("type")),
            citation_count=item.get("is-referenced-by-count"),
            abstract=strip_jats(item.get("abstract")),
            abstract_source=self.name if strip_jats(item.get("abstract")) else None,
            authors=authors,
            is_open_access=None,
            open_access_pdf=pdf_url,
            language=item.get("language") or None,
            relevance_score=item.get("score"),
            source=self.name,
            sources=[self.name],
            source_id=doi or "",
            source_ids={self.name: [doi]} if doi else {},
            doi_aliases=[doi] if doi else [],
            raw_work_types={self.name: [item["type"]]} if item.get("type") else {},
            landing_page_url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
            pdf_url=pdf_url,
        )

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        warnings: list[str] = []
        diagnostics: list[Diagnostic] = []
        local_filter_needed = bool(plan.min_citations) or bool(plan.language)
        overfetch = plan.limit * 3 if local_filter_needed else plan.limit
        warnings.extend(d.message for d in self.diagnostics(plan, query))

        records: list[Record] = []
        total = 0
        cursor = "*"
        fetched = 0
        stop = "exhausted"
        while fetched < overfetch:
            rows = min(CROSSREF_PAGE_SIZE, overfetch - fetched)
            url = self.build_url(query, plan, rows, cursor, cfg.mailto)
            payload = decode_json(request_with_backoff(http, url, policy), self.name)
            message, items = _response_page(payload, self.name)
            if total == 0:
                total = message.get("total-results") or 0
            if not items:
                break
            fetched += len(items)
            for index, item in enumerate(items, fetched - len(items) + 1):
                raw_doi = item.get("DOI")
                identity = (norm_doi(raw_doi) if isinstance(raw_doi, str) else None) or f"第 {index} 条记录"
                start = len(diagnostics)
                try:
                    record = _map_record(functools.partial(self.map_item, diagnostics=diagnostics),
                                         item, self.name)
                except NetworkError as exc:
                    raise NetworkError(f"crossref: {identity}: {exc}") from exc
                for diagnostic in diagnostics[start:]:
                    diagnostic.query = query
                    diagnostic.message = f"crossref: {identity}: {diagnostic.message}"
                if record is not None:
                    _add_retrieval_hit(record, query, index)
                    records.append(record)
            if fetched >= total or len(items) < rows:
                break
            if fetched >= overfetch:
                stop = "scan_budget" if local_filter_needed else "limit"
                break
            next_cursor = message.get("next-cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise NetworkError("crossref: 分页响应缺少新的 next-cursor")
            cursor = next_cursor

        mapped = len(records)
        missing_titles = sum(not record.title for record in records)
        if missing_titles:
            diagnostics.append(Diagnostic(
                "provider_warning", f"crossref: {missing_titles} 条含 DOI 的映射候选缺少标题，已保留",
                self.name, query, "title",
            ))
        if plan.min_citations:
            before = len(records)
            records = [r for r in records
                       if (r.citation_count or 0) >= plan.min_citations]
            removed = before - len(records)
            if removed:
                warnings.append(
                    f"crossref: --min-citations 为本地过滤（该源不支持按被引过滤），"
                    f"剔除 {removed} 条；可提高 --limit 补充候选"
                )
        if plan.language:
            wanted = plan.language.strip().lower()
            before = len(records)
            records = [r for r in records
                       if (r.language or "").lower().startswith(wanted)]
            removed = before - len(records)
            warnings.append(
                f"crossref: --language 为本地过滤，剔除 {removed} 条"
                "（该源 language 字段稀疏，召回不可靠；可提高 --limit 补充候选）"
            )
        records = records[:plan.limit]
        return ProviderResult(total, records, warnings, fetched, mapped, stop, diagnostics)


def _epmc_integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if type(value) is int or (isinstance(value, str) and value.isascii() and value.isdigit()):
        return int(value)
    raise ValueError("Europe PMC 数值字段必须是整数或数字字符串")


class EuropePMCProvider(Provider):
    name = "europepmc"

    def filter_execution(self, plan: QueryPlan) -> dict[str, str]:
        modes = super().filter_execution(plan)
        for key in ("institution", "raw_filter", "work_type"):
            if key in modes:
                modes[key] = "unsupported"
        for key in ("min_citations", "language"):
            if key in modes:
                modes[key] = "local"
        if plan.author and re.fullmatch(r"A\d+", plan.author):
            modes["author"] = "unsupported"
        if plan.fetch_sort != "relevance":
            modes["fetch_sort"] = "unsupported"
        return modes

    def diagnostics(self, plan: QueryPlan, query: str) -> list[Diagnostic]:
        return [Diagnostic("unsupported_filter", f"europepmc: {UNSUPPORTED_FLAG_LABELS[key]} 不受支持，已忽略",
                           self.name, query, key)
                for key, mode in self.filter_execution(plan).items() if mode == "unsupported"]

    def compile_query(self, query: str, plan: QueryPlan) -> str:
        parts = [f"({query})"]
        if plan.year:
            lo, _, hi = plan.year.partition("-")
            parts.append(f"PUB_YEAR:[{lo} TO {hi or lo}]")
        if plan.oa_only:
            parts.append("OPEN_ACCESS:Y")
        for key, value in (("AUTH", plan.author), ("ISSN", plan.journal_issn)):
            if value and not (key == "AUTH" and re.fullmatch(r"A\d+", value)):
                parts.append(f"{key}:{_quote_query_term(value)}")
        return " AND ".join(parts)

    def map_item(self, item: dict) -> Record | None:
        identifier, source = item.get("id"), item.get("source")
        if not isinstance(identifier, str) or not isinstance(source, str) or not identifier or not source:
            raise ValueError("Europe PMC 记录缺少 id/source")
        doi = norm_doi(item.get("doi"))
        abstract = strip_jats(item.get("abstractText"))
        types = (item.get("pubTypeList") or {}).get("pubType") or []
        if not isinstance(types, list) or any(not isinstance(t, str) for t in types):
            raise ValueError("Europe PMC pubType 必须是字符串数组")
        work_type = "preprint" if source == "PPR" else (
            "review" if "Review" in types else "article" if "Journal Article" in types else None)
        journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title")
        return Record(
            doi=doi, title=strip_jats(item.get("title")), year=_epmc_integer(item.get("pubYear")),
            publication_date=item.get("firstPublicationDate"), venue=journal,
            citation_count=_epmc_integer(item.get("citedByCount")),
            abstract=abstract, abstract_source=self.name if abstract else None,
            authors=[a["fullName"] for a in (item.get("authorList") or {}).get("author", [])
                     if a.get("fullName")],
            is_open_access={"Y": True, "N": False}.get(item.get("isOpenAccess") or ""),
            language={"eng": "en", "chi": "zh", "zho": "zh"}.get(item.get("language") or "", item.get("language")),
            work_type=work_type,
            source=self.name, sources=[self.name], source_id=f"{source}:{identifier}",
            source_ids={self.name: [f"{source}:{identifier}"]},
            doi_aliases=[doi] if doi else [], raw_work_types={self.name: types} if types else {},
            landing_page_url=f"https://europepmc.org/article/{urllib.parse.quote(source)}/{urllib.parse.quote(identifier)}",
        )

    def search(self, query: str, plan: QueryPlan, http: Http,
               policy: RetryPolicy, cfg: "Config") -> ProviderResult:
        budget = plan.limit * (3 if plan.min_citations or plan.language else 1)
        fetched, total = 0, 0
        records: list[Record] = []
        cursor, stop = "*", "exhausted"
        while fetched < budget:
            size = min(MAX_PER_PAGE, budget - fetched)
            url = _epmc_url(self.compile_query(query, plan), {"cursorMark": cursor, "pageSize": str(size)})
            payload = decode_json(request_with_backoff(http, url, policy), self.name)
            items = _epmc_page(payload)
            total = payload["hitCount"]
            fetched += len(items)
            for index, item in enumerate(items, fetched - len(items) + 1):
                record = _map_record(self.map_item, item, self.name)
                if record is not None:
                    _add_retrieval_hit(record, query, index)
                    records.append(record)
            if not items or len(items) < size or fetched >= total:
                break
            if fetched >= budget:
                stop = "scan_budget" if budget != plan.limit else "limit"
                break
            next_cursor = payload.get("nextCursorMark")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise NetworkError("europepmc: 分页响应缺少新的 nextCursorMark")
            cursor = next_cursor
        mapped = len(records)
        warnings = [d.message for d in self.diagnostics(plan, query)]
        if plan.min_citations:
            before = len(records)
            records = [r for r in records if (r.citation_count or 0) >= plan.min_citations]
            removed = before - len(records)
            if removed:
                warnings.append(
                    f"europepmc: --min-citations 为本地过滤（该源不支持按被引过滤），"
                    f"剔除 {removed} 条；可提高 --limit 补充候选"
                )
        if plan.language:
            before = len(records)
            records = [r for r in records if r.language == plan.language]
            removed = before - len(records)
            warnings.append(
                f"europepmc: --language 为本地过滤，剔除 {removed} 条；可提高 --limit 补充候选"
            )
        return ProviderResult(total, records[:plan.limit], warnings, fetched, mapped, stop)


def _quote_query_term(term: str) -> str:
    return '"' + term.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _queries_for(plan: QueryPlan, source: str) -> list[str]:
    queries = list(plan.queries)
    if plan.concepts:
        groups = plan.concepts
        if source == "crossref":
            # 按同义词位置轮换，不向 query 发送另一来源的 Boolean 语法。
            for index in range(max(map(len, groups))):
                queries.append(" ".join(_quote_query_term(group[index % len(group)]) for group in groups))
            if len(groups) > 2:
                queries.append(" ".join(_quote_query_term(group[0]) for group in groups[:2]))
        else:
            prefix = "TITLE_ABS:" if source == "europepmc" else ""
            clauses = ["(" + " OR ".join(prefix + _quote_query_term(term) for term in group) + ")"
                       for group in groups]
            queries.append(" AND ".join(clauses))
            if len(clauses) > 2:
                queries.append(" AND ".join(clauses[:2]))
    return list(dict.fromkeys(queries))


PROVIDERS: dict[str, Provider] = {
    OpenAlexProvider.name: OpenAlexProvider(),
    CrossrefProvider.name: CrossrefProvider(),
    EuropePMCProvider.name: EuropePMCProvider(),
}


class SearchClient:
    """完整检索入口；配置和 transport 显式注入，结果通过返回值交付。"""

    def __init__(self, config: "Config", http: Http | None = None,
                 *, retry: RetryPolicy | None = None) -> None:
        _validate_config(config)
        self.config = config
        self.http = http if http is not None else Http()
        self.retry = retry if retry is not None else RetryPolicy()
        self._abstract_cache: dict[str, str | None] = {}

    def _fetch_abstracts(self, dois: list[str]) -> dict[str, str | None]:
        query = " OR ".join(f"DOI:{_quote_query_term(doi)}" for doi in dois)
        abstracts: dict[str, str | None] = dict.fromkeys(dois)
        cursor = "*"
        fetched = 0
        while True:
            url = _epmc_url(query, {"pageSize": str(MAX_PER_PAGE), "cursorMark": cursor})
            payload = decode_json(request_with_backoff(self.http, url, self.retry), "europepmc")
            items = _epmc_page(payload)
            fetched += len(items)
            for item in items:
                remote_doi = item.get("doi")
                if remote_doi is not None and not isinstance(remote_doi, str):
                    raise NetworkError("europepmc: doi 必须是字符串或 null")
                doi = norm_doi(remote_doi)
                if doi is None or doi not in abstracts:
                    continue
                value = item.get("abstractText")
                if value is not None and not isinstance(value, str):
                    raise NetworkError("europepmc: abstractText 必须是字符串或 null")
                if not abstracts[doi]:
                    abstracts[doi] = strip_jats(value)
            if fetched >= payload["hitCount"]:
                return abstracts
            next_cursor = payload.get("nextCursorMark")
            if not items or not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise NetworkError("europepmc: 摘要分页响应缺少新的 nextCursorMark")
            cursor = next_cursor

    def _enrich(self, records: list[Record], budget: int) -> EnrichmentStats:
        stats = EnrichmentStats()
        if not budget:
            return stats
        eligible: list[tuple[Record, str]] = []
        for record in records:
            doi = norm_doi(record.doi)
            if not record.abstract and doi:
                eligible.append((record, doi))
        cached = set(self._abstract_cache)
        uncached = list(dict.fromkeys(doi for _, doi in eligible if doi not in cached))[:budget]
        for offset in range(0, len(uncached), ABSTRACT_BATCH_SIZE):
            batch = uncached[offset:offset + ABSTRACT_BATCH_SIZE]
            self._abstract_cache.update(self._fetch_abstracts(batch))
            stats.requested += len(batch)
        for record, doi in eligible:
            if doi not in self._abstract_cache:
                stats.budget_skipped += 1
                continue
            if doi in cached:
                stats.cache_hits += 1
            cached.add(doi)
            abstract = self._abstract_cache[doi]
            if abstract:
                record.abstract = abstract
                record.abstract_source = "europepmc"
                stats.filled += 1
            else:
                stats.not_found += 1
        return stats

    def explain(self, plan: QueryPlan) -> dict[str, Any]:
        plan = validate_plan(plan)
        return {
            "version": __version__, "schema_version": SCHEMA_VERSION,
            "plan": asdict(plan),
            "config_origins": dict(self.config.origins),
            "compiled_queries": [{"source": name, "query": query,
                                  "remote_query": PROVIDERS[name].compile_query(query, plan)}
                                 for name in plan.sources for query in _queries_for(plan, name)],
            "sources": [{
                "source": name,
                "key_configured": bool(self.config.api_keys.get(name)),
                "requires_api_key": PROVIDERS[name].requires_api_key,
                "query_count": len(_queries_for(plan, name)),
                "filter_execution": PROVIDERS[name].filter_execution(plan),
            } for name in plan.sources],
            "diagnostics": [asdict(d) for name in plan.sources for query in _queries_for(plan, name)
                            for d in PROVIDERS[name].diagnostics(plan, query)],
        }

    def _fetch(self, plan: QueryPlan) -> tuple[list[Record], list[QueryExecution], list[Diagnostic]]:
        records: list[Record] = []
        executions: list[QueryExecution] = []
        diagnostics: list[Diagnostic] = []
        for name in plan.sources:
            provider = PROVIDERS[name]
            for query in _queries_for(plan, name):
                current = provider.diagnostics(plan, query)
                result = provider.search(query, plan, self.http, self.retry, self.config)
                current.extend(result.diagnostics)
                executions.append(QueryExecution(
                    name, query, result.total, result.fetched_count, result.mapped_count,
                    len(result.records), result.stop_reason, provider.filter_execution(plan),
                ))
                records.extend(result.records)
                for warning in result.warnings:
                    if not any(d.message == warning for d in current):
                        current.append(Diagnostic("provider_warning", warning, name, query))
                if result.stop_reason != "exhausted":
                    local_fields = [key for key, mode in provider.filter_execution(plan).items()
                                    if mode == "local"]
                    reason = ("耗尽 scan_budget 扫描预算" if result.stop_reason == "scan_budget"
                              else "到达 limit 候选窗口")
                    detail = f"；来源本地条件：{', '.join(local_fields)}" if local_fields else ""
                    current.append(Diagnostic(
                        "fetch_window_limited", f"{name}: 「{query}」远端共 {result.total} 篇，"
                        f"只考察了前 {result.fetched_count} 篇；{reason}{detail}；"
                        f"映射 {result.mapped_count} 篇，保留 {len(result.records)} 篇"
                        "（提高 --limit 可取更多候选，或调整筛选条件）", name, query,
                    ))
                diagnostics.extend(current)
        return records, executions, diagnostics

    def search(self, plan: QueryPlan) -> SearchResult:
        plan = validate_plan(plan)
        preflight = [d for name in plan.sources for query in _queries_for(plan, name)
                     for d in PROVIDERS[name].diagnostics(plan, query)]
        if plan.strict_filters and preflight:
            raise UsageError("严格筛选模式拒绝忽略或覆盖条件：" + preflight[0].message)
        check_keys(plan.sources, self.config)
        raw, executions, diagnostics = self._fetch(plan)
        merged, stats = merge_records(raw, policy=plan.merge_policy)
        enrichment = self._enrich(merged, plan.enrich_abstracts)
        if enrichment.not_found:
            diagnostics.append(Diagnostic("abstract_not_found",
                f"europepmc: {enrichment.not_found} 条候选未找到可用摘要", source="europepmc"))
        if enrichment.budget_skipped:
            diagnostics.append(Diagnostic("enrichment_budget_limited",
                f"摘要补全预算用尽，跳过 {enrichment.budget_skipped} 条候选", field="enrich_abstracts"))
        filtered, filter_stats = apply_filters(merged, plan.require, plan.exclude)
        if not filtered:
            if merged:
                diagnostics.append(Diagnostic(
                    "empty_results", f"合并后 {len(merged)} 篇经 require/exclude 主题过滤后为空；"
                    "请检查 filter_stats 各组保留量并调整主题条件", field="require/exclude"))
            else:
                for execution in executions:
                    if execution.remote_total == 0:
                        reason = "远端无命中，请调整检索词或远端条件"
                    elif execution.mapped_count == 0:
                        reason = f"已扫描 {execution.fetched_count} 篇，映射未产生有效记录"
                    elif execution.returned_count == 0:
                        reason = f"已扫描 {execution.fetched_count} 篇，来源本地条件筛空，请调整条件或提高 --limit"
                    else:
                        reason = "候选缺少可合并身份，合并后为空"
                    diagnostics.append(Diagnostic(
                        "empty_results", f"{execution.source}: 「{execution.query}」{reason}",
                        execution.source, execution.query))
        ordered = rank_records(filtered, plan)
        truncated = max(0, len(ordered) - plan.max_results) if plan.max_results else 0
        if truncated:
            ordered = ordered[:plan.max_results]
            diagnostics.append(Diagnostic(
                "results_truncated", f"过滤后 {filter_stats.after} 篇，按 --max-results "
                f"{plan.max_results} 截断掉 {truncated} 篇",
            ))
        missing_doi = sum(not norm_doi(r.doi) for r in ordered)
        if missing_doi:
            diagnostics.append(Diagnostic(
                "missing_doi", f"{missing_doi} 条无 DOI，未写入 DOI 列表",
            ))
        totals = [(f"{e.source} · {e.query}", e.remote_total) for e in executions]
        report = Report(plan, totals, stats, filter_stats, truncated, executions, diagnostics)
        report.enrichment = enrichment
        return SearchResult(ordered, report)


def search_all(plan: QueryPlan, cfg: "Config", http: Http,
               policy: RetryPolicy, sleep_fn: Callable[[float], None] = time.sleep,
               polite: float = POLITE_DELAY) -> tuple[list[Record], list[tuple[str, int]], list[str]]:
    """候选抓取兼容入口；完整工作流使用 SearchClient.search。"""
    http.sleep, http.polite = sleep_fn, polite
    records, executions, diagnostics = SearchClient(cfg, http, retry=policy)._fetch(validate_plan(plan))
    totals = [(f"{e.source} · {e.query}", e.remote_total) for e in executions]
    return records, totals, list(dict.fromkeys(d.message for d in diagnostics))


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
    today = report.generated
    heading = plan.title or (plan.queries[0] if plan.queries else "文献清单")
    lines: list[str] = [f"# {heading}", "", f"> 最近更新：{today}。", ""]

    lines.append("## 检索说明")
    lines.append("")
    lines.append(f"- 数据来源：{'、'.join(plan.sources)}")
    lines.append(f"- 年份范围：{plan.year or '不限'}")
    lines.append(f"- 每个查询抓取候选：{plan.limit} 条")
    if plan.max_results is not None:
        lines.append(f"- 产出上限：{plan.max_results} 条")
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
    lines.append(f"- 远端抓取排序：{plan.fetch_sort}")
    lines.append(f"- 展示排序：{plan.sort}")
    lines.append("")

    lines.append("各查询命中数（远端原始 total）：")
    lines.append("")
    for label, total in report.totals:
        lines.append(f"- `{label}` → {total} 篇")
    lines.append("")

    stats = report.stats
    fstats = report.filters
    lines.append("合并与过滤：")
    lines.append("")
    if report.queries:
        lines.append(f"- 远端返回候选 {sum(e.fetched_count for e in report.queries)} 条")
    lines.append(f"- 进入合并的候选 {stats.total_in} 条")
    if stats.doi_merged:
        lines.append(f"- 按 DOI 合并掉 {stats.doi_merged} 条（跨源同一文章）")
    if stats.title_merged:
        lines.append(f"- 按标题合并掉 {stats.title_merged} 条（同一论文的不同版本）")
    if stats.dropped_no_id:
        lines.append(f"- 丢弃 {stats.dropped_no_id} 条（既无 DOI 也无标题）")
    if plan.exclude:
        lines.append(f"- 排除词刷掉 {fstats.excluded} 条，剩 {fstats.passed_exclude} 条")
    for index, (group, kept) in enumerate(zip(plan.require, fstats.per_group), 1):
        lines.append(f"- 单看第 {index} 组「{' 或 '.join(group)}」可留 {kept} 条")
    if plan.require:
        lines.append(f"- 命中全部 require 组后剩 {fstats.after} 条")
    lines.append(f"- 过滤后保留 **{len(records)} 篇**（{plan.sort} 降序）")
    if report.truncated:
        lines.append(
            f"- 另有 {report.truncated} 篇被 --max-results {plan.max_results} 截断"
            "（提高该值可放出）"
        )
    lines.append("")

    if report.diagnostics:
        lines.extend(["## 诊断", ""])
        lines.extend(f"- `{d.code}`：{d.message}" for d in report.diagnostics)
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
            if isinstance(value, dict) or name == "retrieval_hits":
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            elif isinstance(value, list):
                value = "; ".join(str(v) for v in value)
            row.append("" if value is None else value)
        writer.writerow(row)
    return buf.getvalue()


def _result_payload(report: Report, records: list[Record]) -> dict[str, Any]:
    plan = report.plan
    payload: dict[str, Any] = {
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "generated": report.generated,
        "queries": [{"label": label, "total_count": total}
                    for label, total in report.totals],
        "filters": {
            "year": plan.year,
            "limit": plan.limit,
            "max_results": plan.max_results,
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
            "fetch_sort": plan.fetch_sort,
            "merge_policy": plan.merge_policy,
            "strict_filters": plan.strict_filters,
            "enrich_abstracts": plan.enrich_abstracts,
            "concepts": plan.concepts,
        },
        "merge_stats": asdict(report.stats),
        "enrichment": asdict(report.enrichment),
        "filter_stats": {
            "before": report.filters.before,
            "excluded": report.filters.excluded,
            "passed_exclude": report.filters.passed_exclude,
            "per_group": report.filters.per_group,
            "after": report.filters.after,
        },
        "truncated": report.truncated,
        "count": len(records),
        "results": [asdict(rec) for rec in records],
        "diagnostics": [asdict(d) for d in report.diagnostics],
    }
    for legacy, execution in zip(payload["queries"], report.queries):
        legacy.update(asdict(execution))
    return payload


def render_json(report: Report, records: list[Record]) -> str:
    return json.dumps(_result_payload(report, records), ensure_ascii=False, indent=2)


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


def query_slug(queries: list[str], title: str | None = None,
               max_len: int = 48) -> str:
    """生成文件名片段，优先用 --title，否则用第一个查询。

    三个约束：
    - 保留 Unicode 词字符（`\\w` 含中文），否则中文课题会退化成 'query'，
      同一天跑两个中文课题就会互相覆盖；
    - 按 '-' 边界截断，避免出现 '...marine-bacter' 这种切了一半的单词；
    - 万一连一个词字符都没有（标题全是标点），补短哈希，保证不同课题不撞名。
    """
    base = (title or "").strip() or (queries[0].strip() if queries else "")
    slug = re.sub(r"[^\w]+", "-", base.lower(), flags=re.UNICODE).strip("-_")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-_")
        cut = slug.rfind("-")
        if cut > max_len // 2:        # 只在回退幅度不大时才退到词边界
            slug = slug[:cut]
    if not slug:
        if not base:
            return "query"
        return f"query-{hashlib.sha1(base.encode('utf-8')).hexdigest()[:8]}"
    return slug


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
    origins: dict[str, str] = field(default_factory=dict)


def user_config_path(env: dict[str, str] | None = None) -> Path | None:
    values = os.environ if env is None else env
    if os.name == "nt":
        base = values.get("APPDATA")
        return Path(base) / "litsearch" / "config.toml" if base else None
    return Path(values.get("HOME", str(Path.home()))) / ".config" / "litsearch" / "config.toml"


def parse_env_file(path: Path) -> dict[str, str]:
    """.env 是扁平 KEY=VALUE（tomllib 读不了），仅提供扁平回退。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"无法读取配置：{path}（{exc}）") from exc
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
    cwd = cwd.resolve()

    candidates: list[Path] = []
    override = env.get("LITSEARCH_CONFIG")
    if override:
        explicit_path = _absolute_path(Path(override), cwd)
        if not explicit_path.is_file():
            raise ConfigError(f"LITSEARCH_CONFIG 指定的文件不存在：{explicit_path}")
        candidates.append(explicit_path)
    resolved = user_config_path(env)
    if resolved:
        candidates.append(resolved)
    candidates.append(cwd / "litsearch.toml")

    documents: list[tuple[Path, dict]] = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            loaded = tomllib.loads(path.read_text(encoding="utf-8"))
            _validate_config_document(loaded, path)
            documents.append((path, loaded))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"配置文件无法解析：{path}（{exc}）") from exc

    origins: dict[str, str] = {}

    def scalar(toml_key: str) -> Any:
        for path, candidate in documents:
            found = candidate.get(toml_key)
            if found is not None and found != "":
                origins[toml_key] = str(path)
                return found
        return None

    api_section: dict[str, Any] = {}
    for path, loaded in documents:
        for source, credential in (loaded.get("api_keys") or {}).items():
            if source not in api_section and credential:
                api_section[source] = credential
                origins[f"api_keys.{source}"] = str(path)

    presets: dict[str, dict] = {}
    for path, loaded in reversed(documents):
        presets.update(loaded.get("presets") or {})
        for name in loaded.get("presets", {}):
            origins[f"presets.{name}"] = str(path)

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
            key = f"api_keys.{name}"
            if env.get(provider.env_key):
                origins[key] = f"env:{provider.env_key}"
            elif name not in api_section:
                origins[key] = str(cwd / ".env")

    mailto = _first_not_none(
        env.get("CROSSREF_MAILTO"), env.get("LITSEARCH_MAILTO"),
        scalar("mailto"), dotenv.get("CROSSREF_MAILTO"), dotenv.get("LITSEARCH_MAILTO"),
    )
    output_dir_raw = _first_not_none(
        env.get("LITSEARCH_OUTPUT_DIR"),
        scalar("output_dir"),
        dotenv.get("LITSEARCH_OUTPUT_DIR"),
    )
    for key, names in (("mailto", ("CROSSREF_MAILTO", "LITSEARCH_MAILTO")),
                       ("output_dir", ("LITSEARCH_OUTPUT_DIR",))):
        supplied = next((name for name in names if env.get(name)), None)
        if supplied:
            origins[key] = f"env:{supplied}"
        elif key not in origins and (mailto if key == "mailto" else output_dir_raw):
            origins[key] = str(cwd / ".env")
    output_dir = None
    if output_dir_raw:
        origin = origins["output_dir"]
        base = cwd if origin.startswith("env:") else Path(origin).parent
        output_dir = _absolute_path(Path(output_dir_raw), base)
    default_sources: list[str] | None = None
    sources_default = scalar("default_sources")
    if sources_default:
        default_sources = list(sources_default)

    return Config(
        api_keys=api_keys,
        mailto=str(mailto) if mailto else None,
        output_dir=output_dir,
        default_sources=default_sources,
        presets=presets,
        config_path=documents[0][0] if documents else None,
        origins=origins,
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


PRESET_ALIASES = {"type": "work_type", "source": "sources", "oa_filter": "raw_filter"}
TEXT_OPTIONS = frozenset({
    "author", "institution", "journal_issn", "work_type", "language",
    "raw_filter", "sort", "fetch_sort", "title", "year", "merge_policy",
})
INTEGER_OPTIONS = frozenset({"limit", "max_results", "min_citations", "enrich_abstracts"})
LIST_OPTIONS = frozenset({"queries", "sources", "require", "exclude", "concepts"})


def _normalized_year(value: str) -> str:
    year = re.sub(r"\s+", "", value)
    if not re.fullmatch(r"[0-9]{4}(-[0-9]{4})?", year):
        raise UsageError("--year 格式应为 2024 或 2021-2026")
    lo, _, hi = year.partition("-")
    if int(lo) < 1 or int(hi or lo) < int(lo):
        raise UsageError("--year 的起点不能大于终点，年份必须大于零")
    return year


def _validate_preset(preset: dict, label: str = "preset") -> None:
    if not isinstance(preset, dict):
        raise ConfigError(f"{label} 必须是 table")
    known = TEXT_OPTIONS | INTEGER_OPTIONS | LIST_OPTIONS | {"oa_only", "strict_filters"}
    for key, value in preset.items():
        name = PRESET_ALIASES.get(key, key)
        if name not in known:
            raise ConfigError(f"{label}.{key}：未知选项")
        if key != name and name in preset:
            raise ConfigError(f"{label} 同时指定了 {key} 和 {name}")
        if name in TEXT_OPTIONS:
            valid = isinstance(value, str)
            if name == "year":
                valid = valid or type(value) is int
        elif name in INTEGER_OPTIONS:
            valid = type(value) is int
        elif name in {"oa_only", "strict_filters"}:
            valid = type(value) is bool
        else:
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
            if name == "sources" and isinstance(value, str):
                valid = True
        if not valid:
            raise ConfigError(f"{label}.{key}：类型错误")
        if name == "year":
            try:
                _normalized_year(str(value))
            except UsageError as exc:
                raise ConfigError(f"{label}.{key}：{exc}") from exc
        if name == "queries" and (not value or any(not q.strip() for q in value)):
            raise ConfigError(f"{label}.{key}：必须提供非空检索词")
        choices = SORT_CHOICES if name == "sort" else FETCH_SORT_CHOICES
        if name in {"sort", "fetch_sort"} and value not in choices:
            raise ConfigError(f"{label}.{key}：应为 {', '.join(choices)}")
        if name == "merge_policy" and value not in ("title", "doi"):
            raise ConfigError(f"{label}.{key}：应为 title 或 doi")
        if name in INTEGER_OPTIONS and value < (0 if name in {"min_citations", "enrich_abstracts"} else 1):
            raise ConfigError(f"{label}.{key}：超出允许范围")
        if name == "sources":
            names = value.split(",") if isinstance(value, str) else value
            if not names or any(n.strip().lower() not in PROVIDERS for n in names):
                raise ConfigError(f"{label}.{key}：未知或空数据源")


def _validate_api_keys(keys: Any, label: str) -> None:
    if not isinstance(keys, dict) or any(
        name not in PROVIDERS or not isinstance(value, str) for name, value in keys.items()
    ):
        raise ConfigError(f"{label}：api_keys 必须是来源名称到字符串的映射")


def _validate_default_sources(value: Any, label: str) -> None:
    if (not isinstance(value, list) or not value
            or any(not isinstance(n, str) or n.strip().lower() not in PROVIDERS for n in value)):
        raise ConfigError(f"{label}：default_sources 必须是有效来源的数组")


def _validate_config_document(document: dict, path: Path) -> None:
    known = {"api_keys", "presets", "mailto", "output_dir", "default_sources"}
    unknown = document.keys() - known
    if unknown:
        raise ConfigError(f"{path}：未知配置 {', '.join(sorted(unknown))}")
    for key in ("mailto", "output_dir"):
        if key in document and not isinstance(document[key], str):
            raise ConfigError(f"{path}：{key} 必须是字符串")
    _validate_api_keys(document.get("api_keys", {}), str(path))
    if "default_sources" in document:
        _validate_default_sources(document["default_sources"], str(path))
    presets = document.get("presets", {})
    if not isinstance(presets, dict):
        raise ConfigError(f"{path}：presets 必须是 table")
    for name, preset in presets.items():
        _validate_preset(preset, f"{path}:presets.{name}")


def _validate_config(config: Config) -> None:
    if not isinstance(config, Config):
        raise ConfigError("config 必须是 Config 实例")
    _validate_api_keys(config.api_keys, "Config")
    if config.mailto is not None and not isinstance(config.mailto, str):
        raise ConfigError("Config：mailto 必须是字符串或 None")
    if config.output_dir is not None and not isinstance(config.output_dir, (Path, str)):
        raise ConfigError("Config：output_dir 必须是 Path、字符串或 None")
    if config.default_sources is not None:
        _validate_default_sources(config.default_sources, "Config")
    if not isinstance(config.presets, dict):
        raise ConfigError("Config：presets 必须是映射")
    for name, preset in config.presets.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("Config：presets 键必须是非空字符串")
        _validate_preset(preset, f"presets.{name}")


def validate_plan(plan: QueryPlan) -> QueryPlan:
    """CLI 和 Python 调用共用的检索意图校验；返回独立、规范化的计划。"""
    for key in ("queries", "sources", "exclude"):
        value = getattr(plan, key)
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise UsageError(f"{key} 必须是字符串数组")
    for key in ("require", "concepts"):
        groups = getattr(plan, key)
        if (not isinstance(groups, list)
                or any(not isinstance(g, list) or not g
                       or any(not isinstance(t, str) or not t.strip() for t in g) for g in groups)):
            raise UsageError(f"{key} 必须是非空字符串组的数组")
    if (not plan.queries and not plan.concepts) or any(not q.strip() for q in plan.queries):
        raise UsageError("缺少检索词；检索词必须是非空字符串")
    sources = list(dict.fromkeys(s.strip().lower() for s in plan.sources))
    if not sources:
        raise UsageError("至少需要一个数据源")
    unknown = [source for source in sources if source not in PROVIDERS]
    if unknown:
        raise UsageError(f"未知数据源：{', '.join(unknown)}（可用：{', '.join(PROVIDERS)}）")
    for key in INTEGER_OPTIONS:
        value = getattr(plan, key)
        if value is None and key in {"max_results", "min_citations"}:
            continue
        if type(value) is not int or value < (0 if key in {"min_citations", "enrich_abstracts"} else 1):
            raise UsageError(f"{key} 必须是有效整数")
    for key in TEXT_OPTIONS:
        value = getattr(plan, key)
        if value is not None and not isinstance(value, str):
            raise UsageError(f"{key} 必须是字符串")
    if plan.sort not in SORT_CHOICES or plan.fetch_sort not in FETCH_SORT_CHOICES:
        raise UsageError(f"sort 必须是 {', '.join(SORT_CHOICES)}；fetch_sort 仅支持 relevance/citations/year")
    if plan.oa_only is not None and type(plan.oa_only) is not bool:
        raise UsageError("oa_only 必须是布尔值")
    if type(plan.strict_filters) is not bool:
        raise UsageError("strict_filters 必须是布尔值")
    if plan.merge_policy not in ("title", "doi"):
        raise UsageError("merge_policy 必须是 title 或 doi")
    year = _normalized_year(plan.year) if plan.year is not None else None
    normalized_text: dict[str, Any] = {}
    for key in TEXT_OPTIONS - {"year"}:
        value = getattr(plan, key)
        normalized_text[key] = value.strip() if value is not None else None
    return replace(plan, **normalized_text, queries=list(dict.fromkeys(q.strip() for q in plan.queries)), sources=sources,
                   year=year, oa_only=bool(plan.oa_only),
                   require=[list(g) for g in plan.require], exclude=list(plan.exclude),
                   concepts=[list(g) for g in dict.fromkeys(
                       tuple(dict.fromkeys(t.strip() for t in group)) for group in plan.concepts)])


def _preset_lookup(preset: dict | None, key: str) -> Any:
    if not preset:
        return None
    names = [key] + [alias for alias, target in PRESET_ALIASES.items()
                     if target == key]
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
    if preset is not None:
        _validate_preset(preset)
    # nargs="*" 的位置参数在缺省时给的是 []，必须当成「未给出」才能让 preset 生效
    cli_queries = args.query if args.query else None
    queries = _resolve(cli_queries, preset, "queries", [])
    sources_raw = _resolve(args.source, preset, "sources", cfg.default_sources or ["openalex"])
    sources = sources_raw.split(",") if isinstance(sources_raw, str) else sources_raw
    year_raw = _resolve(args.year, preset, "year", None)
    year = str(year_raw) if year_raw is not None else None

    require_specs = _resolve(args.require, preset, "require", []) or []
    exclude_specs = _resolve(args.exclude, preset, "exclude", []) or []
    require_groups: list[list[str]] = []
    for spec in require_specs:
        groups = _split_groups(str(spec))
        if not groups:
            raise UsageError("--require 必须包含非空检索词")
        require_groups.extend(groups)
    concepts: list[list[str]] = []
    for spec in _resolve(args.concept, preset, "concepts", []) or []:
        terms = [term.strip() for term in str(spec).split(",") if term.strip()]
        if not terms:
            raise UsageError("--concept 必须包含非空检索词")
        concepts.append(terms)
    exclude_terms: list[str] = []
    for spec in exclude_specs:
        exclude_terms.extend(_split_or(str(spec)))

    plan = QueryPlan(
        queries=queries,
        sources=sources,
        year=year,
        limit=_resolve(args.limit, preset, "limit", DEFAULT_LIMIT),
        min_citations=_resolve(args.min_citations, preset, "min_citations", None),
        author=_resolve(args.author, preset, "author", None),
        institution=_resolve(args.institution, preset, "institution", None),
        journal_issn=_resolve(args.journal_issn, preset, "journal_issn", None),
        work_type=_resolve(args.work_type, preset, "work_type", None),
        oa_only=_resolve(args.oa_only, preset, "oa_only", False),
        language=_resolve(args.language, preset, "language", None),
        raw_filter=_resolve(args.oa_filter, preset, "raw_filter", None),
        require=require_groups,
        exclude=exclude_terms,
        sort=_resolve(args.sort, preset, "sort", DEFAULT_SORT),
        fetch_sort=_resolve(args.fetch_sort, preset, "fetch_sort", DEFAULT_FETCH_SORT),
        max_results=_resolve(args.max_results, preset, "max_results", None),
        title=_resolve(args.title, preset, "title", None),
        strict_filters=_resolve(args.strict_filters, preset, "strict_filters", False),
        merge_policy=_resolve(args.merge_policy, preset, "merge_policy", DEFAULT_MERGE_POLICY),
        enrich_abstracts=_resolve(args.enrich_abstracts, preset, "enrich_abstracts", 0),
        concepts=concepts,
    )
    return validate_plan(plan)


# --------------------------------------------------------------------------- #
# 输出路径
# --------------------------------------------------------------------------- #

def _absolute_path(path: Path, cwd: Path) -> Path:
    expanded = path.expanduser()
    return (expanded if expanded.is_absolute() else cwd / expanded).resolve()


def _output_directory(cfg: Config, cwd: Path) -> Path:
    return _absolute_path(cfg.output_dir or Path("output"), cwd.resolve())


def _check_output_paths(paths: list[Path], overwrite: bool) -> None:
    for index, path in enumerate(paths):
        if any(path.resolve() == other.resolve()
               or (path.exists() and other.exists() and path.samefile(other))
               for other in paths[:index]):
            raise UsageError("主输出与 DOI 列表不能使用同一个文件")
        if path.is_dir():
            raise UsageError(f"输出目标是目录：{path}（请指定文件路径）")
        if path.exists() and not overwrite:
            raise UsageError(f"输出目标已存在：{path}（覆盖文件请使用 --overwrite）")
        for parent in path.parents:
            if parent.exists() and not parent.is_dir():
                raise UsageError(f"输出父路径不是目录：{parent}")

def resolve_outputs(args: argparse.Namespace, cfg: Config, plan: QueryPlan,
                    cwd: Path | None = None) -> tuple[Path | None, Path | None]:
    """返回 (主输出路径, DOI 列表路径)；--stdout 时两者均为 None。"""
    if args.stdout:
        return None, None

    base_dir = (Path.cwd() if cwd is None else cwd).resolve()
    out_dir = _output_directory(cfg, base_dir)
    if args.output:
        main_path = _absolute_path(Path(args.output), base_dir)
        _render_suffix(main_path)      # 尽早失败：别等检索完才发现后缀不对
    else:
        stamp = _dt.date.today().isoformat()
        slug = query_slug(plan.queries, plan.title)
        main_path = out_dir / f"litsearch_{slug}_{stamp}.md"

    no_doi_list = bool(getattr(args, "no_doi_list", False))
    if args.doi_list is not None:
        if no_doi_list:
            raise UsageError("--doi-list 与 --no-doi-list 不能同时使用")
        if not args.doi_list.strip():
            raise UsageError("--doi-list 路径不能为空；若不需要 DOI 列表请使用 --no-doi-list")
        doi_path: Path | None = _absolute_path(Path(args.doi_list), base_dir)
    elif no_doi_list:
        doi_path = None
    else:
        doi_path = main_path.with_name(main_path.stem + ".doi.txt")
    _check_output_paths([p for p in (main_path, doi_path) if p is not None],
                        getattr(args, "overwrite", False))
    return main_path, doi_path


def write_outputs(main_path: Path | None, doi_path: Path | None,
                  main_text: str, doi_text: str, *, overwrite: bool = False) -> list[Path]:
    written: list[Path] = []
    staged: list[tuple[Path, Path]] = []
    outputs = [(p, t) for p, t in ((main_path, main_text), (doi_path, doi_text)) if p is not None]
    try:
        _check_output_paths([p for p, _ in outputs], overwrite)
        for path, text in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                             dir=path.parent, delete=False) as temporary:
                staged.append((Path(temporary.name), path))
                temporary.write(text)
        for staged_path, path in staged:
            if overwrite:
                os.replace(staged_path, path)
            elif os.name == "nt":
                os.rename(staged_path, path)
            else:
                # POSIX rename 会覆盖已有目标；硬链接提交可以原子地拒绝同名竞争者。
                os.link(staged_path, path)
            written.append(path)
    except OSError as exc:
        raise OutputError(f"输出写入失败：{exc}", written) from exc
    finally:
        for staged_path, _ in staged:
            staged_path.unlink(missing_ok=True)
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

EPILOG = """\
过滤语法：
  --require "a,b" --require "c,d"  组内 OR、组间 AND
  --exclude "x,y"                  命中任一即排除
  默认词首前缀匹配；'=sea' 为整词；'*bacteri*' 为任意位置子串。
  主题过滤作用于合并后的标题与摘要。

候选与结果：
  --limit 是每个来源/查询的候选上限；--max-results 是最终结果上限。
  --fetch-sort 决定远端候选排序，--sort 决定最终展示排序。
  Crossref 与 Europe PMC 本地过滤的扫描预算是 limit 的三倍；执行统计和停止原因见 JSON。

输出与检查：
  默认写 output/ 下的 Markdown 与 DOI 列表（--no-doi-list 可跳过）；已有文件需 --overwrite。
  --stdout 默认 JSON，可用 --stdout-format md|csv|doi。
  --explain 输出脱敏的生效计划，不联网、不写文件。
  --strict-filters 拒绝来源忽略或原始 filter 覆盖的筛选条件。
  --merge-policy doi 保留 DOI 不同的同名文献；默认 title 合并同名版本。

示例：
  litsearch "marine bacteria" --source crossref --limit 10 --stdout
  litsearch '"low nucleic acid content" bacteria' --year 2022-2026 --stdout
  litsearch --preset marine --explain
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litsearch",
        description="多源文献检索工具（OpenAlex + Crossref + Europe PMC），输出人读清单与 DOI 列表",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument("query", nargs="*", default=None,
                        help="自由文本检索词，可给多个（合并去重）；可用 --preset 代替")
    parser.add_argument("--preset", default=None,
                        help="使用配置文件 [presets] 中的命名检索预设")
    parser.add_argument("--source", default=None,
                        help="数据源，逗号分隔（默认 openalex）；可选 openalex,crossref,europepmc")
    parser.add_argument("--year", default=None, help="年份过滤：2024 或 2021-2026")
    parser.add_argument("--limit", type=int, default=None,
                        help="每个查询从远端抓取的候选条数上限（默认 25，可超过 100，自动分页）")
    parser.add_argument("--max-results", dest="max_results", type=int, default=None,
                        help="最终写入清单的条数上限（默认不限）；"
                             "配合 --limit 可「多抓候选、少出结果」")
    parser.add_argument("--min-citations", type=int, default=None, help="最低被引数")
    parser.add_argument("--enrich-abstracts", type=int, default=None, metavar="N",
                        help="按 DOI 用 Europe PMC 补齐空摘要，最多查询 N 个 DOI（默认 0）")
    parser.add_argument("--author", default=None, help="作者（姓名或 OpenAlex 作者 ID）")
    parser.add_argument("--institution", default=None,
                        help="机构（名称、OpenAlex 机构 ID 或 ROR 链接）")
    parser.add_argument("--journal-issn", default=None, help="期刊 ISSN")
    parser.add_argument("--type", dest="work_type", default=None,
                        help="文献类型，如 article / preprint / review（Crossref 仅支持 article、preprint 及原生类型）")
    parser.add_argument("--language", default=None, help="语言，如 en / zh")
    parser.add_argument("--oa-only", action=argparse.BooleanOptionalAction,
                        default=None, help="仅开放获取（--no-oa-only 关闭）")
    parser.add_argument("--oa-filter", default=None, metavar="STR",
                        help="直接透传给 OpenAlex 的原始 filter 字符串（逃生舱，优先级最高）")
    parser.add_argument("--require", action="append", default=None, metavar="A,B",
                        help="相关性分组过滤，可重复；组内逗号分隔为 OR，组间为 AND")
    parser.add_argument("--concept", action="append", default=None, metavar="A,B",
                        help="主题概念同义词组，可重复；按来源生成精查询与宽召回查询，不作硬过滤")
    parser.add_argument("--exclude", action="append", default=None, metavar="X,Y",
                        help="排除词，可重复；命中任一即剔除")
    parser.add_argument("--sort", choices=list(SORT_CHOICES),
                        default=None, help="展示排序（默认 citations）")
    parser.add_argument("--fetch-sort", dest="fetch_sort",
                        choices=list(FETCH_SORT_CHOICES), default=None,
                        help="远端抓取排序，决定取到哪一批候选（默认 relevance）")
    parser.add_argument("--title", default=None, help="Markdown 清单标题")
    parser.add_argument("--output", default=None,
                        help="覆盖主输出路径；后缀 .md/.csv/.json 决定格式")
    parser.add_argument("--doi-list", dest="doi_list", default=None,
                        help="覆盖 DOI 列表输出路径（默认与主输出同目录同名 .doi.txt）")
    parser.add_argument("--no-doi-list", dest="no_doi_list", action="store_true",
                        help="不生成 DOI 列表文件")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出文件")
    parser.add_argument("--explain", action="store_true", help="以 JSON 解释生效计划与来源能力，不联网")
    parser.add_argument("--strict-filters", action=argparse.BooleanOptionalAction,
                        default=None, help="拒绝被来源忽略或原始 filter 覆盖的筛选条件")
    parser.add_argument("--merge-policy", choices=["title", "doi"], default=None,
                        help="去重策略：title 合并同名版本（默认）；doi 仅按 DOI 合并")
    parser.add_argument("--stdout", action="store_true",
                        help="把结果打到 stdout，不写任何文件")
    parser.add_argument("--stdout-format", dest="stdout_format",
                        choices=list(STDOUT_FORMATS), default=None,
                        help="--stdout 的内容格式（默认 json）；md / csv / doi 可选")
    parser.add_argument("--version", action="version",
                        version=f"litsearch {__version__}")
    return parser


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 输出，避免非 ASCII 字符报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace", newline="")
        except (AttributeError, ValueError):
            pass


RENDER_SUFFIXES = (".md", ".csv", ".json")


def _render_suffix(main_path: Path) -> str:
    """校验并返回主输出格式的后缀。"""
    suffix = main_path.suffix.lower()
    if suffix not in RENDER_SUFFIXES:
        raise UsageError(
            f"输出后缀必须是 {' / '.join(RENDER_SUFFIXES)}，"
            f"收到 {suffix or '（无后缀）'}：{main_path}"
        )
    return suffix


def render_main(main_path: Path | None, report: Report,
                records: list[Record]) -> str:
    if main_path is None:                       # 只在意格式时给了无路径的调用方
        return render_markdown(report, records)
    suffix = _render_suffix(main_path)
    if suffix == ".csv":
        return render_csv(records)
    if suffix == ".json":
        return render_json(report, records)
    return render_markdown(report, records)


STDOUT_FORMATS = ("json", "md", "csv", "doi")


def render_stdout(fmt: str, report: Report, records: list[Record]) -> str:
    """--stdout 的内容格式。默认 json 便于 agent 解析；md / csv / doi 便于直读或管道。"""
    if fmt == "md":
        return render_markdown(report, records)
    if fmt == "csv":
        return render_csv(records)
    if fmt == "doi":
        return render_doi_list(records)[0].rstrip("\n")
    return render_json(report, records)


def run(args: argparse.Namespace, cwd: Path, http: Http,
        policy: RetryPolicy, sleep_fn: Callable[[float], None]) -> int:
    no_doi_list = bool(getattr(args, "no_doi_list", False))
    if args.stdout and (args.output or args.doi_list is not None or no_doi_list):
        raise UsageError("--stdout 与 --output / --doi-list / --no-doi-list 不能同时使用")
    if args.stdout_format and not args.stdout:
        raise UsageError("--stdout-format 需要与 --stdout 一起使用")

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
    http.sleep = sleep_fn
    client = SearchClient(cfg, http, retry=policy)
    if args.explain:
        if args.output or args.doi_list is not None or no_doi_list or args.stdout_format or args.overwrite:
            raise UsageError("--explain 与文件输出、输出格式和覆盖选项互斥")
        explanation = client.explain(plan)
        explanation["output"] = {
            "directory": str(_output_directory(cfg, cwd)),
            "origin": cfg.origins.get("output_dir", "default"),
        }
        print(json.dumps(explanation, ensure_ascii=False, indent=2))
        return EXIT_OK
    main_path, doi_path = resolve_outputs(args, cfg, plan, cwd)
    result = client.search(plan)
    ordered, report = result.records, result.report
    stats = report.stats
    fetched = sum(execution.fetched_count for execution in report.queries)
    for message in dict.fromkeys(d.message for d in report.diagnostics):
        print(f"[warn] {message}", file=sys.stderr)

    if args.stdout:
        text = render_stdout(args.stdout_format or "json", report, ordered)
        if args.stdout_format == "csv":
            sys.stdout.write(text)
        else:
            print(text)
        print(
            f"[ok] 共 {len(ordered)} 篇（抓取 {fetched} 条，进入合并 {stats.total_in} 条，"
            f"DOI 合并 {stats.doi_merged}，标题合并 {stats.title_merged}）",
            file=sys.stderr,
        )
        return EXIT_OK

    doi_text = "" if doi_path is None else render_doi_list(ordered)[0]
    written = write_outputs(
        main_path, doi_path,
        render_main(main_path, report, ordered), doi_text,
        overwrite=args.overwrite,
    )
    for path in written:
        print(str(path))
    print(
        f"[ok] 共 {len(ordered)} 篇（抓取 {fetched} 条，进入合并 {stats.total_in} 条，"
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
    except OutputError as exc:
        for path in exc.written:
            print(str(path))
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_OUTPUT
    except KeyboardInterrupt:
        print("[error] 已中断", file=sys.stderr)
        return EXIT_NET


if __name__ == "__main__":
    sys.exit(main())
