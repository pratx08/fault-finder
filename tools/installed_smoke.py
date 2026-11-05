"""Run with the wheel-installed interpreter, from outside the source checkout."""

import subprocess
import sys
import tempfile
from pathlib import Path

from faultfinder.engine import investigate, validate_bundle
from faultfinder.models import Plan, Policy, Verdict


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="installed-smoke-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        (project / "test_example.py").write_text("def test_example():\n    assert 2+2 == 4\n")
        subprocess.run(
            [sys.executable, "-m", "faultfinder", "--version"], cwd=root, check=True, timeout=30
        )
        bundle = investigate(
            project,
            Plan(
                kind="pytest",
                title="Installed wheel smoke",
                args=["test_example.py"],
                policy=Policy(sizes=[0, 1, 2]),
            ),
            root / "reports",
            backend="local",
        )
        evidence = validate_bundle(bundle)
        assert evidence.assessment.verdict == Verdict.NO_EVIDENCE, evidence.model_dump_json()
        assert (bundle / "report.html").is_file()
        print("Installed wheel: CLI, subprocess worker, pytest, evidence and HTML passed")


if __name__ == "__main__":
    main()
