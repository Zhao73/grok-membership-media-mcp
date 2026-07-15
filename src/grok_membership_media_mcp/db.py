from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class JobStateConflict(RuntimeError):
    def __init__(self, job: dict[str, Any]):
        self.job = job
        super().__init__(f"job {job['id']} is already {job['status']}")


class OutputReservationError(RuntimeError):
    def __init__(self, job: dict[str, Any]):
        self.job = job
        super().__init__(
            f"output is reserved by job {job['id']} ({job['status']})"
        )


class JobStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT,
                    output_key TEXT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    phase TEXT NOT NULL,
                    submission TEXT NOT NULL,
                    retry_safe INTEGER NOT NULL DEFAULT 0,
                    pid INTEGER,
                    log_path TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT")
            if "output_key" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN output_key TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key_idx "
                "ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_output_key_idx "
                "ON jobs(output_key) WHERE output_key IS NOT NULL"
            )

    def create(
        self,
        kind: str,
        request: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        output_key: str | None = None,
    ) -> dict[str, Any]:
        job_id = f"med_{uuid.uuid4().hex}"
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing_row = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing_row is not None:
                    existing = self._decode(existing_row)
                    if self.can_safely_retry(existing):
                        connection.execute(
                            "UPDATE jobs SET idempotency_key = NULL, output_key = NULL "
                            "WHERE id = ?",
                            (existing["id"],),
                        )
                    else:
                        connection.commit()
                        existing["reused"] = True
                        return existing
            if output_key is not None:
                reserved_row = connection.execute(
                    "SELECT * FROM jobs WHERE output_key = ?", (output_key,)
                ).fetchone()
                if reserved_row is not None:
                    reserved = self._decode(reserved_row)
                    if self.can_safely_retry(reserved):
                        connection.execute(
                            "UPDATE jobs SET output_key = NULL WHERE id = ?",
                            (reserved["id"],),
                        )
                    else:
                        connection.commit()
                        raise OutputReservationError(reserved)
            connection.execute(
                """
                INSERT INTO jobs
                (id, idempotency_key, output_key, kind, status, request_json, phase,
                 submission, retry_safe, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, 'queued', 'not_submitted', 1, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    output_key,
                    kind,
                    json.dumps(request, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get(job_id)

    @staticmethod
    def can_safely_retry(job: dict[str, Any]) -> bool:
        return bool(
            job["status"] == "failed"
            and job["submission"] == "not_submitted"
            and job["retry_safe"]
        )

    def get_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        if row is None:
            raise KeyError(idempotency_key)
        return self._decode(row)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        return self._update(job_id, expected_statuses=None, fields=fields)

    def update_if_status(
        self,
        job_id: str,
        expected_statuses: set[str],
        **fields: Any,
    ) -> dict[str, Any]:
        return self._update(job_id, expected_statuses=expected_statuses, fields=fields)

    def _update(
        self,
        job_id: str,
        *,
        expected_statuses: set[str] | None,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "status",
            "result_json",
            "error",
            "phase",
            "submission",
            "retry_safe",
            "pid",
            "log_path",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        values: list[Any] = []
        assignments: list[str] = []
        for key, value in fields.items():
            if key == "result_json" and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            if key == "retry_safe":
                value = int(bool(value))
            assignments.append(f"{key} = ?")
            values.append(value)
        assignments.append("updated_at = ?")
        values.append(time.time())
        values.append(job_id)
        where = "id = ?"
        if expected_statuses is not None:
            if not expected_statuses:
                raise ValueError("expected_statuses must not be empty")
            placeholders = ", ".join("?" for _ in expected_statuses)
            where += f" AND status IN ({placeholders})"
            values.extend(sorted(expected_statuses))
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE {where}", values
            )
        if cursor.rowcount != 1:
            current = self.get(job_id)
            if expected_statuses is not None:
                raise JobStateConflict(current)
            raise KeyError(job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._decode(row)

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["request"] = json.loads(data.pop("request_json"))
        raw_result = data.pop("result_json")
        data["result"] = json.loads(raw_result) if raw_result else None
        data["retry_safe"] = bool(data["retry_safe"])
        return data
