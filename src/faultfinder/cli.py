"""Public commands; non-success states have nonzero exit codes."""

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Annotated

import psutil
import typer
from rich.console import Console

from faultfinder import __version__
from faultfinder.agents import AgentClient, CompatibleAPIClient
from faultfinder.corpus import CASES
from faultfinder.discovery import inspect_project
from faultfinder.engine import investigate, prune
from faultfinder.engine import replay as replay_bundle
from faultfinder.execution import terminate_tree
from faultfinder.io import read_text
from faultfinder.models import Evidence, Plan, Policy, Verdict
from faultfinder.observer import GrowthDetector, Sample
from faultfinder.reporting import write_report
from faultfinder.store import Store

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="Reproducible Python memory-retention investigations.",
)
console = Console()
Project = Annotated[Path, typer.Argument(exists=True, file_okay=False, resolve_path=True)]


@app.callback(invoke_without_command=True)
def options(version: Annotated[bool, typer.Option("--version", is_eager=True)] = False) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()


def main() -> None:
    """Console boundary: operational failures should not expose raw source/model data."""
    try:
        app()
    except (OSError, ValueError) as exc:
        console.print(
            f"Cannot complete the command ({type(exc).__name__}). "
            "Check input paths, permissions, and configuration.",
            markup=False,
        )
        sys.exit(2)


def finish(bundle: Path) -> None:
    evidence = Evidence.model_validate_json(read_text(bundle / "evidence.json"))
    console.print(f"{evidence.assessment.verdict}: {evidence.assessment.reason}", markup=False)
    console.print(f"Report: {bundle / 'report.html'}", markup=False)
    for limitation in evidence.limitations:
        console.print(f"  {limitation}", markup=False)
    code = {
        Verdict.NO_EVIDENCE: 0,
        Verdict.SUSPECTED: 1,
        Verdict.RETENTION: 1,
        Verdict.VERIFIED: 1,
        Verdict.INCONCLUSIVE: 2,
    }[evidence.assessment.verdict]
    raise typer.Exit(code)


def policy(sizes: str, timeout: int) -> Policy:
    try:
        return Policy(sizes=[int(n) for n in sizes.split(",")], timeout_seconds=timeout)
    except ValueError as exc:
        raise typer.BadParameter(
            "Use 3+ increasing sizes starting at 0 and a positive timeout"
        ) from exc


def model_client(provider: str, model: str, api_url: str, api_key_env: str) -> AgentClient | None:
    if provider == "ollama":
        if api_url:
            raise typer.BadParameter("--api-url requires --provider api")
        return AgentClient(model) if model else None
    if provider == "api":
        if not api_url or not model:
            raise typer.BadParameter("API mode requires --api-url and --model")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise typer.BadParameter("--api-key-env must name an environment variable")
        key = os.environ.get(api_key_env, "")
        if not key and sys.stdin.isatty() and sys.stdout.isatty():
            key = typer.prompt("Model API key", hide_input=True)
        if not key:
            raise typer.BadParameter(f"Set {api_key_env} or run interactively to enter the key")
        return CompatibleAPIClient(model, api_url, api_key=key)
    raise typer.BadParameter("Provider must be ollama or api")


@app.command("inspect")
def inspect_command(project: Project = Path(".")) -> None:
    """Discover source, tests and static hypotheses without importing application code."""
    console.print_json(json.dumps(inspect_project(project)))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def check(
    ctx: typer.Context,
    project: Project = Path("."),
    tests: str = "tests",
    sizes: str = "0,5,10",
    timeout: int = 900,
    trusted_local: bool = False,
    image: str = "faultfinder-worker:local",
    model: str = "",
    provider: Annotated[str, typer.Option(help="ollama or api (Chat Completions)")] = "ollama",
    api_url: Annotated[str, typer.Option(help="HTTPS Chat Completions URL for api mode")] = "",
    api_key_env: Annotated[str, typer.Option(help="API key variable; prompts if unset")] = (
        "LLM_API_KEY"
    ),
) -> None:
    """Repeat existing tests, or investigate with an explicit model provider in Docker."""
    args = list(ctx.args) or [tests]
    if args and args[0] == "pytest":
        args.pop(0)
    if (model or provider == "api") and trusted_local:
        raise typer.BadParameter("Generated experiments require Docker; omit --trusted-local")
    plan = Plan(
        kind="pytest",
        title="Repeated test memory investigation",
        args=args,
        policy=policy(sizes, timeout),
    )
    agent = model_client(provider, model, api_url, api_key_env)
    try:
        bundle = investigate(
            project,
            plan,
            project / ".faultfinder" / "reports",
            "local" if trusted_local else "docker",
            image,
            agent,
        )
    finally:
        if agent:
            agent.close()
    finish(bundle)


@app.command()
def demo(
    case: str = "listeners",
    sizes: str = "0,100,500,1000",
    trusted_local: bool = False,
    output: Path = Path(".faultfinder/reports"),
) -> None:
    """Run the labeled seeded corpus; this is not a scan of your application."""
    if case not in CASES:
        raise typer.BadParameter(f"case must be one of {', '.join(CASES)}")
    plan = Plan(
        kind="demo", title=f"Seeded corpus: {case}", demo_case=case, policy=policy(sizes, 900)
    )
    # An empty source snapshot is sufficient for the installed, versioned corpus.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="faultfinder-corpus-") as temporary:
        bundle = investigate(
            Path(temporary), plan, output.resolve(), "local" if trusted_local else "docker"
        )
    finish(bundle)


