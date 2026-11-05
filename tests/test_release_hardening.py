import subprocess
import sys
from pathlib import Path

import pytest

from faultfinder.execution import run_trial
from faultfinder.io import atomic_text, read_text
from faultfinder.models import Plan, Trial


def test_artifact_read_budget(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * 20)
    with pytest.raises(ValueError, match="budget"):
        read_text(path, limit=10)


def test_atomic_write_replaces_complete_document(tmp_path):
    path = tmp_path / "evidence.json"
    atomic_text(path, '{"old": true}')
    atomic_text(path, '{"new": true}')
    assert read_text(path) == '{"new": true}'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("field", ["size", "repeat", "completed", "survivors", "path_hits"])
def test_negative_measurements_rejected(field):
    data = {"variant": "subject", "size": 0, "repeat": 0, field: -1}
    with pytest.raises(ValueError):
        Trial.model_validate(data)


def test_unknown_backend_does_not_fall_back_to_host_execution(tmp_path):
    result = run_trial(
        Plan(kind="pytest", title="invalid backend"), tmp_path, "subject", 0, 0, "doker", "", 5
    )
    assert not result.ok
    assert "Unknown" in result.error


def test_invalid_evidence_has_clean_cli_error(tmp_path):
    (tmp_path / "evidence.json").write_text('{"secret": "do-not-print"}')
    result = subprocess.run(
        [sys.executable, "-m", "faultfinder", "report", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "do-not-print" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_version_command():
    result = subprocess.run(
        [sys.executable, "-m", "faultfinder", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "0.1.0" in result.stdout


def test_cloud_model_tag_rejected():
    from faultfinder.agents import AgentClient

    with pytest.raises(ValueError, match="Cloud"):
        AgentClient("example:cloud")


def test_cloud_alias_rejected_from_model_metadata():
    import httpx

    from faultfinder.agents import AgentClient

    client = AgentClient(
        "innocent-alias",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"remote_host": "https://cloud.example", "model_info": {}}
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="Cloud"):
            client.validate_model()
    finally:
        client.close()


def test_wheel_selection_is_explicit():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/faultfinder"]
    assert config["project"]["license"] == "MIT"
