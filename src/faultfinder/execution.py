"""Bounded subprocesses and a restrictive Docker execution boundary."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path

import psutil

from faultfinder import __version__
from faultfinder.io import read_text
from faultfinder.models import Plan, Trial

MAX_OUTPUT = 1_000_000
DEFAULT_IMAGE = f"faultfinder-worker:{__version__}"


def docker_binary() -> str:
    found = shutil.which("docker")
    if found:
        return found
    if os.name == "nt" and (local := os.environ.get("LOCALAPPDATA")):
        candidate = Path(local) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
        if candidate.is_file():
            return str(candidate)
    return "docker"


def terminate_tree(process: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.Error:
                pass
        parent.kill()
        psutil.wait_procs([parent, *children], timeout=3)
    except psutil.Error:
        pass
    process.wait(timeout=5)


def bounded_run(
    command: list[str], cwd: Path, timeout: float, memory_mb: int, env: dict[str, str] | None = None
) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    overflow = threading.Event()

    def drain() -> None:
        total = 0
        assert process.stdout is not None
        while chunk := process.stdout.read(8192):
            total += len(chunk)
            if total > MAX_OUTPUT:
                overflow.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    start = time.monotonic()
    error = ""
    descendants: dict[int, psutil.Process] = {}
    try:
        while process.poll() is None:
            if time.monotonic() - start > timeout:
                error = "Trial exceeded time budget"
                break
            if overflow.is_set():
                error = "Trial exceeded output budget"
                break
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                descendants.update((p.pid, p) for p in children)
                total = sum(p.memory_info().rss for p in [parent, *children])
                if total > memory_mb * 1024 * 1024:
                    error = "Trial exceeded memory budget"
                    break
            except psutil.NoSuchProcess:
                pass
            time.sleep(0.025)
    finally:
        if process.poll() is None:
            terminate_tree(process)
        for child in descendants.values():
            try:
                if child.is_running():
                    child.kill()
            except psutil.Error:
                pass
        reader.join(timeout=2)
        if process.stdout is not None and not reader.is_alive():
            process.stdout.close()
    if overflow.is_set():
        error = error or "Trial exceeded output budget"
    return process.returncode, error


def docker_image_id(image: str) -> str:
    binary = docker_binary()
    try:
        result = subprocess.run(
            [binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Docker is unavailable; install/start Docker for isolated execution"
        ) from exc
    if result.returncode != 0:
        if image != DEFAULT_IMAGE:
            raise RuntimeError("Custom Docker worker image is unavailable")
        try:
            engine = subprocess.run(
                [binary, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Docker engine is unavailable") from exc
        if engine.returncode != 0:
            raise RuntimeError("Docker engine is unavailable")
        dockerfile = (
            "FROM python:3.12-slim\n"
            f"RUN python -m pip install --no-cache-dir fault-finder=={__version__}\n"
            "ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp\n"
            "USER 65534:65534\n"
            "WORKDIR /project\n"
            'ENTRYPOINT ["python", "-m", "faultfinder.worker"]\n'
        )
        try:
            built = subprocess.run(
                [binary, "build", "--tag", DEFAULT_IMAGE, "-"],
                input=dockerfile,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Automatic Docker worker build failed") from exc
        if built.returncode != 0:
            raise RuntimeError("Automatic Docker worker build failed")
        result = subprocess.run(
            [binary, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError("Built Docker image could not be inspected")
    value = result.stdout.strip()
    if not value.startswith("sha256:"):
        raise RuntimeError("Could not resolve an immutable Docker image ID")
    return value


def docker_command(image: str, source: Path, io: Path, name: str, memory_mb: int) -> list[str]:
    # Bind only the allowlisted snapshot. Code can still contain hardcoded secrets.
    return [
        docker_binary(),
        "run",
        "--rm",
        "--name",
        name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--pids-limit=128",
        "--cpus=1",
        f"--memory={memory_mb}m",
        f"--memory-swap={memory_mb}m",
        "--tmpfs=/tmp:rw,nosuid,size=256m",
        "--mount",
        f"type=bind,source={source},target=/project,readonly",
        "--mount",
        f"type=bind,source={io},target=/output",
        "--workdir=/project",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "--env=HOME=/tmp",
        "--entrypoint=python",
        image,
        "-m",
        "faultfinder.worker",
        "/output/request.json",
        "/output/result.json",
    ]


def run_trial(
    plan: Plan,
    source: Path,
    variant: str,
    size: int,
    repeat: int,
    backend: str,
    image: str,
    timeout: float,
) -> Trial:
    failed = Trial(variant=variant, size=size, repeat=repeat)  # type: ignore[arg-type]
    if backend not in {"local", "docker"}:
        failed.error = "Unknown execution backend"
        return failed
    if backend == "local" and plan.kind == "probe":
        failed.error = "Generated harnesses require Docker; local execution is forbidden"
        return failed
    with tempfile.TemporaryDirectory(prefix="faultfinder-") as temporary:
        io = Path(temporary)
        # The unprivileged container user needs to write a result on Linux hosts.
        io.chmod(0o777)
        harness = io / "harness.py"
        harness.write_text(plan.harness, encoding="utf-8")
        request = {
            "plan": plan.model_dump(),
            "variant": variant,
            "size": size,
            "repeat": repeat,
            "root": str(source),
            "harness": str(harness),
        }
        name = f"faultfinder-{uuid.uuid4().hex[:16]}"
        if backend == "docker":
            request.update(root="/project", harness="/output/harness.py")
            command = docker_command(image, source, io, name, plan.policy.memory_mb)
            env = None
        else:
            command = [
                sys.executable,
                "-m",
                "faultfinder.worker",
                str(io / "request.json"),
                str(io / "result.json"),
            ]
            allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "SYSTEMDRIVE"}
            env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
            env.update(
                PYTHONDONTWRITEBYTECODE="1",
                PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                PYTHONPATH=str(Path(__file__).resolve().parent.parent),
            )
        (io / "request.json").write_text(json.dumps(request), encoding="utf-8")
        try:
            code, error = bounded_run(command, source, timeout, plan.policy.memory_mb, env)
            if error or code != 0:
                failed.error = error or f"Worker exited with code {code}"
                return failed
            result = io / "result.json"
            if not result.is_file() or result.is_symlink() or result.stat().st_size > MAX_OUTPUT:
                failed.error = "Worker result is missing or invalid"
                return failed
            trial = Trial.model_validate_json(read_text(result, MAX_OUTPUT))
            if (trial.variant, trial.size, trial.repeat) != (variant, size, repeat):
                raise ValueError("Worker result identity mismatch")
            return trial
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            failed.error = f"Execution failed: {type(exc).__name__}"
            return failed
        finally:
            if backend == "docker":
                with suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(
                        [docker_binary(), "rm", "-f", name], capture_output=True, timeout=10
                    )
