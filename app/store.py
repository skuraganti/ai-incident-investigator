"""Independent SQLite cases and model attempts; repairs are data, never actions."""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from app.evidence import MODES, PROBES


def identity(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Canonical UUID required")
    return value


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
        CREATE TABLE IF NOT EXISTS cases(
            id TEXT PRIMARY KEY, token TEXT UNIQUE, scenario TEXT, state TEXT,
            created REAL, captured REAL, evidence TEXT, evidence_digest TEXT,
            raw TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS analyses(
            id TEXT PRIMARY KEY, token TEXT UNIQUE, case_id TEXT, policy TEXT, probe TEXT,
            state TEXT, created REAL, started REAL, finished REAL, published INTEGER DEFAULT0,
            evidence_digest TEXT, result TEXT, error TEXT, usage TEXT, model_ms REAL,
            raw_output TEXT);
        """.replace("DEFAULT0", "DEFAULT 0")
        )
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    @staticmethod
    def decode(row):
        result = dict(row)
        for key in ["evidence", "raw", "result", "usage"]:
            if key in result and result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def rows(self, kind):
        table = "cases" if kind == "cases" else "analyses"
        with self.lock:
            return [
                self.decode(r)
                for r in self.db.execute(
                    f"SELECT * FROM {table} ORDER BY created DESC,id DESC"
                ).fetchall()
            ]

    def get(self, kind, identifier):
        identity(identifier)
        table = "cases" if kind == "cases" else "analyses"
        with self.lock:
            row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("Unknown local record")
            return self.decode(row)

    def capture(self, value):
        if not isinstance(value, dict) or set(value) != {"scenario", "token"}:
            raise ValueError("Expected scenario and idempotency token")
        mode, token = value["scenario"], identity(value["token"])
        if not isinstance(mode, str) or mode not in MODES:
            raise ValueError("Unknown allowed lab scenario")
        with self.lock:
            prior = self.db.execute("SELECT * FROM cases WHERE token=?", (token,)).fetchone()
            if prior:
                if prior["scenario"] != mode:
                    raise ValueError("Token already used for another scenario")
                return prior["id"]
            rows = self.rows("cases")
            if len(rows) >= 40 or sum(r["state"] in {"queued", "capturing"} for r in rows) >= 2:
                raise ValueError("Capture bound:40 cases and2 pending")
            identifier = str(uuid.uuid4())
            with self.db:
                self.db.execute(
                    "INSERT INTO cases(id,token,scenario,state,created) VALUES(?,?,?,?,?)",
                    (identifier, token, mode, "queued", time.time()),
                )
            return identifier

    def mark_capture(self, identifier):
        with self.lock, self.db:
            self.db.execute("UPDATE cases SET state='capturing' WHERE id=?", (identifier,))

    def finish_capture(self, identifier, captured=None, error=None):
        with self.lock, self.db:
            self.db.execute(
                """UPDATE cases SET state=?,captured=?,evidence=?,evidence_digest=?,
                raw=?,error=? WHERE id=? AND state IN ('queued','capturing')""",
                (
                    "failed" if error is not None else "complete",
                    time.time(),
                    json.dumps(captured["bundle"], allow_nan=False) if captured else None,
                    captured["digest"] if captured else None,
                    json.dumps(captured, allow_nan=False) if captured else None,
                    (error or "Capture failed")[:300] if error is not None else None,
                    identifier,
                ),
            )

    def analyze(self, value, evidence_digest):
        if not isinstance(value, dict) or set(value) != {"case_id", "policy", "probe", "token"}:
            raise ValueError("Expected case_id, policy, fixed probe and token")
        case_id, token = identity(value["case_id"]), identity(value["token"])
        if value["policy"] not in {"baseline", "model"} or value["probe"] not in PROBES:
            raise ValueError("Unknown policy or probe")
        with self.lock:
            case = self.get("cases", case_id)
            if case["state"] != "complete":
                raise ValueError("Capture must complete first")
            prior = self.db.execute("SELECT * FROM analyses WHERE token=?", (token,)).fetchone()
            if prior:
                if any(prior[k] != value[k] for k in ["case_id", "policy", "probe"]):
                    raise ValueError("Token already used for another analysis")
                return prior["id"]
            rows = self.rows("analyses")
            model = [r for r in rows if r["policy"] == "model"]
            if len(rows) >= 160 or (
                value["policy"] == "model"
                and (
                    len(model) >= 10
                    or sum(r["state"] in {"queued", "awaiting"} for r in model) >= 3
                )
            ):
                raise ValueError(
                    "Analysis bound:160 records,10 lifetime model reservations,3pending"
                )
            identifier = str(uuid.uuid4())
            with self.db:
                self.db.execute(
                    """INSERT INTO analyses
                    (id,token,case_id,policy,probe,state,created,evidence_digest)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        identifier,
                        token,
                        case_id,
                        value["policy"],
                        value["probe"],
                        "queued",
                        time.time(),
                        evidence_digest,
                    ),
                )
            return identifier

    def published(self, identifier):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE analyses SET state='awaiting',started=?,published=1 WHERE id=?",
                (time.time(), identifier),
            )

    def finish(
        self, identifier, result=None, error=None, usage=None, model_ms=None, raw_output=None
    ):
        with self.lock, self.db:
            self.db.execute(
                """UPDATE analyses SET state=?,finished=?,result=?,error=?,usage=?,
                model_ms=?,raw_output=?
                WHERE id=? AND state IN ('queued','awaiting')""",
                (
                    "failed" if error is not None else "complete",
                    time.time(),
                    json.dumps(result, allow_nan=False) if result is not None else None,
                    (error or "Analysis failed")[:300] if error is not None else None,
                    json.dumps(usage, allow_nan=False) if usage is not None else None,
                    model_ms,
                    raw_output[:32768] if isinstance(raw_output, str) else None,
                    identifier,
                ),
            )