@app.command()
def report(bundle: Project) -> None:
    """Re-render existing evidence without executing the workload."""
    evidence = Evidence.model_validate_json(read_text(bundle / "evidence.json"))
    console.print(str(write_report(evidence, bundle)))


@app.command()
def replay(
    bundle: Project, trusted_local: bool = False, output: Path = Path(".faultfinder/reports")
) -> None:
    """Check hashes and repeat the original experiment in fresh processes."""
    finish(replay_bundle(bundle, output.resolve(), trusted_local))


@app.command("prune")
def prune_command(
    output: Path = Path(".faultfinder/reports"), days: Annotated[int, typer.Option(min=1)] = 7
) -> None:
    """Remove completed valid bundles older than the requested retention period."""
    console.print(f"Removed {prune(output.resolve(), days)} bundles")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def watch(
    ctx: typer.Context,
    project: Project = Path("."),
    interval: Annotated[float, typer.Option(min=0.1)] = 5,
    warmup: Annotated[float, typer.Option(min=0)] = 60,
) -> None:
    """Observe a normal command; queue candidates for the separate worker."""
    if not ctx.args:
        raise typer.BadParameter("Supply a command after --, for example -- python app.py")
    import os

    metadata = inspect_project(project)
    state = project / ".faultfinder"
    state.mkdir(exist_ok=True)
    metrics = state / "metrics.json"
    env = dict(os.environ)
    env["FAULTFINDER_METRICS_FILE"] = str(metrics)
    store = Store(state / "jobs.sqlite3")
    detector = GrowthDetector(warmup=warmup)
    process = subprocess.Popen(ctx.args, cwd=project, env=env)
    started = time.monotonic()
    warned = False
    try:
        while process.poll() is None:
            try:
                proc = psutil.Process(process.pid)
                if proc.children() and not warned:
                    console.print("Child processes detected: memory coverage is incomplete.")
                    warned = True
                counters = {}
                if metrics.is_file():
                    try:
                        candidate = json.loads(metrics.read_text(encoding="utf-8"))
                        if (
                            candidate["pid"] == process.pid
                            and time.time() - candidate["timestamp"] < interval * 3
                        ):
                            counters = candidate
                    except (OSError, ValueError, KeyError):
                        pass
                sample = Sample(
                    time.monotonic() - started,
                    proc.memory_info().rss,
                    counters.get("completed"),
                    counters.get("active"),
                )
                finding = detector.add(sample)
                if finding:
                    fingerprint = hashlib.sha256(
                        (metadata["source_sha256"] + "rss").encode()
                    ).hexdigest()
                    job = store.enqueue(
                        fingerprint,
                        {
                            "project": str(project),
                            "finding": finding,
                            "source_sha256": metadata["source_sha256"],
                        },
                    )
                    console.print(f"Suspected growth; investigation queued: {job}")
            except psutil.NoSuchProcess:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("Stopping observed process.")
    finally:
        if process.poll() is None:
            terminate_tree(process)
    raise typer.Exit(process.returncode or 0)


@app.command("worker")
def worker_command(
    project: Project = Path("."),
    model: str = "",
    provider: Annotated[str, typer.Option(help="ollama or api (Chat Completions)")] = "ollama",
    api_url: Annotated[str, typer.Option(help="HTTPS Chat Completions URL for api mode")] = "",
    api_key_env: Annotated[str, typer.Option(help="API key variable; prompts if unset")] = (
        "LLM_API_KEY"
    ),
    image: str = "faultfinder-worker:local",
    once: bool = False,
) -> None:
    """Drain the production candidate queue using Docker; never run probes in the app."""
    store = Store(project / ".faultfinder" / "jobs.sqlite3")
    agent = model_client(provider, model, api_url, api_key_env)
    try:
        while True:
            job = store.claim()
            if job:
                payload = job["payload"]
                root = Path(payload["project"])
                if inspect_project(root)["source_sha256"] != payload["source_sha256"]:
                    store.finish(job["id"], "inconclusive: observed source has changed")
                else:
                    plan = Plan(
                        kind="pytest",
                        title="Production memory candidate",
                        args=["tests"],
                        policy=Policy(sizes=[0, 5, 10]),
                    )
                    stop_lease = threading.Event()
                    job_id = job["id"]

                    def renew_lease(
                        stop: threading.Event = stop_lease,
                        identifier: str = job_id,
                    ) -> None:
                        while not stop.wait(30):
                            store.renew(identifier)

                    heartbeat = threading.Thread(target=renew_lease, daemon=True)
                    heartbeat.start()
                    try:
                        bundle = investigate(
                            root,
                            plan,
                            root / ".faultfinder" / "reports",
                            "docker",
                            image,
                            agent,
                            observation=payload["finding"],
                        )
                        store.finish(job["id"], str(bundle))
                        console.print(str(bundle / "report.html"))
                    except Exception as exc:
                        store.finish(job["id"], f"inconclusive: {type(exc).__name__}")
                    finally:
                        stop_lease.set()
                        heartbeat.join(timeout=2)
            if once:
                break
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        if agent:
            agent.close()
