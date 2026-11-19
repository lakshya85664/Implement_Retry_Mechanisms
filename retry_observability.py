"""Workflow observability: structured traces, metrics, and JSON reports."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retry_adapters import FakeShippingProviderAdapter
from retry_engine import (
    PermanentError,
    RetryEngine,
    RetryPolicy,
    RetryableError,
)
from retry_mechanisms import ShippingLabelInput


OUTPUT_DIR = Path("outputs")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_shipping_input(shipment_id: str) -> ShippingLabelInput:
    return ShippingLabelInput(
        shipment_id=shipment_id,
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )


class WorkflowMonitor:
    """Collect workflow results, traces, and operational measurements."""

    def __init__(self) -> None:
        self.run_records: list[dict[str, Any]] = []
        self.traces: list[dict[str, Any]] = []

    def execute_scenario(
        self,
        scenario_name: str,
        shipment_id: str,
        scripted_results: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one scenario and collect its observability data."""
        shipping_input = make_shipping_input(shipment_id)

        adapter = FakeShippingProviderAdapter(
            scripted_results=scripted_results or []
        )

        engine = RetryEngine(
            policy=RetryPolicy(
                max_attempts=3,
                initial_backoff_seconds=0,
                backoff_multiplier=2,
                max_backoff_seconds=0,
            ),
            sleep_fn=lambda seconds: None,
        )

        started_at = utc_timestamp()
        start_clock = time.perf_counter()

        result = engine.execute(shipping_input, adapter)

        latency_ms = round(
            (time.perf_counter() - start_clock) * 1000,
            3,
        )
        completed_at = utc_timestamp()

        record = {
            "scenario": scenario_name,
            "run_id": result.run_id,
            "shipment_id": shipment_id,
            "status": result.status,
            "attempts": result.attempts,
            "provider_calls": adapter.call_count,
            "idempotency_key": result.idempotency_key,
            "label_id": result.label_id,
            "error_type": result.error_type,
            "latency_ms": latency_ms,
            "started_at": started_at,
            "completed_at": completed_at,
            "total_backoff_seconds": result.total_backoff_seconds,
        }

        trace = {
            "trace_id": f"TRACE-{len(self.traces) + 1:03d}",
            "scenario": scenario_name,
            "run_id": result.run_id,
            "shipment_id": shipment_id,
            "events": adapter.get_trace(),
            "workflow_result": {
                "status": result.status,
                "attempts": result.attempts,
                "error_type": result.error_type,
                "label_id": result.label_id,
            },
        }

        self.run_records.append(record)
        self.traces.append(trace)

        return record

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Look up one trace by its trace ID."""
        for trace in self.traces:
            if trace["trace_id"] == trace_id:
                return trace
        return None

    def get_status(self, run_id: str) -> dict[str, Any] | None:
        """Look up the latest recorded status for a run ID."""
        for record in reversed(self.run_records):
            if record["run_id"] == run_id:
                return {
                    "run_id": run_id,
                    "scenario": record["scenario"],
                    "status": record["status"],
                    "attempts": record["attempts"],
                    "error_type": record["error_type"],
                }
        return None

    def build_metrics(self) -> dict[str, Any]:
        """Calculate aggregate workflow measurements."""
        total_runs = len(self.run_records)
        completed_runs = sum(
            record["status"] == "completed"
            for record in self.run_records
        )
        failed_runs = total_runs - completed_runs

        total_attempts = sum(
            record["attempts"] for record in self.run_records
        )
        latencies = [
            record["latency_ms"] for record in self.run_records
        ]

        state_counts = Counter(
            record["status"] for record in self.run_records
        )

        average_latency = (
            round(sum(latencies) / len(latencies), 3)
            if latencies else 0
        )

        return {
            "generated_at": utc_timestamp(),
            "total_runs": total_runs,
            "completed_runs": completed_runs,
            "failed_runs": failed_runs,
            "success_rate_percent": round(
                completed_runs / total_runs * 100, 2
            ) if total_runs else 0,
            "failure_rate_percent": round(
                failed_runs / total_runs * 100, 2
            ) if total_runs else 0,
            "total_attempts": total_attempts,
            "average_attempts_per_run": round(
                total_attempts / total_runs, 2
            ) if total_runs else 0,
            "average_latency_ms": average_latency,
            "maximum_latency_ms": max(latencies, default=0),
            "state_counts": dict(state_counts),
            "trace_count": len(self.traces),
            "measurement_note": (
                "Latency is measured locally and is not a production SLA. "
                "State counts represent final workflow states."
            ),
        }

    def export(self, output_dir: Path = OUTPUT_DIR) -> dict[str, str]:
        """Write traces, run records, and metrics as JSON files."""
        output_dir.mkdir(parents=True, exist_ok=True)

        metrics = self.build_metrics()

        files = {
            "traces": output_dir / "task4_traces.json",
            "run_records": output_dir / "task4_run_records.json",
            "metrics": output_dir / "task4_metrics.json",
        }

        payloads = {
            "traces": self.traces,
            "run_records": self.run_records,
            "metrics": metrics,
        }

        for name, path in files.items():
            path.write_text(
                json.dumps(payloads[name], indent=2),
                encoding="utf-8",
            )

        return {name: str(path) for name, path in files.items()}


def run_observability_demo() -> dict[str, Any]:
    """Run success, retry, permanent failure, and replay scenarios."""
    monitor = WorkflowMonitor()

    monitor.execute_scenario(
        "success",
        "SHIP-4101",
    )

    monitor.execute_scenario(
        "retry_then_success",
        "SHIP-4102",
        [
            RetryableError("Temporary provider outage"),
            {"label_id": "LABEL-SHIP-4102"},
        ],
    )

    monitor.execute_scenario(
        "permanent_failure",
        "SHIP-4103",
        [
            PermanentError("Shipment service is not supported"),
        ],
    )

    replay_record = monitor.execute_scenario(
        "replay_safe_success",
        "SHIP-4104",
    )

    # Verify a repeated request returns the already-created label.
    # This is a separate deterministic replay check.
    replay_adapter = FakeShippingProviderAdapter()
    replay_input = make_shipping_input("SHIP-4105")
    replay_key = "shipping-label:SHIP-4105"

    first_label = replay_adapter(replay_input, replay_key)
    replayed_label = replay_adapter(replay_input, replay_key)

    replay_check = {
        "shipment_id": "SHIP-4105",
        "first_label_id": first_label,
        "replayed_label_id": replayed_label,
        "same_label_returned": first_label == replayed_label,
        "unique_labels_created": len(replay_adapter.created_labels),
        "provider_calls": replay_adapter.call_count,
    }

    files = monitor.export()

    return {
        "scenario_count": len(monitor.run_records),
        "run_records": monitor.run_records,
        "metrics": monitor.build_metrics(),
        "replay_check": replay_check,
        "replay_scenario_run_id": replay_record["run_id"],
        "trace_lookup_example": monitor.get_trace("TRACE-001"),
        "status_lookup_example": monitor.get_status(
            monitor.run_records[0]["run_id"]
        ),
        "exported_files": files,
    }


if __name__ == "__main__":
    print(json.dumps(run_observability_demo(), indent=2))
