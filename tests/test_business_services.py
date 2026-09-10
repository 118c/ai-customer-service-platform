from integrations.business_services import LocalReferenceGateway, plan_business_action


async def test_reference_gateway_queries_seeded_workflow(tmp_path):
    gateway = LocalReferenceGateway(str(tmp_path / "services.db"))
    result = await gateway.query("workflow.lookup", {"workflow_id": "WF-202609-001"})
    assert result["found"] is True
    assert result["record"]["current_node"] == "部门主管"


async def test_reviewed_repair_action_creates_order(tmp_path):
    gateway = LocalReferenceGateway(str(tmp_path / "services.db"))
    action = plan_business_action(
        "repair_request",
        "设备 EQ-A17 主轴异响",
        "E1001",
        "conv-1",
        {"equipment_id": ["EQ-A17"]},
    )
    assert action is not None and action.requires_review is True
    result = await gateway.execute(action)
    assert result["success"] is True
    assert result["record_id"].startswith("REP-")
