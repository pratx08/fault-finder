import json

import httpx
import pytest

from faultfinder.agents import AgentClient
from faultfinder.discovery import inspect_project
from faultfinder.models import Policy
from faultfinder.worker import execute

APP = """
class Payload:
    pass

class Owner:
    def __init__(self):
        self.items = []

    def run(self, retain):
        item = Payload()
        if retain:
            self.items.append(item)
        return item
"""

HARNESS = """
from subject import Owner

def build(variant, track):
    class Scenario:
        def __init__(self):
            self.owner = Owner()
        def run(self):
            item = self.owner.run(variant == "subject")
            assert item is not None
            track(item)
        def close(self):
            self.owner.items.clear()
    return Scenario()
"""


def test_agent_roles_plan_a_real_source_probe_and_worker_measures_it(tmp_path):
    (tmp_path / "subject.py").write_text(APP)
    line = APP.splitlines().index("        item = Payload()") + 1
    answers = iter(
        [
            {
                "explanation": "Owner retains completed items",
                "target_file": "subject.py",
                "target_line": line,
                "expected_lifetime": "Unknown until independently confirmed",
            },
            {"code": HARNESS, "control_explanation": "Same method with retention disabled"},
        ]
    )

    def response(request):
        return httpx.Response(
            200, json={"done": True, "message": {"content": json.dumps(next(answers))}}
        )

    client = AgentClient("mock-protocol-only", transport=httpx.MockTransport(response))
    try:
        plan = client.plan(inspect_project(tmp_path), tmp_path)
    finally:
        client.close()
    plan.policy = Policy(sizes=[0, 10, 30], warmup=4)
    harness = tmp_path / "harness.py"
    harness.write_text(plan.harness)
    # This test executes a test-owned fixture, not arbitrary model output.
    for variant, expected in [("subject", 30), ("control", 0)]:
        result = execute(
            {
                "plan": plan.model_dump(),
                "root": str(tmp_path),
                "harness": str(harness),
                "variant": variant,
                "size": 30,
                "repeat": 0,
            }
        )
        assert result.ok, result.error
        assert result.path_hits >= 30
        assert result.survivors == expected
        assert result.after_release == 0


def test_agent_cannot_select_source_outside_project(tmp_path):
    (tmp_path / "subject.py").write_text(APP)
    result = {
        "explanation": "bad",
        "target_file": "../secret.py",
        "target_line": 1,
        "expected_lifetime": "bad",
    }
    client = AgentClient(
        "mock",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"done": True, "message": {"content": json.dumps(result)}}
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="unavailable source"):
            client.plan(inspect_project(tmp_path), tmp_path)
    finally:
        client.close()
