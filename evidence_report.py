"""Generate Task 5 workflow evidence and operator measurement report."""

import json
import time
from pathlib import Path

from retry_adapters import FakeShippingProviderAdapter
from retry_engine import (
    PermanentError,
    RetryEngine,
    RetryPolicy,
    RetryResult,
    RetryableError,
)
from retry_mechanisms import ShippingLabelInput
from retry_observability import WorkflowMonitor
from retry_reliability import (
    IdempotentShippingGateway,
    execute_with_timeout,
    recovery_decision,
)


OUTPUT_DIR = Path("outputs")
REPORT_DIR = Path("reports")


def make_input(shipment_id):
    return ShippingLabelInput(
        shipment_id=shipment_id,
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )


def run_success_scenario():
    adapter = FakeShippingProviderAdapter()
    engine = RetryEngine(
        RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
        sleep_fn=lambda seconds: None,
    )
    result = engine.execute(make_input("SHIP-6001"), adapter)

    return {
        "scenario": "success",
        "result": result.to_dict(),
        "operator_action": (
            "No action required. Confirm the label ID and continue "
            "downstream fulfilment."
        ),
    }


def run_failure_scenario():
    adapter = FakeShippingProviderAdapter(
        scripted_results=[
            RetryableError("Temporary provider outage"),
            RetryableError("Provider still unavailable"),
            RetryableError("Provider unavailable after final attempt"),
        ]
    )
    engine = RetryEngine(
        RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
        ),
        sleep_fn=lambda seconds: None,
    )
    result = engine.execute(make_input("SHIP-6002"), adapter)

    return {
        "scenario": "retry_exhaustion",
        "result": result.to_dict(),
        "provider_calls": adapter.call_count,
        "operator_action": (
            "Stop automatic retries. Inspect provider availability and "
            "logs, verify the idempotency key, and investigate before "
            "authorizing another attempt."
        ),
    }


def run_replay_scenario():
    gateway = IdempotentShippingGateway()
    shipping_input = make_input("SHIP-6003")
    idempotency_key = "shipping-label:SHIP-6003"

    def provider(data):
        return {"label_id": "LABEL-SHIP-6003"}

    first_label = gateway.create_label(
        shipping_input,
        idempotency_key,
        provider,
    )
    replayed_label = gateway.create_label(
        shipping_input,
        idempotency_key,
        provider,
    )

    return {
        "scenario": "duplicate_delivery_replay",
        "first_label_id": first_label,
        "replayed_label_id": replayed_label,
        "same_label_returned": first_label == replayed_label,
        "gateway_summary": gateway.summary(),
        "operator_action": (
            "Do not purchase another label. Reuse the original label "
            "and verify the shipment record."
        ),
    }


def run_recovery_scenarios():
    timeout_outcome = execute_with_timeout(
        lambda: time.sleep(0.15),
        timeout_seconds=0.01,
    )

    timeout_result = RetryResult(
        run_id="RUN-TIMEOUT-6004",
        status="needs_review",
        attempts=1,
        idempotency_key="shipping-label:SHIP-6004",
        error_type="TimeoutError",
        message=timeout_outcome.error or "Operation timed out",
    )
    timeout_decision = recovery_decision(timeout_result)

    malformed_result = RetryResult(
        run_id="RUN-MALFORMED-6005",
        status="needs_review",
        attempts=1,
        idempotency_key="shipping-label:SHIP-6005",
        error_type="MalformedResponseError",
        message="Provider response did not contain a valid label ID",
    )
    malformed_decision = recovery_decision(malformed_result)

    return {
        "timeout": {
            "completed": timeout_outcome.completed,
            "error": timeout_outcome.error,
            "elapsed_seconds": round(
                timeout_outcome.elapsed_seconds, 4
            ),
            "recovery": {
                "status": timeout_decision.status,
                "next_action": timeout_decision.next_action,
                "reason": timeout_decision.reason,
                "safe_to_replay": timeout_decision.safe_to_replay,
            },
            "operator_action": (
                "Query the provider using the original idempotency key. "
                "If a label exists, reconcile it. Replay only after "
                "confirming the provider's idempotency behavior."
            ),
        },
        "malformed_response": {
            "status": malformed_decision.status,
            "next_action": malformed_decision.next_action,
            "reason": malformed_decision.reason,
            "safe_to_replay": malformed_decision.safe_to_replay,
            "operator_action": (
                "Verify whether the provider created a label despite "
                "the malformed response. Reconcile before replay."
            ),
        },
    }


