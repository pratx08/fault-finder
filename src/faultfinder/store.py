"""Transactional single-worker queue with leases and candidate deduplication."""

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL,
                updated REAL NOT NULL, result TEXT NOT NULL DEFAULT '')""")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(self, fingerprint: str, payload: dict, cooldown: float = 3600) -> str:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
            if old and (old["state"] in {"queued", "running"} or now - old["updated"] < cooldown):
                return str(old["id"])
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0] >= 100:
                raise RuntimeError("Investigation queue is full")
            job_id = uuid.uuid4().hex
            db.execute("DELETE FROM jobs WHERE fingerprint=?", (fingerprint,))
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                (job_id, fingerprint, json.dumps(payload), "queued", now, ""),
            )
            return job_id

    def claim(self, lease_seconds: float = 1800) -> dict | None:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET state='queued' WHERE state='running' AND updated<?",
                (now - lease_seconds,),
            )
            if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY updated LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (now, row["id"]))
            return {"id": row["id"], "payload": json.loads(row["payload"])}

    def finish(self, job_id: str, result: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET state='complete', updated=?, result=? WHERE id=?",
                (time.time(), result, job_id),
            )

    def renew(self, job_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET updated=? WHERE id=? AND state='running'", (time.time(), job_id)
            )
