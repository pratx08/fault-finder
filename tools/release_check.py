"""Check the exact distributions, not the developer's editable checkout."""

import argparse
import email
import hashlib
import json
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


def check(dist: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wheels, sources = list(dist.glob("*.whl")), list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Release directory must contain exactly one wheel and one source archive")
    forbidden = {".env", ".git", ".venv", ".faultfinder", "__pycache__", "dist"}
    with zipfile.ZipFile(wheels[0]) as wheel:
        members = wheel.namelist()
        for name in members:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or forbidden.intersection(path.parts):
                raise ValueError(f"Forbidden wheel member: {name}")
            if path.parts[0] != "faultfinder" and not path.parts[0].endswith(".dist-info"):
                raise ValueError(f"Unexpected wheel content: {name}")
        metadata_file = next(n for n in members if n.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(wheel.read(metadata_file))
        for field, expected in [
            ("Name", project["name"]),
            ("Version", project["version"]),
            ("License-Expression", "MIT"),
        ]:
            if metadata[field] != expected:
                raise ValueError(f"Incorrect distribution metadata: {field}")
        if "faultfinder/templates/report.html" not in members:
            raise ValueError("HTML report template is not packaged")
        if not any(n.endswith("licenses/LICENSE") for n in members):
            raise ValueError("License is not packaged")
    with tarfile.open(sources[0]) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or forbidden.intersection(path.parts):
                raise ValueError(f"Forbidden source member: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError("Source distributions must not contain links")
    return {
        "name": project["name"],
        "version": project["version"],
        "artifacts": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [wheels[0], sources[0]]
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    print(json.dumps(check(args.dist), indent=2))
