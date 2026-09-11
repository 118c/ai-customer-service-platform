import pytest

from evaluation.rag_metrics import (
    citation_precision,
    grounded_fact_score,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_rank_metrics_reward_early_relevant_documents():
    retrieved = ["doc-x", "doc-a", "doc-b"]
    relevant = ["doc-a", "doc-b"]
    assert recall_at_k(retrieved, relevant, 2) == 0.5
    assert reciprocal_rank(retrieved, relevant) == 0.5
    assert ndcg_at_k(retrieved, relevant, 3) == pytest.approx(0.6934, abs=0.001)
    assert citation_precision(retrieved[:2], relevant) == 0.5


def test_grounded_fact_score_requires_answer_and_evidence_support():
    score = grounded_fact_score(
        "跨日夜班按班次开始日期计算，漏卡两日内补办。",
        ["跨日夜班以班次开始日期归属考勤日。"],
        ["班次开始日期", "两日内补办"],
    )
    assert score == 0.5
