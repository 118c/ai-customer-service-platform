from api.main import app


def test_evaluation_management_routes_are_published():
    paths = app.openapi()["paths"]
    assert "post" in paths["/admin/evaluations/run"]
    assert "post" in paths["/admin/evaluations/rag/run"]
    assert "get" in paths["/admin/evaluations/{run_id}/failures"]
