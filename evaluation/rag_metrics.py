"""Deterministic retrieval and grounded-answer metrics for regression suites."""
from __future__ import annotations

import math
from typing import Iterable, Sequence


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant_set = {item for item in relevant if item}
    if not relevant_set:
        return 1.0
    hits = relevant_set.intersection(retrieved[:max(0, k)])
    return len(hits) / len(relevant_set)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    relevant_set = set(relevant)
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    limited = retrieved[:max(0, k)]
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(limited, start=1)
        if item in relevant_set
    )
    ideal_hits = min(len(relevant_set), max(0, k))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0


def citation_precision(cited: Sequence[str], relevant: Iterable[str]) -> float:
    if not cited:
        return 1.0 if not set(relevant) else 0.0
    relevant_set = set(relevant)
    return sum(1 for item in cited if item in relevant_set) / len(cited)


def citation_recall(cited: Sequence[str], relevant: Iterable[str]) -> float:
    return recall_at_k(cited, relevant, len(cited))


def grounded_fact_score(answer: str, evidence: Sequence[str], expected_facts: Sequence[str]) -> float:
    """Measure whether expected facts are present in both the answer and retrieved evidence."""
    facts = [fact.strip() for fact in expected_facts if fact.strip()]
    if not facts:
        return 1.0
    evidence_text = "\n".join(evidence)
    supported = sum(1 for fact in facts if fact in answer and fact in evidence_text)
    return supported / len(facts)


def answer_relevance_score(answer: str, expected_facts: Sequence[str]) -> float:
    facts = [fact.strip() for fact in expected_facts if fact.strip()]
    if not facts:
        return 1.0 if answer.strip() else 0.0
    return sum(1 for fact in facts if fact in answer) / len(facts)


def retrieval_metrics(retrieved: Sequence[str], relevant: Sequence[str], k: int = 3) -> dict[str, float]:
    return {
        f"recall@{k}": round(recall_at_k(retrieved, relevant, k), 4),
        "mrr": round(reciprocal_rank(retrieved, relevant), 4),
        f"ndcg@{k}": round(ndcg_at_k(retrieved, relevant, k), 4),
        "citation_precision": round(citation_precision(retrieved[:k], relevant), 4),
        "citation_recall": round(citation_recall(retrieved[:k], relevant), 4),
    }
