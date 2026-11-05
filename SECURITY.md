# Security and privacy

This tool executes application tests and, when explicitly enabled, model-generated experiments.
Use it on projects you trust. It is not a security scanner and does not certify an application
as safe, leak-free, or production-ready.

## Execution boundaries

- Production observation does not execute generated code or force garbage collection.
- Generated experiments require the Docker worker. There is no local override for generated
  probes. `--trusted-local` executes your tests with your OS user's permissions.
- Containers have no network, a read-only source mount, a non-root user and resource limits.
  Docker is not a complete boundary against hostile code or kernel vulnerabilities.
- Python application code runs in the same interpreter as the measurement hooks. Measurements
  are not tamper-proof. Generated probes cannot produce the strongest verified-bug verdict.
- Never expose the Docker daemon or mount its socket into an application/experiment container.

## Data handling

Source snapshots, file paths, dependency fingerprints and measurements remain in the selected
local artifact directory. Model requests go only to the configured loopback Ollama endpoint.
No analytics or cloud model calls are enabled. Ordinary package/model downloads still require
network access during setup.

`.env`, Git metadata and known environment directories are excluded from snapshots. However,
Python files and project configuration can contain hardcoded credentials or private information.
There is **no comprehensive secret detector**. Review your source and artifacts before sharing
them. Raw application stdout is discarded; exception details are deliberately limited.

Do not open or replay bundles from unknown parties. Checksums detect accidental changes; they
are not digital signatures. Keep artifacts in directories writable only by trusted users.

## Reporting vulnerabilities

Do not include credentials, private source or exploitable payloads in public issues. Once the
public repository exists, use its private vulnerability-reporting channel. Publication must
configure that channel; no external reporting address is invented in this local project.
