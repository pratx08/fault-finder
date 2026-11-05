"""Investigation orchestration and immutable reproduction bundles."""

import hashlib
import json
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from faultfinder.agents import AgentClient
from faultfinder.discovery import inspect_project
from faultfinder.execution import DEFAULT_IMAGE, docker_image_id, run_trial
from faultfinder.io import atomic_text, read_text
from faultfinder.models import Assessment, Evidence, Plan, Trial, Verdict
from faultfinder.reporting import write_report
from faultfinder.verification import assess

ARTIFACT_QUOTA = 512 * 1024 * 1024


def bundle_source(project: dict, destination: Path) -> None:
    source = Path(project["root"])
    total = 0
    for relative, expected in project["files"].items():
        path = source / relative
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("Source escaped project root")
        data = path.read_bytes()
        total += len(data)
        if total > 64 * 1024 * 1024:
            raise ValueError("Source bundle exceeds 64 MiB")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Source changed during snapshot; retry investigation")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def validate_bundle(bundle: Path) -> Evidence:
    evidence = Evidence.model_validate_json(read_text(bundle / "evidence.json"))
    if (bundle / "source").is_symlink():
        raise ValueError("Bundle source must not be a symlink")
    source = (bundle / "source").resolve()
    if inspect_project(source)["files"] != evidence.project["files"]:
        raise ValueError("Bundle source hash mismatch or unexpected files")
    for relative, digest in evidence.project["files"].items():
        path = (source / relative).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError("Bundle source is missing or escapes its root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Bundle source hash mismatch")
    manifest = json.loads(read_text(bundle / "manifest.json"))
    plan_hash = hashlib.sha256(evidence.plan.model_dump_json().encode()).hexdigest()
    if manifest["plan_sha256"] != plan_hash:
        raise ValueError("Bundle plan hash mismatch")
    if evidence.plan.harness:
        if read_text(bundle / "harness.py") != evidence.plan.harness:
            raise ValueError("Bundle harness hash mismatch")
    return evidence


def investigate(
    root: Path,
    plan: Plan,
    output_root: Path,
    backend: str = "docker",
    image: str = DEFAULT_IMAGE,
    agent: AgentClient | None = None,
    observation: dict | None = None,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in output_root.rglob("*") if p.is_file())
    if used > ARTIFACT_QUOTA:
        raise ValueError("Artifact quota reached; export or prune older bundles")
    project = inspect_project(root)
    source_bytes = sum((root / name).stat().st_size for name in project["files"])
    if used + source_bytes > ARTIFACT_QUOTA:
        raise ValueError("Source snapshot would exceed the artifact quota")
    if observation:
        project["observation"] = observation
    job_id = uuid.uuid4().hex[:12]
    output = output_root / job_id
    source = output / "source"
    source.mkdir(parents=True)
    bundle_source(project, source)
    trials: list[Trial] = []
    limitations = [
        "Single process; Python object retention only",
        "Source snapshots exclude .env files but may contain secrets hardcoded in code",
        "Missing data fixtures or services make an investigation inconclusive",
    ]
    if backend == "local":
        limitations.append("Trusted local mode is not a security sandbox or Linux qualification")
    image_id = ""
    try:
        if backend == "docker":
            image_id = docker_image_id(image)
        if agent:
            agent.validate_model()
            generated = agent.plan(project, source)
            generated.policy = plan.policy
            plan = generated
        start = time.monotonic()
        variants = ["subject"] if plan.kind == "pytest" else ["subject", "control"]
        for variant in variants:
            for size in plan.policy.sizes:
                for repeat in range(plan.policy.repeats):
                    remaining = plan.policy.timeout_seconds - (time.monotonic() - start)
                    if remaining <= 0:
                        raise TimeoutError("Investigation time budget exhausted")
                    trial = run_trial(
                        plan, source, variant, size, repeat, backend, image_id, remaining
                    )
                    trials.append(trial)
                    # Avoid repeating a broken setup or invalid generated harness 24 times.
                    if not trial.ok:
                        raise RuntimeError(trial.error)
        assessment = assess(plan, trials)
    except Exception as exc:
        assessment = Assessment(
            verdict=Verdict.INCONCLUSIVE, reason=f"Investigation stopped: {type(exc).__name__}"
        )
        safe = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else ""
        if safe:
            limitations.append(safe[:500])
    project["image_id"] = image_id
    evidence = Evidence(
        id=job_id,
        created_at=datetime.now(UTC).isoformat(),
        project=project,
        plan=plan,
        trials=trials,
        assessment=assessment,
        backend=backend,
        limitations=limitations,
    )
    if agent and trials:
        try:
            evidence.agent_review = agent.review(
                {
                    "assessment": assessment.model_dump(mode="json"),
                    "trials": [t.model_dump() for t in trials],
                    "hypothesis": plan.hypothesis,
                }
            )
        except Exception:
            evidence.limitations.append("Agent review unavailable; measured verdict is unchanged")
    atomic_text(
        output / "manifest.json",
        json.dumps(
            {
                "plan_sha256": hashlib.sha256(plan.model_dump_json().encode()).hexdigest(),
                "source_sha256": project["source_sha256"],
            },
            indent=2,
        ),
    )
    if plan.harness:
        atomic_text(output / "harness.py", plan.harness)
    write_report(evidence, output)
    return output


def replay(bundle: Path, output_root: Path, trusted_local: bool) -> Path:
    original = validate_bundle(bundle)
    if original.backend == "local" and not trusted_local:
        raise ValueError("Replaying a local workload requires --trusted-local")
    result = investigate(
        bundle / "source",
        original.plan,
        output_root,
        original.backend,
        original.project.get("image_id") or DEFAULT_IMAGE,
    )
    replayed = validate_bundle(result)
    old_runtimes = {tuple(sorted(t.runtime.items())) for t in original.trials if t.ok}
    new_runtimes = {tuple(sorted(t.runtime.items())) for t in replayed.trials if t.ok}
    replayed.project["replay_of"] = original.id
    if old_runtimes and new_runtimes and old_runtimes != new_runtimes:
        replayed.assessment = Assessment(
            verdict=Verdict.INCONCLUSIVE,
            reason="Replay environment differs from the recorded runtime or dependencies",
        )
    write_report(replayed, result)
    return result


def prune(output_root: Path, days: int) -> int:
    cutoff = time.time() - days * 86400
    removed = 0
    if not output_root.is_dir():
        return removed
    for path in output_root.iterdir():
        if path.is_symlink() or not path.is_dir():
            continue
        evidence = path / "evidence.json"
        if evidence.is_file() and evidence.stat().st_mtime < cutoff:
            # Only remove completed, valid bundles within the requested artifact directory.
            validate_bundle(path)
            if not path.resolve().is_relative_to(output_root.resolve()):
                raise ValueError("Artifact path escaped output directory")
            shutil.rmtree(path)
            removed += 1
    return removed
