# Release procedure

The distribution name is `fault-finder`; the import package and CLI are `faultfinder`.
PyPI returned no project for the normalized name at the last check, but availability is not
reserved until the first upload. Confirm it again immediately before publishing.

## Required gates

1. Confirm `fault-finder` is still available on PyPI and TestPyPI.
2. Commit and push the reviewed source to the owner's repository. Configure private security
   reporting and a protected `pypi` GitHub environment.
3. Pass the Windows/Linux Python matrix, actual Docker test and clean wheel installation.
   Check the runtime dependency audit and resolve findings. Record live-model and performance
   limitations explicitly for an alpha release.
4. Build both source and wheel artifacts, run strict metadata/content checks, and record hashes.
5. Verify the README, supported versions and limitations against actual evidence. Do not use a
   stable/production classifier merely because packaging succeeds.
6. Register the repository and `release.yml` as a Trusted Publisher in the owner's PyPI account.
   Never put API tokens into the source tree. Configure TestPyPI separately for a rehearsal.
7. Publish a GitHub release only when all required evidence is reviewed. The workflow rebuilds
   from the release commit, validates the name/version, and uploads via OIDC.

## Local validation commands

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src/faultfinder
uv run pytest
uv build --out-dir .faultfinder/release-dist
uv run twine check --strict .faultfinder/release-dist/*
uv run python tools/release_check.py --dist .faultfinder/release-dist
```

Use a new empty artifact directory for each build. Never upload a wildcard from a directory
containing older builds. The checksum manifest identifies the exact wheel and source archive.
Install the wheel into a separate environment with no editable checkout and run
`tools/installed_smoke.py` with that environment's Python.

PyPI/TestPyPI publication is intentionally not performed by the local build scripts. A built
wheel is not evidence that the package name is reserved or the project is production-qualified.
