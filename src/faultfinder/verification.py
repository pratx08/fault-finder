"""Conservative, deterministic decisions. Agents cannot set verdicts."""

from statistics import median

from faultfinder.models import Assessment, Plan, Trial, Verdict

VERIFIED_DEMOS = {"listeners", "registry", "task_results"}


def assess(plan: Plan, trials: list[Trial]) -> Assessment:
    variants = ["subject"] if plan.kind == "pytest" else ["subject", "control"]
    expected = {
        (v, n, r) for v in variants for n in plan.policy.sizes for r in range(plan.policy.repeats)
    }
    actual = [(t.variant, t.size, t.repeat) for t in trials]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        return Assessment(verdict=Verdict.INCONCLUSIVE, reason="Missing or duplicate trial records")
    if any(not t.ok or t.completed != t.size for t in trials):
        return Assessment(
            verdict=Verdict.INCONCLUSIVE, reason="Workload failed or did not complete"
        )
    if plan.kind == "probe" and any(t.path_hits == 0 for t in trials if t.size > 0):
        return Assessment(verdict=Verdict.INCONCLUSIVE, reason="Target path was not exercised")
    counts = all(t.survivors is not None for t in trials)
    metric = "survivors" if counts else "retained_bytes"
    if any(getattr(t, metric) is None for t in trials):
        return Assessment(verdict=Verdict.INCONCLUSIVE, reason="Measurement is unavailable")
    values = {
        (v, n): [getattr(t, metric) for t in trials if t.variant == v and t.size == n]
        for v in variants
        for n in plan.policy.sizes
    }
    tolerance = 0 if counts else plan.policy.noise_bytes
    sizes = plan.policy.sizes
    base = values["subject", 0]
    last = values["subject", sizes[-1]]
    # Require separation across every repetition, not just a favorable average.
    separated = min(last) > max(base) + tolerance
    increasing = all(
        median(values["subject", b]) > median(values["subject", a])
        for a, b in zip(sizes[1:-1], sizes[2:], strict=True)
    )
    if not separated or not increasing:
        return Assessment(
            verdict=Verdict.NO_EVIDENCE,
            reason="No repeatable increasing retention in the exercised workload",
            metric=metric,
        )
    slope = (median(last) - median(base)) / sizes[-1]
    if "control" not in variants:
        return Assessment(
            verdict=Verdict.SUSPECTED,
            metric=metric,
            growth_per_operation=slope,
            reason="Repeatable traced growth; no independent lifecycle control",
            limitations=["Repeated tests can retain fixtures or test-runner state"],
        )
    control_base = values["control", 0]
    control_last = values["control", sizes[-1]]
    if max(control_last) > min(control_base) + tolerance:
        return Assessment(
            verdict=Verdict.INCONCLUSIVE,
            metric=metric,
            reason="Control also retains memory; explanation is not isolated",
        )
    trusted_contract = plan.kind == "demo" and plan.demo_case in VERIFIED_DEMOS
    released = all(t.after_release == 0 for t in trials)
    owners = all(t.owner_types for t in trials if t.variant == "subject" and t.size > 0)
    if trusted_contract and counts and released and owners:
        return Assessment(
            verdict=Verdict.VERIFIED,
            metric=metric,
            growth_per_operation=slope,
            reason="Completed objects outlive the corpus cleanup contract; "
            "controls and releasing the retaining owner eliminate retention",
            limitations=["Seeded corpus result; not a production impact estimate"],
        )
    return Assessment(
        verdict=Verdict.RETENTION,
        metric=metric,
        growth_per_operation=slope,
        reason="Repeated retention with a healthy control; lifecycle intent "
        "still requires independent confirmation",
        limitations=["Generated harness is evidence, not an authoritative contract"],
    )
