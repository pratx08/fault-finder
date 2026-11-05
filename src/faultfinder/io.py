"""Bounded, atomic artifact I/O. Never publish partially written evidence."""

import os
import tempfile
from pathlib import Path

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def read_text(path: Path, limit: int = MAX_ARTIFACT_BYTES) -> str:
    if path.is_symlink():
        raise ValueError("Artifact must not be a symlink")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Artifact exceeds the read budget")
    return data.decode("utf-8")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
