# Implement Retry Mechanisms

## Project overview
This project implements a bounded, observable retry workflow for a simulated shipping-label service. It demonstrates input validation, workflow state transitions, transient/permanent error classification, exponential backoff, idempotency, recovery decisions, structured traces, and operator evidence.

**Assessment:** Workflow Orchestration — Topic 10  
**Runtime:** Python 3.13+ | **Validation:** Pydantic 2.x | **Tests:** pytest

## Features
- Validated shipping-label input and explicit workflow states.
- Bounded retry policy with capped exponential backoff.
- Transient/permanent error classification.
- Stable idempotency keys across attempts and duplicate delivery.
- Injectable fake shipping-provider adapter.
- Timeout and malformed-response recovery decisions.
- Structured JSON traces, run records, status lookup, and metrics.
- Operator runbook and machine-readable evidence report.

## Project structure
```text
Implement_Retry_Mechanisms/
├── retry_mechanisms.py
├── retry_engine.py
├── retry_reliability.py
├── retry_adapters.py
├── retry_observability.py
├── evidence_report.py
├── tests/
├── docs/
├── outputs/
└── reports/
```

## Setup
```cmd
cd /d E:\Tayana_Projects\Workflow_Orchestration\Implement_Retry_Mechanisms
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run demonstrations
```cmd
python retry_mechanisms.py
python retry_engine.py
python retry_reliability.py
python retry_adapters.py
python retry_observability.py
python evidence_report.py
python -m pytest -v
```

Latest verified full test result during development: **45 passed**.

## Evidence and measurements
Task 4 outputs:
- `outputs/task4_traces.json` — structured provider events.
- `outputs/task4_run_records.json` — per-run status, attempts, identifiers, and latency.
- `outputs/task4_metrics.json` — aggregate run counts, rates, attempts, and local latency.

Task 5 outputs:
- `outputs/task5_evidence.json` — success, retry exhaustion, replay, and recovery evidence.
- `reports/task5_evidence_report.md` — operator measurement and recovery summary.

The Task 4 observability demonstration recorded four runs: three completed and one needing review (75% success rate), five total attempts, and four traces. The Task 5 evidence run demonstrated success, retry exhaustion after three attempts, replay returning the same label with one unique purchase, and recovery instructions for timeout and malformed-response cases. Latency values are local demonstration measurements, not production SLAs.

## Operator recovery guidance
- **completed:** verify the label ID and continue fulfilment.
- **retrying:** allow bounded retries; avoid duplicate submissions.
- **needs_review:** stop automatic processing and investigate.
- **timeout:** query provider using the original idempotency key before replay.
- **malformed response:** verify whether the provider created a label before replay.
- **duplicate delivery:** reuse the original label rather than purchasing again.

See `docs/operator_runbook.md` for detailed procedures.

## Known limitations
The provider and idempotency gateway are in-memory fakes; records do not survive process restarts. The timeout helper limits caller wait time but cannot forcibly terminate a Python worker thread. Provider lookup and recovery decisions are simulated and do not contact a real external provider. Latency measurements vary by machine and are not production SLAs. Metrics record final workflow states rather than time spent in each state. A production implementation would require durable idempotency storage, provider-side guarantees, persistent event storage, distributed concurrency controls, authentication, and production monitoring.

## Engineering reflection
Building this retry workflow helped me understand that retrying an operation is not simply repeating a failed function call. The system first needs to decide whether an error is transient or permanent, enforce a strict attempt limit, and apply backoff so repeated requests do not overwhelm an unavailable dependency. I implemented a stable idempotency key and tested duplicate delivery to demonstrate why safe retries matter when an external service may have completed an operation even though the caller did not receive a valid response.

The timeout and malformed-response exercises were especially useful. A timeout does not prove that a shipping label was not created, and a malformed response does not prove that the provider rejected the request. The safer recovery path is to query the provider with the original idempotency key, reconcile any existing result, and only then consider replay. This shifted my thinking from treating errors as isolated exceptions to treating them as workflow states that require evidence and deliberate operator action.

I also learned the value of an injectable fake adapter. It allowed me to reproduce success, transient failure, permanent failure, malformed responses, and duplicate requests without depending on a real shipping service. Structured traces and metrics made those scenarios easier to inspect and test. The automated test suite gave me confidence that retry limits, state transitions, replay behavior, and reporting remained consistent as the implementation grew.

The main limitation is that the prototype uses in-memory records and simulated provider behavior. A production version would need durable idempotency storage, provider-side guarantees, persistent observability, and stronger concurrency controls. Overall, this activity improved my understanding of reliable workflow design, failure recovery, and the importance of making automated operations observable and safe for human operators.
