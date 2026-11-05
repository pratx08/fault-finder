# Phase 1 implementation and qualification

This document distinguishes implemented behavior from release qualification. The implementation
is available locally; full production qualification requires the remaining checks below.

## Implemented

- Typed CLI package with a `src/` layout, locked dependencies, wheel build and CI matrix.
- Import-free discovery and bounded source hypotheses.
- ASGI lifecycle counters and bounded process-growth detection.
- Persistent candidate queue, deduplication, worker lease renewal and crash recovery.
- Fresh-process experiments, warm-up, repeated workloads and negative controls.
- Weak-reference lifetime checks, owner-type diagnostics and traced allocation comparisons.
- Existing pytest workload repetition, including asyncio tests.
- Docker execution restrictions; generated harnesses forbidden in local mode.
- Local-model investigator, planner and reviewer using schema-validated responses.
- Deterministic verdicts; generated experiments cannot self-certify a lifecycle contract.
- Offline HTML/JSON reports, source snapshots, checked replay and artifact retention commands.

## Local evidence

Windows 11, CPython 3.12.9 and 3.11.9 are the available validation environments.
Exact test results are recorded in `.runproof/tests-python312.xml` and
`.runproof/tests-python311.xml` after their respective runs.

Results recorded on 26 October 2026, with additional release checks on 5 November 2026:

| Check | Result |
|---|---|
| CPython 3.12.9 | 61 passed; Docker test excluded after the rename on 5 November 2026 |
| CPython 3.11.9 | 48 passed; Docker test excluded |
| Docker integration selection | 1 passed with the renamed `faultfinder-worker:local` image on 5 November 2026 |
| Ruff lint and formatting | Passed |
| mypy | Passed for all 16 core modules |
| Distribution | Wheel and source archive built; strict Twine metadata and artifact content checks passed |
| Clean wheel installation | CLI, subprocess worker, pytest, evidence and HTML smoke test passed |
| Runtime dependency audit | No known vulnerabilities found on 5 November 2026 |

Tests cover successful/failed/skipped and async workloads, cancellation, retention controls,
source integrity, replay, process time/output limits, queue recovery, report escaping and
model-response validation. The newer checks do not establish production reliability across
arbitrary projects.

The listener corpus retains one controller-equivalent payload per operation in the subject and
zero in the control. Releasing the owner removes those references. The bounded-cache case
plateaus and produces no finding. An ordinary example application that stores 64 KiB results
in a global list produces suspected growth through its existing tests, without a custom probe.

The local agent protocol test uses **mock model responses** and executes only a reviewed,
test-owned fixture. It validates planning, target-path coverage and measurements, but does not
establish the quality of any real LLM's generated experiments.

`tools/validate_beeai.py` evaluates the actual BeeAI baseline and fixed Git revisions using a
reviewed known-bug harness and fresh processes. It does not alter the checkout and is not an
autonomous-discovery benchmark. Its results are stored in `.runproof/beeai-validation.json`.

| BeeAI revision | Trials | Retained controllers after 1,000 operations |
|---|---:|---:|
| Baseline `d6896400a47b7586be80dccea5d5f4ef805f1ddb` | 18 | 1,000 in every subject repetition |
| Fixed `18623b928cc73689822e53a37756e18805be90c2` | 18 | 0 in every subject repetition |

Both revisions retained zero in the no-shared-signal controls. Releasing the shared owner
also released the retained controllers. The automated verdict is conservatively
`reproduced_retention` for the baseline and `no_evidence` for the fixed revision.

The following reports were generated locally during validation. They are not tracked in Git:

- Listener corpus: `../.runproof/reports/2f8dd11559e7/report.html`.
- Bounded cache: `../.runproof/reports/a3c6bb56e6da/report.html`.
- Existing-test screening: `../examples/retention_app/.runproof/reports/ef2fe3338799/report.html`.

`tools/benchmark_observer.py` measures a synthetic ASGI workload over five paired runs. The
raw results live in `.runproof/observer-benchmark.json`. Scheduler variation on Windows is
substantial; negative measured overhead is noise, not a speedup claim. This experiment alone
does not qualify production overhead or Linux behavior.

## Remaining production qualification

The configurable Chat Completions adapter has protocol and CLI tests with mocked responses.
No live third-party API was called during this release, so provider-specific compatibility and
model-generated reproduction quality remain unverified.

1. Run the installed local Ollama model against previously unseen projects and record its
   actual completion and failure rates. Mock protocol tests are not a substitute for this check.
2. Evaluate a second independent real project and a held-out corpus. Publish actual false-positive
   and missed-defect counts. The examples and seeded cases are not independent-project evidence.
3. Measure observation overhead under realistic Linux application workloads and network traffic;
   verify the TDD's throughput and p95 budgets on those workloads.

## Deliberate limitations

- Only the packaged corpus has pre-established machine-checkable lifecycle contracts. Findings
  from generated probes are labeled reproduced retention until lifecycle intent is independently
  established. Source patterns or model assertions never provide that proof by themselves.
- Pytest repetition is a growth-screening fallback. Fixture/test-runner retention may explain a
  result, so it does not produce a verified bug verdict.
- Source snapshots include Python and supported project/dependency configuration files. Arbitrary
  data fixtures, external services, credentials and writable source trees are not reproduced
  automatically. Missing requirements produce an inconclusive result.
- One process is covered. Child processes, native allocations and GPU memory are not attributed.
- Container isolation assumes a trusted target project. In-process measurement is not tamper-proof
  against a deliberately hostile application or generated harness.
- One candidate and one generated harness are investigated per invocation. A failed harness is
  reported as inconclusive; automatic harness repair is deferred rather than silently retried.
