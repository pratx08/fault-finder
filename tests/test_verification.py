import pytest

from faultfinder.models import Plan, Policy, Trial, Verdict
from faultfinder.verification import assess


def experiment(kind="demo", case="listeners"):
    plan = Plan(kind=kind, title="test", demo_case=case, policy=Policy(sizes=[0, 10, 30]))
    trials = [
        Trial(
            variant=v,
            size=n,
            repeat=r,
            ok=True,
            completed=n,
            survivors=n if v == "subject" else 0,
            after_release=0,
            owner_types=["cell"] if n else [],
            path_hits=n,
        )
        for v in ("subject", "control")
        for n in plan.policy.sizes
        for r in range(3)
    ]
    return plan, trials


def test_controlled_corpus_violation_is_verified():
    plan, trials = experiment()
    result = assess(plan, trials)
    assert result.verdict == Verdict.VERIFIED
    assert result.growth_per_operation == 1


def test_generated_harness_cannot_self_certify_lifetime_contract():
    plan, trials = experiment(kind="probe")
    assert assess(plan, trials).verdict == Verdict.RETENTION


@pytest.mark.parametrize("failure", ["missing", "duplicate", "failed", "partial", "uncovered"])
def test_incomplete_evidence_never_passes(failure):
    plan, trials = experiment(kind="probe")
    if failure == "missing":
        trials.pop()
    elif failure == "duplicate":
        trials.append(trials[-1])
    elif failure == "failed":
        trials[-1].ok = False
    elif failure == "partial":
        trials[-1].completed -= 1
    else:
        trials[-1].path_hits = 0
    assert assess(plan, trials).verdict == Verdict.INCONCLUSIVE


def test_failed_control_invalidates_explanation():
    plan, trials = experiment()
    for trial in trials:
        trial.survivors = trial.size
    assert assess(plan, trials).verdict == Verdict.INCONCLUSIVE


def test_plateau_is_not_unbounded_growth():
    plan, trials = experiment()
    for trial in trials:
        trial.survivors = min(trial.size, 8)
    assert assess(plan, trials).verdict == Verdict.NO_EVIDENCE


def test_noise_outlier_does_not_become_verified():
    plan, trials = experiment()
    trials[8].survivors = 0
    assert assess(plan, trials).verdict == Verdict.NO_EVIDENCE


@pytest.mark.parametrize("sizes", [[1, 2, 3], [0, 1], [0, 2, 2], [0, 4, 2], [0, 1, 100001]])
def test_invalid_protocol_is_rejected(sizes):
    with pytest.raises(ValueError):
        Policy(sizes=sizes)
