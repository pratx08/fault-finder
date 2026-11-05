import json
import subprocess
import sys

import pytest

from faultfinder.engine import investigate, replay, validate_bundle
from faultfinder.models import Plan, Policy, Verdict
from faultfinder.worker import execute


@pytest.mark.parametrize(
    "case",
    ["listeners", "registry", "task_results", "bounded_cache", "cycles", "temporary", "lazy"],
)
def test_measurement_corpus(case, tmp_path):
    plan = Plan(kind="demo", title=case, demo_case=case, policy=Policy(sizes=[0, 10, 30], warmup=4))
    result = execute(
        {
            "plan": plan.model_dump(),
            "root": str(tmp_path),
            "harness": "",
            "variant": "subject",
            "size": 30,
            "repeat": 0,
        }
    )
    assert result.ok, result.error
    assert result.completed == 30
    assert result.survivors == (
        30
        if case in {"listeners", "registry", "task_results"}
        else 8
        if case == "bounded_cache"
        else 0
    )
    assert result.after_release == 0


def test_complete_investigation_and_replay(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    plan = Plan(
        kind="demo",
        title="listeners",
        demo_case="listeners",
        policy=Policy(sizes=[0, 10, 30], warmup=4),
    )
    output = investigate(project, plan, tmp_path / "reports", "local")
    evidence = validate_bundle(output)
    assert evidence.assessment.verdict == Verdict.VERIFIED
    assert len(evidence.trials) == 18
    assert all(t.runtime["python"] for t in evidence.trials)
    again = replay(output, tmp_path / "replays", trusted_local=True)
    assert validate_bundle(again).assessment.verdict == evidence.assessment.verdict


@pytest.mark.parametrize(
    "source,expected",
    [
        ("def test_ok():\n    assert 1+1 == 2\n", Verdict.NO_EVIDENCE),
        (
            "import pytest\n@pytest.mark.asyncio\nasync def test_async():\n    assert True\n",
            Verdict.NO_EVIDENCE,
        ),
        ("def test_fail():\n    assert False\n", Verdict.INCONCLUSIVE),
        ("import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n", Verdict.INCONCLUSIVE),
    ],
)
def test_existing_pytest_workload(source, expected, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_app.py").write_text(source)
    plan = Plan(
        kind="pytest", title="Existing tests", args=["test_app.py"], policy=Policy(sizes=[0, 1, 2])
    )
    output = investigate(project, plan, tmp_path / "reports", "local")
    result = validate_bundle(output)
    assert result.assessment.verdict == expected, result.model_dump_json()


def test_cli_inspect_runs_from_installed_package(tmp_path):
    (tmp_path / "app.py").write_text("items = []\n")
    result = subprocess.run(
        [sys.executable, "-m", "faultfinder", "inspect", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["files"]["app.py"]
