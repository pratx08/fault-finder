"""Evaluate FaultFinder on the real BeeAI regression using a reviewed fixture.

This is a known-bug evaluation, not autonomous discovery. The checkout is never modified.
Use an environment containing the target dependencies, or --dependency-path for an existing
compatible Python environment. Each trial runs in a fresh process against a Git archive.
"""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HARNESS = """
from beeai_framework.context import RunContext
from beeai_framework.emitter import Emitter
from beeai_framework.utils.cancellation import AbortController

def build(variant, track):
    class Instance:
        def __init__(self):
            self.emitter = Emitter(namespace=["faultfinder_evaluation"])
    class Scenario:
        def __init__(self):
            self.instance = Instance()
            self.source = AbortController()
        async def run(self):
            async def handler(context):
                track(context._controller)
                return 42
            result = await RunContext.enter(
                self.instance, handler,
                signal=self.source.signal if variant == "subject" else None)
            assert result == 42
        def close(self):
            self.source = None
            self.instance.emitter.destroy()
    return Scenario()
"""


def child(args):
    if args.dependency_path:
        sys.path.append(str(args.dependency_path))
    from faultfinder.worker import execute

    request = json.loads(args.child.read_text(encoding="utf-8"))
    args.result.write_text(execute(request).model_dump_json(indent=2), encoding="utf-8")


def main(args):
    from faultfinder.models import Plan, Policy, Trial
    from faultfinder.verification import assess

    records = []
    for revision in [args.baseline, args.fixed]:
        sha = subprocess.check_output(
            ["git", "-C", str(args.repo), "rev-parse", revision], text=True
        ).strip()
        archive = subprocess.check_output(
            ["git", "-C", str(args.repo), "archive", "--format=zip", sha, "python/beeai_framework"]
        )
        with tempfile.TemporaryDirectory(prefix="faultfinder-beeai-") as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                for member in zipped.infolist():
                    if not (root / member.filename).resolve().is_relative_to(root.resolve()):
                        raise ValueError("Archive path escapes extraction directory")
                zipped.extractall(root)
            source = root / "python"
            context = source / "beeai_framework/context.py"
            line = next(
                i
                for i, text in enumerate(context.read_text().splitlines(), 1)
                if "self._controller = AbortController()" in text
            )
            plan = Plan(
                kind="probe",
                title="Known BeeAI regression",
                harness=HARNESS,
                target_file="beeai_framework/context.py",
                target_line=line,
                policy=Policy(sizes=[0, 100, 1000], warmup=4),
            )
            harness = root / "harness.py"
            harness.write_text(HARNESS, encoding="utf-8")
            trials = []
            for variant in ["subject", "control"]:
                for size in plan.policy.sizes:
                    for repeat in range(3):
                        request = root / "request.json"
                        request.write_text(
                            json.dumps(
                                {
                                    "plan": plan.model_dump(),
                                    "root": str(source),
                                    "harness": str(harness),
                                    "variant": variant,
                                    "size": size,
                                    "repeat": repeat,
                                }
                            ),
                            encoding="utf-8",
                        )
                        output = root / "result.json"
                        command = [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--child",
                            str(request),
                            "--result",
                            str(output),
                        ]
                        if args.dependency_path:
                            command.extend(["--dependency-path", str(args.dependency_path)])
                        subprocess.run(command, check=True, timeout=120)
                        trial = Trial.model_validate_json(output.read_text(encoding="utf-8"))
                        assert trial.ok, trial.error
                        expected = size if revision == args.baseline and variant == "subject" else 0
                        assert trial.survivors == expected, trial.model_dump()
                        assert trial.after_release == 0
                        trials.append(trial)
            assessment = assess(plan, trials)
            records.append(
                {
                    "commit": sha,
                    "assessment": assessment.model_dump(mode="json"),
                    "trials": [t.model_dump() for t in trials],
                }
            )
            print(f"{sha[:12]}: {assessment.verdict}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "scope": "Reviewed harness against real Git revisions; not agent discovery",
                "comparisons": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--baseline", default="d6896400a47b7586be80dccea5d5f4ef805f1ddb")
    parser.add_argument("--fixed", default="HEAD")
    parser.add_argument("--dependency-path", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".faultfinder/beeai-validation.json"))
    parser.add_argument("--child", type=Path)
    parser.add_argument("--result", type=Path)
    arguments = parser.parse_args()
    child(arguments) if arguments.child else main(arguments)
