from monitor.performance_monitor import PerformanceMonitor


class EmptyStats:
    def get_stats(self):
        return {}


def test_threshold_alert_is_deduplicated_and_resolved():
    monitor = PerformanceMonitor(EmptyStats(), EmptyStats())
    monitor._check_threshold("agent_success_rate", 0.5, "technical_0")
    monitor._check_threshold("agent_success_rate", 0.4, "technical_0")
    assert len(monitor._alerts) == 1
    assert monitor._alerts[0].value == 0.4

    monitor._check_threshold("agent_success_rate", 0.99, "technical_0")
    assert monitor._alerts[0].resolved is True
    assert monitor._alerts[0].resolved_at is not None

    before = monitor._prom["requests_total"].labels(
        method="POST", path="/chat", status="200"
    )._value.get()
    monitor.record_request("POST", "/chat", 200, 125.0)
    after = monitor._prom["requests_total"].labels(
        method="POST", path="/chat", status="200"
    )._value.get()
    assert after == before + 1
