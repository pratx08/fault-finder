"""Read source and metadata without importing the target application."""

import ast
import hashlib
import os
import subprocess
import tomllib
from pathlib import Path

from faultfinder.models import Candidate

IGNORED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".faultfinder",
    "dist",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
MAX_FILES = 5000
MAX_FILE_BYTES = 512_000


def source_files(root: Path) -> list[Path]:
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in IGNORED
            and not Path(directory, d).is_symlink()
            and Path(directory, d).resolve().is_relative_to(root.resolve())
        )
        for name in sorted(files):
            path = Path(directory, name)
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            if path.suffix == ".py" or name in {
                "pyproject.toml",
                "uv.lock",
                "poetry.lock",
                "requirements.txt",
                "pytest.ini",
            }:
                result.append(path)
                if len(result) > MAX_FILES:
                    raise ValueError(
                        f"Source exceeds {MAX_FILES} files; choose a smaller project root"
                    )
    return sorted(result)


def git_value(root: Path, *args: str) -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, timeout=5
            )
            .decode()
            .strip()
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def inspect_project(root: Path) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project must be a directory")
    files = source_files(root)
    hashes = {}
    candidates = []
    dependencies: list[str] = []
    for path in files:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"Source file too large for bounded discovery: {path.name}")
        data = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(data).hexdigest()
        if path.name == "pyproject.toml":
            metadata = tomllib.loads(data.decode("utf-8"))
            dependencies.extend(metadata.get("project", {}).get("dependencies", []))
        if path.suffix == ".py" and not path.name.startswith("test_"):
            try:
                tree = ast.parse(data, filename=relative)
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr in {"append", "add", "add_done_callback", "add_event_listener"}:
                    candidates.append(
                        Candidate(
                            file=relative,
                            line=node.lineno,
                            kind="possible_retaining_owner",
                            reason=f"{node.func.attr} may retain references; check its lifecycle",
                        ).model_dump()
                    )
    digest = hashlib.sha256(repr(sorted(hashes.items())).encode()).hexdigest()
    return {
        "root": str(root),
        "commit": git_value(root, "rev-parse", "HEAD"),
        "dirty": bool(git_value(root, "status", "--porcelain")),
        "source_sha256": digest,
        "files": hashes,
        "tests": [p for p in hashes if Path(p).name.startswith("test_")],
        "dependencies": dependencies,
        "candidates": candidates[:100],
        "candidate_count": len(candidates),
        "support": "CPython 3.11/3.12; single process; Python allocations only",
    }
