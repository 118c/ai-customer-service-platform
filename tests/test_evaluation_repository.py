from evaluation.repository import EvaluationRepository


def test_evaluation_repository_persists_runs_and_trends(tmp_path):
    repository = EvaluationRepository(str(tmp_path / "evaluations.db"))
    run = {
        "run_id": "run-1",
        "suite": "full-workflow",
        "dataset_version": "2026.09",
        "started_at": "2026-09-11T01:00:00+00:00",
        "completed_at": "2026-09-11T01:01:00+00:00",
        "status": "completed",
        "pass_rate": 1.0,
        "metrics": {"recall@3": 1.0},
        "metadata": {"case_count": 1},
        "results": [{
            "case_id": "rag-1", "passed": True, "latency_ms": 20.0,
            "checks": {"intent": True}, "metrics": {"recall@3": 1.0}, "detail": "",
            "request_ids": ["request-1"], "trace_ids": ["request-1"],
            "metadata": {"conversation_id": "conversation-1"},
        }],
    }
    repository.save_run(run)

    loaded = repository.get_run("run-1")
    assert loaded is not None
    assert loaded["metrics"]["recall@3"] == 1.0
    assert loaded["results"][0]["checks"]["intent"] is True
    assert loaded["results"][0]["request_ids"] == ["request-1"]
    assert loaded["results"][0]["trace_ids"] == ["request-1"]
    assert repository.trends()[0]["dataset_version"] == "2026.09"
    assert repository.failures("run-1") == []
