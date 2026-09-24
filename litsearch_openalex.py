#!/usr/bin/env python3
"""本地 OpenAlex 文献检索小工具（2026 版 API key 计费模型）。

用途：按自由文本 + 年份检索 OpenAlex 学术文献，支持多查询合并、去重与
     相关性分组过滤，输出 Markdown/CSV 清单或结构化 JSON，
     用于海洋扩增子/ASV/真核-原核交互等主题的文献调研。
API key 通过环境变量读取，不写入脚本，也不暴露在对话中：
    OPENALEX_API_KEY  (Windows 用户级环境变量，见 memory/openalex-api-key-model.md)

用法：
    # 单查询，JSON 输出（向后兼容）
    python scripts/litsearch_openalex.py "marine amplicon ASV protist prokaryote interaction" \
        --year 2021-2026 --limit 30

    # 多查询批量 + 去重 + 分组相关性过滤 + 输出 Markdown 清单
    python scripts/litsearch_openalex.py "marine protist prokaryote interaction" \
        "nanoflagellate grazing bacteria" --year 2021-2026 --limit 12 \
        --include-groups "amplicon,asv,16s,18s,metabarcoding;protist,eukaryot,prokaryot,bacteria,flagellate;interaction,grazing,bacterivor,network,trophic" \
        --output docs/literature/marine_amplicon_euk_prok_2026.md --title "海洋扩增子真核-原核交互文献清单"

说明：
- 匿名访问 2026 年起被限流(429)，必须提供 OPENALEX_API_KEY。
- key 以 api_key 查询参数传递（官方认证文档方式）。
- 结果含重建摘要(OpenAlex inverted index)、被引数、期刊、DOI、OA 链接。
- --include-groups 用 ";" 分组成 AND、组内用 "," 分隔 OR：结果须命中全部组才保留。
- --exclude-terms 用 "," 分隔 OR：结果命中任一即剔除。
- --output 按后缀决定格式：.md -> Markdown 表格，.csv -> CSV，其它后缀打印 JSON。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request

API_BASE = "https://api.openalex.org"
DEFAULT_SELECT = (
    "id,title,publication_year,publication_date,cited_by_count,doi,"
    "primary_location,authorships,abstract_inverted_index,open_access,type,relevance_score"
)
# OpenAlex 硬限制 per_page <= 100
MAX_PER_PAGE = 100


def reconstruct_abstract(inv_idx: dict | None) -> str:
    """OpenAlex 摘要为倒排索引 {word: [positions]}，重建为正常语序文本。"""
    if not inv_idx:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in inv_idx.items():
        for p in positions:
            slots.append((p, word))
    slots.sort()
    return " ".join(w for _, w in slots)


def build_url(query: str, year: str | None, limit: int,
              min_citations: int | None, api_key: str) -> str:
    params = {
        "search": query,
        "per_page": str(max(1, min(MAX_PER_PAGE, limit))),
        "sort": "relevance_score:desc",
        "select": DEFAULT_SELECT,
    }
    filters: list[str] = []
    if year:
        if "-" in year:
            lo, hi = year.split("-", 1)
            filters.append(f"from_publication_date:{lo}-01-01")
            filters.append(f"to_publication_date:{hi}-12-31")
        else:
            filters.append(f"from_publication_date:{year}-01-01")
            filters.append(f"to_publication_date:{year}-12-31")
    if min_citations is not None:
        filters.append(f"cited_by_count:>{max(0, min_citations - 1)}")
    if filters:
        params["filter"] = ",".join(filters)
    if api_key:
        params["api_key"] = api_key  # 官方认证方式：api_key 查询参数
    return f"{API_BASE}/works?{urllib.parse.urlencode(params)}"


def fetch(query: str, year: str | None, limit: int,
          min_citations: int | None, api_key: str) -> tuple[int, list[dict]]:
    url = build_url(query, year, limit, min_citations, api_key)
    req = urllib.request.Request(
        url, headers={"User-Agent": "thesis-litsearch/1.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        print(f"[error] OpenAlex HTTP {e.code}: {e.reason}", file=sys.stderr)
        if e.code == 429:
            print("[error] 429 = 超每日预算或访问过频，请检查 OPENALEX_API_KEY", file=sys.stderr)
        sys.exit(4)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[error] 请求失败: {e}", file=sys.stderr)
        sys.exit(4)

    results: list[dict] = []
    for r in payload.get("results", []):
        pl = r.get("primary_location") or {}
        src = pl.get("source") or {}
        oa = r.get("open_access") or {}
        results.append({
            "openalex_id": r.get("id"),
            "title": r.get("title"),
            "year": r.get("publication_year"),
            "publication_date": r.get("publication_date"),
            "venue": src.get("display_name"),
            "work_type": r.get("type"),
            "citation_count": r.get("cited_by_count"),
            "relevance_score": r.get("relevance_score"),
            "doi": (r.get("doi") or "").replace("https://doi.org/", "") or None,
            "is_open_access": oa.get("is_oa"),
            "open_access_pdf": oa.get("oa_url") or "",
            "authors": [
                (a.get("author") or {}).get("display_name")
                for a in (r.get("authorships") or [])
            ],
            "abstract": reconstruct_abstract(r.get("abstract_inverted_index")),
        })
    total = (payload.get("meta") or {}).get("count", 0)
    return total, results


def norm_title(rec: dict) -> str:
    """标题归一化：小写、去掉所有非字母数字（连字符/变音符号等统一忽略）。"""
    title = rec.get("title") or ""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def dedup_key(rec: dict) -> str:
    """去重键：归一化标题优先（合并 preprint/正式版等不同 DOI 的同文），DOI 兜底。"""
    t = norm_title(rec)
    if t:
        return f"title:{t}"
    if rec.get("doi"):
        return f"doi:{rec['doi'].lower()}"
    return f"id:{rec.get('openalex_id', '')}"


def _split_groups(spec: str) -> list[list[str]]:
    """--include-groups 'a1,a2;a3,a4' -> [['a1','a2'], ['a3','a4']]。"""
    groups = []
    for g in spec.split(";"):
        terms = [t.strip().lower() for t in g.split(",") if t.strip()]
        if terms:
            groups.append(terms)
    return groups


def _split_or(spec: str) -> list[str]:
    return [t.strip().lower() for t in spec.split(",") if t.strip()]


def matches(text: str, include_groups: list[list[str]],
            exclude_terms: list[str]) -> bool:
    """相关性过滤：须命中所有 include 组（组内任一），且不命中任一 exclude 词。"""
    tl = text.lower()
    if exclude_terms and any(t in tl for t in exclude_terms):
        return False
    if include_groups:
        for group in include_groups:
            if not any(t in tl for t in group):
                return False
    return True


def dedup_and_filter(records: list[dict], include_groups: list[list[str]],
                     exclude_terms: list[str], sort: str) -> list[dict]:
    # 先去重（标题为主键）：按被引降序预处理，保证同一论文保留被引最高版本
    records = sorted(
        records, key=lambda r: (r.get("citation_count") or 0), reverse=True)
    seen: set[str] = set()
    out: list[dict] = []
    for rec in records:
        text = f"{rec.get('title') or ''} {rec.get('abstract') or ''}"
        if not matches(text, include_groups, exclude_terms):
            continue
        key = dedup_key(rec)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    if sort == "citations":
        out.sort(key=lambda r: r.get("citation_count") or 0, reverse=True)
    elif sort == "year":
        out.sort(key=lambda r: (r.get("year") or 0), reverse=True)
    elif sort == "relevance":
        out.sort(key=lambda r: r.get("relevance_score") or 0, reverse=True)
    return out


def render_markdown(title: str, queries: list[tuple[str, int]], records: list[dict],
                    year: str | None, include_groups: list[list[str]],
                    exclude_terms: list[str]) -> str:
    today = __import__("datetime").date.today().isoformat()
    lines: list[str] = [f"# {title}", "", f"> 最近更新：{today}。", ""]
    lines.append("## 检索说明")
    lines.append("")
    lines.append("数据来源：OpenAlex API（脚本 `scripts/litsearch_openalex.py`，key 计费模型）。")
    lines.append("")
    lines.append(f"- 年份范围：{year or '不限'}")
    if include_groups:
        groups = "；".join(" 或 ".join(g) for g in include_groups)
        lines.append(f"- 相关性分组过滤（须命中全部组）：{groups}")
    if exclude_terms:
        lines.append(f"- 排除词：{'、'.join(exclude_terms)}")
    lines.append("")
    lines.append("各查询命中数（OpenAlex 原始 total）：")
    lines.append("")
    for q, total in queries:
        lines.append(f"- `{q}` -> {total} 篇")
    lines.append("")
    lines.append(f"合并去重并过滤后：**{len(records)} 篇**，按被引数降序。")
    lines.append("")
    lines.append("| # | 年份 | 标题 | 期刊 | 被引 | DOI | 开放获取 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(records, 1):
        title = (r.get("title") or "").replace("|", "\\|")
        venue = (r.get("venue") or "").replace("|", "\\|")
        doi = r.get("doi") or ""
        oa = "是" if r.get("is_open_access") else ""
        lines.append(
            f"| {i} | {r.get('year') or ''} | {title} | {venue} "
            f"| {r.get('citation_count') or 0} | {doi} | {oa} |"
        )
    return "\n".join(lines) + "\n"


def render_csv(records: list[dict]) -> str:
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "year", "publication_date", "venue", "citation_count",
                     "doi", "is_open_access", "open_access_pdf", "authors", "abstract"])
    for r in records:
        writer.writerow([
            r.get("title") or "",
            r.get("year") or "",
            r.get("publication_date") or "",
            r.get("venue") or "",
            r.get("citation_count") or 0,
            r.get("doi") or "",
            r.get("is_open_access") or "",
            r.get("open_access_pdf") or "",
            "; ".join(r.get("authors") or []),
            r.get("abstract") or "",
        ])
    return buf.getvalue()


def main() -> int:
    # Windows 控制台默认 GBK，强制 UTF-8 输出，避免 − 等字符触发 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="OpenAlex 文献检索工具")
    parser.add_argument("query", nargs="+", help="自由文本检索词（可多个，自动合并去重）")
    parser.add_argument("--year", default=None,
                        help="年份过滤，如 2023-2026 或 2024")
    parser.add_argument("--limit", type=int, default=25,
                        help="每个查询返回条数 1-100（默认 25）")
    parser.add_argument("--min-citations", type=int, default=None,
                        help="最低被引数过滤")
    parser.add_argument("--include-groups", default=None,
                        help="相关性分组过滤：';' 分组成 AND，组内 ',' 分隔为 OR，"
                             "须命中全部组；如 'amplicon,asv,16s;protist,eukaryot;grazing,network'")
    parser.add_argument("--exclude-terms", default=None,
                        help="排除词：',' 分隔，结果命中任一即剔除")
    parser.add_argument("--sort", choices=["citations", "year", "relevance"],
                        default="citations", help="合并后的排序（默认被引降序）")
    parser.add_argument("--title", default=None,
                        help="Markdown 清单标题（默认取第一个查询词）")
    parser.add_argument("--output", default=None,
                        help="输出文件路径；.md -> Markdown 表格，.csv -> CSV，其它打印 JSON")
    args = parser.parse_args()

    api_key = os.getenv("OPENALEX_API_KEY", "")
    if not api_key:
        print("[warning] OPENALEX_API_KEY 未设置，匿名访问可能返回 429", file=sys.stderr)

    include_groups = _split_groups(args.include_groups) if args.include_groups else []
    exclude_terms = _split_or(args.exclude_terms) if args.exclude_terms else []

    queries_totals: list[tuple[str, int]] = []
    records: list[dict] = []
    for q in args.query:
        total, results = fetch(q, args.year, args.limit, args.min_citations, api_key)
        queries_totals.append((q, total))
        records.extend(results)

    final = dedup_and_filter(records, include_groups, exclude_terms, args.sort)

    if args.output:
        if args.output.endswith(".md"):
            title = args.title or args.query[0]
            with open(args.output, "w", encoding="utf-8", newline="") as f:
                f.write(render_markdown(title, queries_totals, final, args.year,
                                        include_groups, exclude_terms))
        elif args.output.endswith(".csv"):
            with open(args.output, "w", encoding="utf-8", newline="") as f:
                f.write(render_csv(final))
        else:
            payload = {
                "queries": [{"query": q, "total_count": t} for q, t in queries_totals],
                "filters": {
                    "year": args.year,
                    "include_groups": include_groups,
                    "exclude_terms": exclude_terms,
                    "min_citations": args.min_citations,
                },
                "count": len(final),
                "results": final,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"[ok] 写入 {args.output}（合并去重后 {len(final)} 条）", file=sys.stderr)
    else:
        payload = {
            "queries": [{"query": q, "total_count": t} for q, t in queries_totals],
            "count": len(final),
            "results": final,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
