# Fault Finder

Fault Finder investigates Python memory retention and produces replayable HTML/JSON evidence.
The Phase 1 implementation separates production observation, isolated investigation, and
deterministic verification. It does not claim to detect every leak or support every language.

## Install

Use Python 3.11 or 3.12:

```sh
python -m pip install fault-finder
faultfinder inspect path/to/project
```

## Development

Requires CPython 3.11 or 3.12 and uv. From this directory:

```sh
uv sync --python 3.12
uv run pytest
uv run ruff check .
uv run mypy src/faultfinder
```

## Try it locally

```sh
uv run faultfinder demo --case listeners --trusted-local
uv run faultfinder demo --case bounded_cache --trusted-local
uv run faultfinder inspect path/to/project
uv run faultfinder check path/to/project --trusted-local --tests tests
```

Each investigation creates `.faultfinder/reports/<id>/report.html`, `evidence.json`, a source
snapshot and hash manifest. The demo is a **seeded corpus**, not a discovery in your application.
Local mode is for your own trusted tests, inherits the controller's installed packages, and
is not a security sandbox. Generated code cannot run in local mode.

Exit codes: `0` no evidence in the tested workload; `1` finding; `2` inconclusive/configuration.
Skipped/xfail/failed tests invalidate a trial. Test workloads repeat whole suites after one
warm-up suite; counts are **suite iterations**, not HTTP requests. Initially use a small test
selection that can be repeated safely. No application payloads or stdout are saved.

## Isolated execution and agents

```sh
docker build -t faultfinder-worker:local .
uv run faultfinder check path/to/project --image faultfinder-worker:local
uv run faultfinder check path/to/project --model YOUR_INSTALLED_OLLAMA_MODEL
```

The base worker includes FaultFinder and pytest, not arbitrary application dependencies. Build a
derived image with the project's pinned dependencies installed **before** investigation.
Runtime containers have no network. Missing packages or fixtures give an inconclusive result.
Use your project's locked install method; do not resolve new dependency versions during trials.

Agent mode uses a local Ollama server on `127.0.0.1:11434`. Investigator and planner inspect
bounded source snippets, then produce a harness. A trusted worker measures weak references,
allocations and path execution. A reviewer explains the measured result. Model-generated
experiments can establish reproduced retention; an unreviewed model claim cannot establish
the application's intended lifecycle. No model is silently downloaded or paid API invoked.

### Use a hosted model API

Use a provider that implements bearer-authenticated Chat Completions. This runs in one terminal;
no Ollama server is needed. Set `LLM_API_KEY` in your environment, or let the command prompt for
it without displaying it:

```sh
faultfinder check path/to/project --provider api --api-url https://PROVIDER/v1/chat/completions --model YOUR_MODEL
```

For example, OpenAI uses `https://api.openai.com/v1/chat/completions`; Groq uses
`https://api.groq.com/openai/v1/chat/completions`. Choose a model available in your own account.
Use `--api-key-env OPENAI_API_KEY` (or another variable name) if you already store your key under
that name. The endpoint must use HTTPS, except for loopback servers. The provider must return one
JSON object in `choices[0].message.content`; malformed or incomplete answers are inconclusive.
Only bounded source snippets and measurements are sent to the selected provider. Review code for
hardcoded secrets first. Your provider may charge for these requests. Generated probes still run
in Docker, so a running Docker engine and a worker image with project dependencies are required.

## Production observation

Wrap the ASGI application once:

```python
from faultfinder.observer import FaultFinderASGI

app = FaultFinderASGI(app)
```

Run these in separate terminals/services:

```sh
uv run faultfinder watch path/to/project -- uvicorn app:app --workers 1
uv run faultfinder worker path/to/project --model YOUR_INSTALLED_OLLAMA_MODEL
```

The watcher records process memory and optional lifecycle counters. It queues sustained
growth after warm-up. The worker deduplicates and investigates candidates in Docker. Source
changes between observation and investigation invalidate the candidate. Without a model,
the worker uses existing tests and produces weaker growth evidence. Idle applications provide
no useful lifecycle evidence. Multiple processes are flagged as incomplete coverage.

## Replay and retention

```sh
uv run faultfinder replay .faultfinder/reports/REPORT_ID --trusted-local
uv run faultfinder report .faultfinder/reports/REPORT_ID
uv run faultfinder prune --days 7
```

Replay validates source and plan hashes. Dependency fingerprints are recorded; Docker image
IDs pin isolated replay. Local replay can differ if your environment changed. Keep the JSON
and source bundle alongside the HTML. Reports require no external website or CDN.

## Project structure

`src/faultfinder/` contains discovery, observation, agent planning, execution, measurement,
verification, persistence, reporting and CLI modules. `tests/` exercises their contracts and
end-to-end behavior. `examples/` contains small applications for integration checks. `docs/`
holds the design and measured qualification results. Generated artifacts stay in `.faultfinder/`.

See `docs/validation.md` for what was actually tested and remaining production qualification.
A reproduced memory increase is not automatically a verified leak or production savings.
