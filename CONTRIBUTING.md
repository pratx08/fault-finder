# Contributing

Use Python 3.11 or 3.12. Install the locked development environment with `uv sync --frozen`.

Before proposing a change, run:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src/faultfinder
uv run pytest -m "not docker"
```

Container changes also require building `faultfinder-worker:local` and running
`uv run pytest -m docker`. Tests that need Docker must not silently pass when the daemon or
image is broken; an absent executable is reported as a skip in developer environments.

Keep measurement and verdict logic independent from model output. Add a healthy control when
adding a detector and preserve incomplete/inconclusive outcomes. Do not weaken lifecycle
requirements merely to turn a model-generated hypothesis into a verified finding.

Add integrations through adapters and typed schemas. Keep application objects and raw payloads
out of telemetry. Do not add provider credentials to test fixtures or reports. Generated harnesses
must execute in the restricted worker, never through a host `exec` call.

Document tested support separately from proposed support. Report actual workloads, repetitions,
versions, and hardware for performance claims. A small seeded corpus is not an accuracy benchmark
for all Python projects.
