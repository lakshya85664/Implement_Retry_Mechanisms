"""Tests for workflow observability, metrics, traces, and JSON exports."""

import json

from retry_engine import PermanentError, RetryableError
from retry_observability import WorkflowMonitor


def test_successful_workflow_is_recorded():
    monitor = WorkflowMonitor()

    record = monitor.execute_scenario(
        "success",
        "SHIP-5001",
    )

    assert record["status"] == "completed"
    assert record["attempts"] == 1
    assert record["provider_calls"] == 1
    assert record["label_id"] == "LABEL-SHIP-5001"


def test_transient_failure_then_success_is_recorded():
    monitor = WorkflowMonitor()

    record = monitor.execute_scenario(
        "retry_then_success",
        "SHIP-5002",
        [
            RetryableError("Temporary outage"),
            {"label_id": "LABEL-SHIP-5002"},
        ],
    )

    assert record["status"] == "completed"
    assert record["attempts"] == 2
    assert record["provider_calls"] == 2


def test_permanent_failure_is_recorded():
    monitor = WorkflowMonitor()

    record = monitor.execute_scenario(
        "permanent_failure",
        "SHIP-5003",
        [PermanentError("Unsupported service")],
    )

    assert record["status"] == "needs_review"
    assert record["attempts"] == 1
    assert record["error_type"] == "PermanentError"


def test_metrics_are_calculated_correctly():
    monitor = WorkflowMonitor()

    monitor.execute_scenario("success", "SHIP-5011")

    monitor.execute_scenario(
        "retry_then_success",
        "SHIP-5012",
        [
            RetryableError("Temporary outage"),
            {"label_id": "LABEL-SHIP-5012"},
        ],
    )

    monitor.execute_scenario(
        "permanent_failure",
        "SHIP-5013",
        [PermanentError("Unsupported service")],
    )

    metrics = monitor.build_metrics()

    assert metrics["total_runs"] == 3
    assert metrics["completed_runs"] == 2
    assert metrics["failed_runs"] == 1
    assert metrics["success_rate_percent"] == 66.67
    assert metrics["failure_rate_percent"] == 33.33
    assert metrics["total_attempts"] == 4
    assert metrics["trace_count"] == 3
    assert metrics["average_attempts_per_run"] == 1.33
    assert metrics["average_latency_ms"] >= 0


def test_trace_lookup_returns_correct_trace():
    monitor = WorkflowMonitor()

    monitor.execute_scenario("success", "SHIP-5021")

    trace = monitor.get_trace("TRACE-001")

    assert trace is not None
    assert trace["scenario"] == "success"
    assert trace["shipment_id"] == "SHIP-5021"
    assert len(trace["events"]) >= 2


def test_unknown_trace_returns_none():
    monitor = WorkflowMonitor()

    assert monitor.get_trace("TRACE-UNKNOWN") is None


def test_status_lookup_returns_run_status():
    monitor = WorkflowMonitor()

    record = monitor.execute_scenario(
        "success",
        "SHIP-5031",
    )

    status = monitor.get_status(record["run_id"])

    assert status is not None
    assert status["run_id"] == record["run_id"]
    assert status["status"] == "completed"


def test_unknown_run_status_returns_none():
    monitor = WorkflowMonitor()

    assert monitor.get_status("RUN-UNKNOWN") is None


def test_json_exports_are_created_and_valid(tmp_path):
    monitor = WorkflowMonitor()

    monitor.execute_scenario("success", "SHIP-5041")

    files = monitor.export(tmp_path)

    assert set(files) == {"traces", "run_records", "metrics"}

    traces = json.loads(
        (tmp_path / "task4_traces.json").read_text(encoding="utf-8")
    )
    records = json.loads(
        (tmp_path / "task4_run_records.json").read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (tmp_path / "task4_metrics.json").read_text(encoding="utf-8")
    )

    assert len(traces) == 1
    assert len(records) == 1
    assert metrics["trace_count"] == 1
    assert records[0]["status"] == "completed"


def test_trace_events_have_structured_fields():
    monitor = WorkflowMonitor()

    monitor.execute_scenario("success", "SHIP-5051")

    trace = monitor.get_trace("TRACE-001")

    for event in trace["events"]:
        assert "timestamp" in event
        assert "component" in event
        assert "event" in event
        assert "shipment_id" in event
        assert "idempotency_key" in event
