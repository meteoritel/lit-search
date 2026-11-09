"""稀疏金标准与未判读结果的质量统计。"""

import runpy
from pathlib import Path

import pytest


evaluation = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/evaluate_retrieval.py"))


def test_recall_uses_both_positive_tiers_and_doi_aliases():
    metrics = evaluation["evaluate"]([
        {"doi": "https://doi.org/10.1/A", "doi_aliases": ["10.1/a"]},
        {"doi": "10.1/variant", "doi_aliases": ["10.1/b"]},
        {"doi": "10.1/outside"},
    ], {"10.1/a": 1, "10.1/b": 2, "10.1/c": 3})
    assert metrics["tier1_recall"] == 1
    assert metrics["tier12_recall"] == 1
    assert metrics["unjudged"] == ["10.1/outside"]
    assert metrics["strict_p_at_20"] is None
    assert metrics["ndcg_at_20"] is None


def test_judged_ranking_metrics_and_duplicate_records():
    metrics = evaluation["evaluate"]([
        {"doi": "10.1/a"}, {"doi": "10.1/a"}, {"doi": "10.1/b"}, {"doi": "10.1/c"},
    ], {"10.1/a": 1, "10.1/b": 2, "10.1/c": 3})
    assert metrics["strict_p_at_20"] == pytest.approx(1 / 3)
    assert metrics["inclusive_p_at_20"] == pytest.approx(2 / 3)
    assert metrics["ndcg_at_20"] == 1


def test_conflicting_labels_are_rejected():
    with pytest.raises(ValueError, match="冲突"):
        evaluation["load_labels"]({"records": [
            {"doi": "10.1/a", "tier": 1}, {"doi": "https://doi.org/10.1/A", "tier": 2},
        ]})
