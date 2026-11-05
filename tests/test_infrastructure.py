import json
import sys
import time

import httpx
import pytest

from faultfinder.agents import AgentClient, Review
from faultfinder.discovery import inspect_project
from faultfinder.engine import investigate, validate_bundle
from faultfinder.execution import bounded_run, docker_binary, docker_command, run_trial
from faultfinder.models import Plan, Policy, Verdict
from faultfinder.store import Store


def test_discovery_does_not_import_target_and_excludes_secrets(tmp_path):
    (tmp_path / "app.py").write_text("raise RuntimeError('must not execute')\nx.append(y)\n")
    (tmp_path / ".env").write_text("SECRET=do-not-copy")
    info = inspect_project(tmp_path)
    assert list(info["files"]) == ["app.py"]
    assert info["candidates"][0]["line"] == 2


def test_queue_deduplicates_and_recovers_expired_lease(tmp_path):
    store = Store(tmp_path / "jobs.sqlite3")
    first = store.enqueue("same", {"project": "example"})
    assert store.enqueue("same", {}) == first
    assert store.claim()["id"] == first
    assert store.claim() is None
    with store.connect() as db:
        db.execute("UPDATE jobs SET updated=?", (time.time() - 2000,))
    assert store.claim()["id"] == first
    store.finish(first, "report")
    assert store.claim() is None


def test_generated_code_cannot_run_in_local_mode(tmp_path):
    plan = Plan(kind="probe", title="test", harness="raise RuntimeError()")
    result = run_trial(plan, tmp_path, "subject", 0, 0, "local", "", 1)
    assert not result.ok
    assert "forbidden" in result.error


def test_timeout_kills_worker(tmp_path):
    start = time.monotonic()
    _, error = bounded_run(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, 0.2, 256
    )
    assert "time budget" in error
    assert time.monotonic() - start < 5


def test_excessive_output_is_bounded(tmp_path):
    _, error = bounded_run([sys.executable, "-c", "print('x'*2000000)"], tmp_path, 5, 256)
    assert "output budget" in error


def test_docker_boundary_has_required_restrictions(tmp_path):
    args = docker_command("sha256:example", tmp_path, tmp_path, "test", 512)
    for restriction in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--user=65534:65534",
        "--pids-limit=128",
        "--memory=512m",
    ):
        assert restriction in args
    assert "docker.sock" not in " ".join(args)


def test_default_worker_image_is_built_once_when_missing(monkeypatch):
    import subprocess

    import faultfinder.execution as execution

    monkeypatch.setattr(execution, "docker_binary", lambda: "docker")
    commands = []
    inspections = 0

    def run(command, **kwargs):
        nonlocal inspections
        commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            inspections += 1
            return subprocess.CompletedProcess(
                command, 1 if inspections == 1 else 0, stdout="sha256:built\n"
            )
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, stdout="29.0\n")
        assert command[1] == "build"
        assert "fault-finder==" in kwargs["input"]
        assert execution.DEFAULT_IMAGE == command[3]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(execution.subprocess, "run", run)
    assert execution.docker_image_id(execution.DEFAULT_IMAGE) == "sha256:built"
    assert [command[1] for command in commands] == ["image", "info", "build", "image"]


def test_missing_custom_worker_image_does_not_build(monkeypatch):
    import subprocess

    import faultfinder.execution as execution

    monkeypatch.setattr(execution, "docker_binary", lambda: "docker")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="")

    monkeypatch.setattr(execution.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Custom Docker worker image"):
        execution.docker_image_id("custom-worker:v1")
    assert len(commands) == 1


def test_local_model_schema_is_validated_and_verdict_is_not_an_output():
    def respond(request):
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert "verdict" not in payload["format"]["properties"]
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"content": json.dumps({"explanation": "Control is missing."})},
            },
        )

    client = AgentClient("test", transport=httpx.MockTransport(respond))
    try:
        assert client.review({}) == "Control is missing."
    finally:
        client.close()


def test_malformed_model_output_fails_closed():
    client = AgentClient(
        "test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"done": True, "message": {"content": '{"verdict":"verified"}'}}
            )
        ),
    )
    try:
        with pytest.raises(ValueError):
            client.ask("review", {}, Review)
    finally:
        client.close()


def test_nonlocal_model_endpoint_rejected():
    with pytest.raises(ValueError):
        AgentClient("test", endpoint="https://example.com")


def test_unavailable_docker_produces_report_not_success(tmp_path, monkeypatch):
    import faultfinder.engine as engine

    def unavailable(_):
        raise RuntimeError("Docker unavailable")

    monkeypatch.setattr(engine, "docker_image_id", unavailable)
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("x = []\n")
    result = investigate(
        root, Plan(kind="pytest", title="<script>bad</script>"), tmp_path / "reports"
    )
    evidence = validate_bundle(result)
    assert evidence.assessment.verdict == Verdict.INCONCLUSIVE
    report = (result / "report.html").read_text(encoding="utf-8")
    assert "<script>bad</script>" not in report
    assert "&lt;script&gt;" in report
    (result / "source" / "app.py").write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        validate_bundle(result)


@pytest.mark.docker
def test_docker_executes_corpus(tmp_path):
    import shutil

    if not shutil.which(docker_binary()):
        pytest.skip("Docker is not installed")
    project = tmp_path / "project"
    project.mkdir()
    plan = Plan(
        kind="demo",
        demo_case="listeners",
        title="Docker corpus",
        policy=Policy(sizes=[0, 10, 30], warmup=4),
    )
    result = investigate(project, plan, tmp_path / "reports")
    assert validate_bundle(result).assessment.verdict == Verdict.VERIFIED
