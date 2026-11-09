"""按 DOI 标签评测检索结果；未判读记录单独报告。"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def norm_doi(value: str | None) -> str:
    value = (value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def load_labels(document: dict) -> dict[str, int]:
    labels: dict[str, int] = {}
    for record in document["records"]:
        doi, tier = norm_doi(record.get("doi")) or record.get("identity", ""), record["tier"]
        if not doi or type(tier) is not int or tier not in (1, 2, 3):
            raise ValueError("标签必须包含 DOI/来源身份与 tier 1/2/3")
        if doi in labels and labels[doi] != tier:
            raise ValueError(f"同一 DOI 的标签冲突：{doi}")
        labels[doi] = tier
    return labels


def evaluate(records: list[dict], labels: dict[str, int]) -> dict:
    identities: set[str] = set()
    found: set[str] = set()
    ranked: list[tuple[str, int | None]] = []
    for record in records:
        aliases = {norm_doi(v) for v in [record.get("doi"), *record.get("doi_aliases", [])]}
        aliases.discard("")
        identity = norm_doi(record.get("doi")) or (
            f"{record.get('source', '')}:{record.get('source_id', '')}" if record.get("source_id")
            else f"title:{record.get('title', '')}"
        )
        if identity in identities or aliases & found:
            continue
        identities.add(identity)
        found.update(aliases)
        found.add(identity)
        tiers = {labels[d] for d in aliases | {identity} if d in labels}
        if len(tiers) > 1:
            raise ValueError(f"合并身份包含冲突标签：{identity}")
        ranked.append((identity, next(iter(tiers)) if tiers else None))
    core = {d for d, tier in labels.items() if tier == 1}
    relevant = {d for d, tier in labels.items() if tier <= 2}
    top = ranked[:20]
    judged = [tier for _, tier in top if tier is not None]
    complete = bool(top) and len(judged) == len(top)
    gains = {1: 3, 2: 1, 3: 0}
    dcg = sum(gains.get(tier, 0) / math.log2(i + 2) for i, (_, tier) in enumerate(top))
    ideal = sorted((gains[tier] for tier in labels.values()), reverse=True)[:len(top)]
    idcg = sum(gain / math.log2(i + 2) for i, gain in enumerate(ideal))
    return {
        "count": len(ranked),
        "tier1_hits": sorted(found & core),
        "tier12_hits": sorted(found & relevant),
        "tier1_recall": len(found & core) / len(core) if core else None,
        "tier12_recall": len(found & relevant) / len(relevant) if relevant else None,
        "unjudged": [identity for identity, tier in ranked if tier is None],
        "top_k": len(top),
        "judged_at_20": len(judged),
        "strict_p_at_20": judged.count(1) / len(top) if complete else None,
        "inclusive_p_at_20": sum(t <= 2 for t in judged) / len(top) if complete else None,
        "ndcg_at_20": dcg / idcg if complete and idcg else None,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()
    labels = load_labels(json.loads(args.gold.read_text(encoding="utf-8")))
    reports = {}
    for path in args.results:
        document = json.loads(path.read_text(encoding="utf-8"))
        reports[str(path)] = evaluate(document["results"], labels)
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
