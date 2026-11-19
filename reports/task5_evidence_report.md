# Task 5 — Operator Evidence Report

## 1. Execution summary

| Scenario | Outcome | Attempts / details |
|---|---|---:|
| Success | completed | 1 attempt(s) |
| Retry exhaustion | needs_review | 3 attempts |
| Duplicate replay | Same label returned | 1 unique purchase |
| Timeout recovery | query_provider_by_idempotency_key | Provider lookup required |
| Malformed response | verify_provider_record_before_replay | Provider verification required |

## 2. Observability measurements

| Metric | Value |
|---|---:|
| Observed workflow runs | 3 |
| Completed runs | 2 |
| Runs needing review | 1 |
| Success rate | 66.67% |
| Failure rate | 33.33% |
| Total attempts | 4 |
| Average attempts per run | 1.33 |
| Average measured latency | 0.25 ms |
| Maximum measured latency | 0.383 ms |
| Structured traces | 3 |

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