def build_markdown_report(evidence):
    success = evidence["success"]["result"]
    failure = evidence["failure"]["result"]
    replay = evidence["replay"]
    metrics = evidence["observability_metrics"]
    recovery = evidence["recovery"]

    return f"""# Task 5 — Operator Evidence Report

## 1. Execution summary

| Scenario | Outcome | Attempts / details |
|---|---|---:|
| Success | {success["status"]} | {success["attempts"]} attempt(s) |
| Retry exhaustion | {failure["status"]} | {failure["attempts"]} attempts |
| Duplicate replay | Same label returned | {replay["gateway_summary"]["unique_labels_purchased"]} unique purchase |
| Timeout recovery | {recovery["timeout"]["recovery"]["next_action"]} | Provider lookup required |
| Malformed response | {recovery["malformed_response"]["next_action"]} | Provider verification required |

## 2. Observability measurements

| Metric | Value |
|---|---:|
| Observed workflow runs | {metrics["total_runs"]} |
| Completed runs | {metrics["completed_runs"]} |
| Runs needing review | {metrics["failed_runs"]} |
| Success rate | {metrics["success_rate_percent"]}% |
| Failure rate | {metrics["failure_rate_percent"]}% |
| Total attempts | {metrics["total_attempts"]} |
| Average attempts per run | {metrics["average_attempts_per_run"]} |
| Average measured latency | {metrics["average_latency_ms"]} ms |
| Maximum measured latency | {metrics["maximum_latency_ms"]} ms |
| Structured traces | {metrics["trace_count"]} |

Measurements are local deterministic demonstration results, not production SLAs.

## 3. Operator actions by workflow state

| State | Operator action |
|---|---|
| created | Validate shipment input and confirm the request is authorized |
| running | Monitor the active attempt; avoid submitting duplicate requests |
| retrying | Allow bounded retries; inspect transient errors if retries continue |
| completed | Verify label ID and continue fulfilment; no replay required |
| needs_review | Stop automatic processing; inspect provider status and reconcile before replay |
| cancelled | Confirm cancellation and ensure no unintended provider side effect occurred |

## 4. Recovery procedures

### Timeout

A timeout only means the caller stopped waiting. The provider operation may still be running. Query the provider with the original idempotency key before considering replay.

### Malformed response

Check the provider record for an existing label. Do not assume the provider did not complete the purchase.

### Retry exhaustion

Stop automatic retries and investigate the dependency. An operator must verify provider status before authorizing recovery.

### Duplicate delivery

Reuse the original idempotency key. The fake gateway should return the existing label rather than create another one.

## 5. Known limitations

- The provider adapter and idempotency gateway are in-memory fakes.
- Idempotency records do not survive process restarts.
- The timeout helper does not forcibly terminate its daemon worker thread.
- Latency measurements are local and may vary between runs.
- The current metrics report final states, not elapsed duration in each individual state.
- The recovery decision identifies the required provider lookup but does not itself query an external provider.
- No real shipping provider, payment service, or production API is contacted.

## 6. Evidence files

- `outputs/task4_traces.json`
- `outputs/task4_run_records.json`
- `outputs/task4_metrics.json`
- `outputs/task5_evidence.json`
- `reports/task5_evidence_report.md`

## 7. Reproduction commands

```cmd
python retry_mechanisms.py
python retry_engine.py
python retry_reliability.py
python retry_adapters.py
python retry_observability.py
python evidence_report.py
python -m pytest -v
```
"""


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    REPORT_DIR.mkdir(exist_ok=True)

    monitor = WorkflowMonitor()

    # Generate an additional set of observable workflow runs.
    monitor.execute_scenario("success", "SHIP-6101")
    monitor.execute_scenario(
        "retry_then_success",
        "SHIP-6102",
        [
            RetryableError("Temporary provider outage"),
            {"label_id": "LABEL-SHIP-6102"},
        ],
    )
    monitor.execute_scenario(
        "permanent_failure",
        "SHIP-6103",
        [PermanentError("Unsupported shipping service")],
    )

    evidence = {
        "project": "Implement Retry Mechanisms",
        "task": 5,
        "success": run_success_scenario(),
        "failure": run_failure_scenario(),
        "replay": run_replay_scenario(),
        "recovery": run_recovery_scenarios(),
        "observability_metrics": monitor.build_metrics(),
        "observability_traces": monitor.traces,
        "operator_actions": {
            "created": "Validate input and authorization",
            "running": "Monitor active attempt",
            "retrying": "Allow bounded retry policy",
            "completed": "Verify label and continue fulfilment",
            "needs_review": "Investigate and reconcile before replay",
            "cancelled": "Verify cancellation and side effects",
        },
    }

    json_path = OUTPUT_DIR / "task5_evidence.json"
    json_path.write_text(
        json.dumps(evidence, indent=2),
        encoding="utf-8",
    )

    report_path = REPORT_DIR / "task5_evidence_report.md"
    report_path.write_text(
        build_markdown_report(evidence),
        encoding="utf-8",
    )

    print("=" * 60)
    print("TASK 5 — EVIDENCE GENERATION")
    print("=" * 60)
    print(json.dumps({
        "success_status": evidence["success"]["result"]["status"],
        "failure_status": evidence["failure"]["result"]["status"],
        "failure_attempts": evidence["failure"]["result"]["attempts"],
        "replay_same_label": evidence["replay"]["same_label_returned"],
        "unique_replay_purchases": evidence["replay"]["gateway_summary"][
            "unique_labels_purchased"
        ],
        "timeout_action": evidence["recovery"]["timeout"]["recovery"][
            "next_action"
        ],
        "malformed_action": evidence["recovery"]["malformed_response"][
            "next_action"
        ],
        "metrics": evidence["observability_metrics"],
        "created_files": [str(json_path), str(report_path)],
    }, indent=2))


if __name__ == "__main__":
    main()
