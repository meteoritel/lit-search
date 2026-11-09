"""在固定候选快照上对照摘要补齐、筛选与排序，不发送网络请求。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import litsearch
from evaluate_retrieval import evaluate, load_labels


def compare_allocations(documents: list[dict], labels: dict[str, int]) -> dict:
    streams: dict[tuple[str, str], dict[int, dict]] = {}
    for document in documents:
        for record in document["results"]:
            for hit in record.get("retrieval_hits", []):
                streams.setdefault((hit["source"], hit["query"]), {})[hit["rank"]] = record
    lists = [list(row for _, row in sorted(stream.items())) for stream in streams.values()]
    report = {}
    for budget in (200, 300, 400):
        policies = {}
        for policy in ("round_robin", "novelty"):
            positions = [0] * len(lists)
            utilities = [1.0] * len(lists)
            seen: set[str] = set()
            selected: list[dict] = []
            remaining, turn = budget, 0
            while remaining and any(pos < len(rows) for pos, rows in zip(positions, lists)):
                available = [i for i, rows in enumerate(lists) if positions[i] < len(rows)]
                unexplored = [i for i in available if positions[i] == 0]
                if unexplored:
                    index = unexplored[0]
                elif policy == "novelty":
                    index = max(available, key=lambda i: (utilities[i], -i))
                else:
                    index = next((i for i in available if i >= turn), available[0])
                page = lists[index][positions[index]:positions[index] + min(50, remaining)]
                new = 0
                for record in page:
                    identity = litsearch.norm_doi(record.get("doi")) or f"{record.get('source', '')}:{record.get('source_id', '')}"
                    if identity not in seen:
                        new += 1
                        seen.add(identity)
                    selected.append(record)
                utilities[index] = new / len(page)
                positions[index] += len(page)
                remaining -= len(page)
                turn = index + 1
            policies[policy] = evaluate(selected, labels)
        report[str(budget)] = policies
    return report


def compare(documents: list[dict], labels: dict[str, int], metadata: list[dict]) -> dict:
    known_fields = set(litsearch.RECORD_FIELDS)
    records = [litsearch.Record(**{key: value for key, value in row.items() if key in known_fields})
               for document in documents for row in document["results"]]
    records, _ = litsearch.merge_records(records, "doi")
    plan = litsearch.QueryPlan(
        queries=list(dict.fromkeys(q["query"] for doc in documents for q in doc.get("queries", []))),
        sources=list(dict.fromkeys(s for doc in documents for s in doc["filters"]["sources"])),
        concepts=documents[0]["filters"].get("concepts", []),
        require=documents[0]["filters"].get("require", []),
        exclude=documents[0]["filters"].get("exclude", []),
    )
    abstracts = {litsearch.norm_doi(row.get("doi")): row for row in metadata if row.get("abstract")}
    enriched = []
    for record in records:
        cached = abstracts.get(litsearch.norm_doi(record.doi))
        if not record.abstract and cached:
            record = replace(record, abstract=cached["abstract"],
                             abstract_source=cached.get("abstract_source") or cached.get("source") or "cache")
        enriched.append(record)
    report = {}
    for name, pool in (("original", records), ("abstract_backfill", enriched)):
        filtered, stats = litsearch.apply_filters(pool, plan.require, plan.exclude)
        rankings = {}
        for sort in ("relevance", "rrf", "bm25", "hybrid"):
            result = litsearch.rank_records(filtered, replace(plan, sort=sort))
            rows = [litsearch.asdict(record) for record in result]
            rankings[sort] = {"metrics": evaluate(rows, labels), "top20": rows[:20]}
        report[name] = {"before_filter": evaluate([litsearch.asdict(r) for r in pool], labels),
                        "filter_stats": litsearch.asdict(stats), "rankings": rankings}
    if any(record.retrieval_hits for record in records):
        report["allocation_experiment"] = compare_allocations(documents, labels)
    return report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--require-from", type=Path)
    parser.add_argument("snapshots", type=Path, nargs="+")
    args = parser.parse_args()
    labels = load_labels(json.loads(args.gold.read_text(encoding="utf-8")))
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in args.snapshots]
    if args.require_from:
        document = json.loads(args.require_from.read_text(encoding="utf-8"))
        filters = document.get("filters", document)
        documents[0]["filters"]["require"] = filters["require"]
        documents[0]["filters"]["exclude"] = filters.get("exclude", [])
    metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else []
    print(json.dumps(compare(documents, labels, metadata), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
