import json
from pathlib import Path

from core.llm_gateway import LocalContinuityProvider, ResilientLLMGateway
from core.intent_recognizer import IntentRecognizer
from evaluation.evaluator import IntentEvaluator, IntentTestCase


async def test_local_provider_classifies_factory_intents():
    gateway = ResilientLLMGateway([LocalContinuityProvider()])
    raw = await gateway.complete(
        system='只返回 JSON，字段包含 "intent"',
        messages=[{"role": "user", "content": "帮我给设备 EQ-A17 创建报修单"}],
    )
    assert '"intent": "repair_request"' in raw
    assert gateway.last_provider == "local-continuity"


async def test_local_provider_handles_approval_request():
    gateway = ResilientLLMGateway([LocalContinuityProvider()])
    raw = await gateway.complete(
        system='只返回 JSON，字段包含 "intent"',
        messages=[{"role": "user", "content": "帮我提交领料申请"}],
    )
    assert '"intent": "approval_request"' in raw


async def test_factory_intent_baseline_is_reproducible():
    gateway = ResilientLLMGateway([LocalContinuityProvider()])
    recognizer = IntentRecognizer(api_key="offline", llm_gateway=gateway)
    rows = [json.loads(line) for line in Path("evaluation/datasets/factory_intents.jsonl").read_text(encoding="utf-8").splitlines()]
    cases = [IntentTestCase(row["message"], row["expected_intent"]) for row in rows]
    report = await IntentEvaluator(recognizer).evaluate(cases)
    assert report["accuracy"] >= 0.92, report
