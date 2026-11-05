"""Offline report rendering; application and model text are always escaped."""

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from faultfinder.io import atomic_text
from faultfinder.models import Evidence


def write_report(evidence: Evidence, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    data = evidence.model_dump(mode="json")
    atomic_text(output / "evidence.json", evidence.model_dump_json(indent=2))
    metric = evidence.assessment.metric or "retained_bytes"
    points = [
        (t.size, getattr(t, metric), t.variant)
        for t in evidence.trials
        if t.ok and getattr(t, metric, None) is not None
    ]
    maximum_x = max((x for x, _, _ in points), default=1) or 1
    maximum_y = max((max(y, 0) for _, y, _ in points), default=1) or 1
    chart = [
        {
            "x": 50 + 680 * x / maximum_x,
            "y": 230 - 190 * max(y, 0) / maximum_y,
            "value": y,
            "size": x,
            "variant": v,
        }
        for x, y, v in points
    ]
    environment = Environment(
        loader=PackageLoader("faultfinder", "templates"), autoescape=select_autoescape(["html"])
    )
    html = environment.get_template("report.html").render(
        evidence=data, chart=chart, metric=metric, maximum_y=maximum_y, maximum_x=maximum_x
    )
    target = output / "report.html"
    atomic_text(target, html)
    return target
