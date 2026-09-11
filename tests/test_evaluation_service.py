from evaluation.evaluation_service import EvaluationService


def test_runtime_metadata_matches_the_provider_that_served_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "remote-model")
    service = EvaluationService(
        workflow=None,
        repository=None,
        project_root=str(tmp_path),
        model_provider="local-continuity",
    )
    metadata = service.runtime_metadata("rag")
    assert metadata["dataset_name"] == "rag"
    assert metadata["model_provider"] == "local-continuity"
    assert metadata["model_version"] == "local-continuity-rules-v1"
