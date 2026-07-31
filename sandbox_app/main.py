"""Private Faultline sandbox: measured local failures only."""

import asyncio
import hashlib
import inspect
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app import source_pin
from app.codec import canonical
from app.io import read_regular
from app.security import BodyLimit

MAX_SOURCE_BYTES = 4 * 1024 * 1024
DB_TIMEOUT_SECONDS = 0.04
DEPENDENCY_URL = "http://127.0.0.1:8001/dependency"
MODES = {
    "normal",
    "sqlite_lock",
    "schema_mismatch",
    "dependency_timeout",
    "slow_aggregate",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _duration_ms(start: float) -> float:
    return max(0.0, (time.perf_counter() - start) * 1000)


def _canonical_uuid(value: str | None) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="invalid capture_id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="invalid capture_id") from exc
    if str(parsed) != value:
        raise HTTPException(status_code=400, detail="invalid capture_id")
    return value


def _source_value(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds byte limit")
    if hashlib.sha256(raw).hexdigest() != source_pin.SOURCE_SHA:
        raise ValueError("source hash mismatch")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid source JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("type") != "FeatureCollection"
        or not isinstance(value.get("metadata"), dict)
        or not isinstance(value.get("features"), list)
    ):
        raise ValueError("invalid source shape")
    return value


class _Trace:
    def __init__(self) -> None:
        self.root_id = str(uuid.uuid4())
        self.root_started = time.perf_counter()
        self.spans: list[dict[str, Any]] = [
            {
                "span_id": self.root_id,
                "parent_id": None,
                "name": "GET /summary",
                "status": "ok",
                "duration_ms": 0.0,
            }
        ]

    def start(self, name: str) -> tuple[dict[str, Any], float]:
        span = {
            "span_id": str(uuid.uuid4()),
            "parent_id": self.root_id,
            "name": name,
            "status": "ok",
            "duration_ms": 0.0,
        }
        self.spans.append(span)
        return span, time.perf_counter()

    @staticmethod
    def finish(span: dict[str, Any], started: float, error: Exception | None = None) -> None:
        span["duration_ms"] = _duration_ms(started)
        if error is not None:
            span["status"] = "error"
            span["error_type"] = type(error).__name__

    def finish_root(self, error: Exception | None = None) -> None:
        root = self.spans[0]
        root["duration_ms"] = _duration_ms(self.root_started)
        if error is not None:
            root["status"] = "error"
            root["error_type"] = type(error).__name__


class _SandboxState:
    def __init__(self, source_path: Path, db_path: Path, dependency: Any) -> None:
        self.source_path = source_path
        self.db_path = db_path
        self.dependency = dependency
        self.mode = "normal"
        self.feature_count = 0
        self.source_sha256 = source_pin.SOURCE_SHA
        self.journal_lock = threading.Lock()

    def _connect(self, timeout: float = DB_TIMEOUT_SECONDS) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=timeout, isolation_level=None)

    def initialize(self) -> None:
        raw = read_regular(self.source_path, MAX_SOURCE_BYTES)
        source = _source_value(raw)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS source_cache ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), blob BLOB NOT NULL, sha256 TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS journal ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, capture_id TEXT NOT NULL, "
                "trace_id TEXT NOT NULL, at TEXT NOT NULL, http_status INTEGER NOT NULL, "
                "duration_ms REAL NOT NULL, log_json TEXT NOT NULL, spans_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS journal_meta ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), dropped_records INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO journal_meta (id, dropped_records) VALUES (1, 0)"
            )
            cached = connection.execute(
                "SELECT blob, sha256 FROM source_cache WHERE id = 1"
            ).fetchone()
            if cached is None:
                connection.execute(
                    "INSERT INTO source_cache (id, blob, sha256) VALUES (1, ?, ?)",
                    (raw, self.source_sha256),
                )
            else:
                blob, saved_sha = cached
                if (
                    saved_sha != self.source_sha256
                    or hashlib.sha256(blob).hexdigest() != self.source_sha256
                    or blob != raw
                ):
                    raise ValueError("persisted source cache mismatch")
        self.feature_count = len(source["features"])

    def read_cached_source(self, mode: str) -> bytes:
        if mode == "sqlite_lock":
            lock_connection = self._connect()
            reader: sqlite3.Connection | None = None
            try:
                lock_connection.execute("PRAGMA journal_mode=DELETE")
                lock_connection.execute("BEGIN EXCLUSIVE")
                reader = self._connect(DB_TIMEOUT_SECONDS)
                row = reader.execute(
                    "SELECT blob, sha256 FROM source_cache WHERE id = 1"
                ).fetchone()
            finally:
                if reader is not None:
                    reader.close()
                try:
                    lock_connection.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                lock_connection.close()
        else:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT blob, sha256 FROM source_cache WHERE id = 1"
                ).fetchone()
        if row is None:
            raise RuntimeError("source cache missing")
        blob, stored_sha = row
        if (
            stored_sha != self.source_sha256
            or hashlib.sha256(blob).hexdigest() != self.source_sha256
        ):
            raise ValueError("source cache hash mismatch")
        return blob

    def append_journal(self, record: dict[str, Any]) -> None:
        with self.journal_lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO journal "
                    "(capture_id, trace_id, at, http_status, duration_ms, log_json, spans_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record["capture_id"],
                        record["trace_id"],
                        record["at"],
                        record["http_status"],
                        record["duration_ms"],
                        canonical(record["log"]),
                        canonical(record["spans"]),
                    ),
                )
                count = connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
                excess = max(0, count - 200)
                if excess:
                    connection.execute(
                        "DELETE FROM journal WHERE sequence IN "
                        "(SELECT sequence FROM journal ORDER BY sequence LIMIT ?)",
                        (excess,),
                    )
                    connection.execute(
                        "UPDATE journal_meta SET dropped_records = dropped_records + ? "
                        "WHERE id = 1",
                        (excess,),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def telemetry(self, capture_id: str) -> tuple[list[dict[str, Any]], int]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT capture_id, trace_id, at, http_status, duration_ms, log_json, spans_json "
                "FROM journal WHERE capture_id = ? ORDER BY sequence LIMIT 200",
                (capture_id,),
            ).fetchall()
            dropped = connection.execute(
                "SELECT dropped_records FROM journal_meta WHERE id = 1"
            ).fetchone()[0]
        records = [
            {
                "capture_id": row[0],
                "trace_id": row[1],
                "at": row[2],
                "http_status": row[3],
                "duration_ms": row[4],
                "log": json.loads(row[5]),
                "spans": json.loads(row[6]),
            }
            for row in rows
        ]
        return records, dropped


def _decode_events(blob: bytes, mode: str) -> list[tuple[dict[str, Any], Any]]:
    value = json.loads(blob)
    features = value["features"]
    decoded: list[tuple[dict[str, Any], Any]] = []
    for feature in features:
        properties = feature["properties"]
        if not isinstance(properties, dict):
            raise ValueError("invalid feature properties")
        magnitude = properties["magnitude"] if mode == "schema_mismatch" else properties.get("mag")
        decoded.append((feature, magnitude))
    return decoded


def _aggregate(events: list[tuple[dict[str, Any], Any]], mode: str) -> dict[str, Any]:
    if mode == "slow_aggregate":
        time.sleep(0.12)
    types: Counter[str] = Counter()
    finite_magnitudes: list[float] = []
    null_magnitudes = 0
    previews: list[dict[str, Any]] = []
    for feature, magnitude in events:
        properties = feature["properties"]
        event_type = properties.get("type")
        types[str(event_type) if event_type is not None else "null"] += 1
        if magnitude is None:
            null_magnitudes += 1
        elif isinstance(magnitude, (int, float)) and not isinstance(magnitude, bool):
            value = float(magnitude)
            if math.isfinite(value):
                finite_magnitudes.append(value)
        if len(previews) < 5:
            previews.append(
                {
                    "id": feature.get("id"),
                    "type": event_type,
                    "title": properties.get("title"),
                    "magnitude": magnitude,
                    "time": properties.get("time"),
                }
            )
    return {
        "event_type_counts": dict(sorted(types.items())),
        "earthquake_records": types.get("earthquake", 0),
        "null_magnitudes": null_magnitudes,
        "min_finite_magnitude": min(finite_magnitudes) if finite_magnitudes else None,
        "max_finite_magnitude": max(finite_magnitudes) if finite_magnitudes else None,
        "previews": previews,
    }


async def _call_dependency(state: _SandboxState) -> None:
    if state.dependency is not None:
        result = state.dependency()
        if inspect.isawaitable(result):
            await result
        return
    timeout = httpx.Timeout(DB_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=timeout
    ) as client:
        response = await client.get(DEPENDENCY_URL)
        response.raise_for_status()


def _error_details(error: Exception) -> tuple[int, str, str]:
    if isinstance(error, httpx.ReadTimeout):
        return 503, "ReadTimeout", "dependency request timed out"
    if isinstance(error, (httpx.HTTPError,)):
        return 503, type(error).__name__, "dependency request failed"
    if isinstance(error, sqlite3.OperationalError):
        message = (
            "database is locked" if "locked" in str(error).lower() else "sqlite operation failed"
        )
        return 500, "OperationalError", message
    if isinstance(error, KeyError):
        return 500, "KeyError", "missing expected source field: " + str(error.args[0])[:80]
    return 500, type(error).__name__, "summary request failed"


def create_app(
    source_path: str | Path | None = None, db_path: str | Path | None = None, dependency: Any = None
) -> FastAPI:
    source = Path(source_path or os.environ.get("SOURCE_PATH", "/source/usgs-all-day.geojson"))
    database = Path(db_path or os.environ.get("DB_PATH", "/state/cache.sqlite"))
    state = _SandboxState(source, database, dependency)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        state.initialize()
        yield

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    application.add_middleware(BodyLimit)
    application.state.faultline = state

    @application.get("/health")
    async def health():
        try:
            state.read_cached_source("normal")
        except (ValueError, RuntimeError, sqlite3.Error):
            return JSONResponse(
                {"status": "error", "error": "Required source cache unavailable"}, status_code=503
            )
        return {
            "status": "ok",
            "source_sha256": state.source_sha256,
            "feature_count": state.feature_count,
        }

    @application.post("/control")
    async def control(request: Request) -> dict[str, Any]:
        body = await request.body()
        if len(body) > 1024:
            raise HTTPException(status_code=400, detail="invalid control")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid control") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"mode"}
            or not isinstance(value["mode"], str)
            or value["mode"] not in MODES
        ):
            raise HTTPException(status_code=400, detail="invalid control")
        state.mode = value["mode"]
        return {"mode": state.mode, "injected": state.mode != "normal"}

    @application.get("/dependency")
    async def local_dependency() -> dict[str, bool]:
        if state.mode == "dependency_timeout":
            await asyncio.sleep(0.15)
        return {"ok": True}

    @application.get("/summary")
    async def summary(request: Request) -> JSONResponse:
        supplied_capture = request.headers.get("X-Capture-ID")
        capture_id = _canonical_uuid(supplied_capture) if supplied_capture else str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        mode = state.mode
        trace = _Trace()
        error: Exception | None = None
        status = 200
        payload: dict[str, Any]
        try:
            dependency_span, started = trace.start("dependency.request")
            try:
                await _call_dependency(state)
            except Exception as exc:
                trace.finish(dependency_span, started, exc)
                raise
            trace.finish(dependency_span, started)

            cache_span, started = trace.start("cache.read")
            try:
                blob = state.read_cached_source(mode)
            except Exception as exc:
                trace.finish(cache_span, started, exc)
                raise
            trace.finish(cache_span, started)

            decode_span, started = trace.start("data.decode")
            try:
                events = _decode_events(blob, mode)
            except Exception as exc:
                trace.finish(decode_span, started, exc)
                raise
            trace.finish(decode_span, started)

            aggregate_span, started = trace.start("aggregate")
            try:
                aggregate = _aggregate(events, mode)
            except Exception as exc:
                trace.finish(aggregate_span, started, exc)
                raise
            trace.finish(aggregate_span, started)
            payload = {
                "dataset_sha256": state.source_sha256,
                "capture_id": capture_id,
                "trace_id": trace_id,
                **aggregate,
            }
        except Exception as exc:
            error = exc
            status, error_type, message = _error_details(exc)
            payload = {"trace_id": trace_id, "error_type": error_type, "message": message}
        finally:
            trace.finish_root(error)
            if error is None:
                log = {
                    "capture_id": capture_id,
                    "trace_id": trace_id,
                    "severity": "INFO",
                    "operation": "GET /summary",
                    "message": "summary completed",
                }
            else:
                _, error_type, message = _error_details(error)
                log = {
                    "capture_id": capture_id,
                    "trace_id": trace_id,
                    "severity": "ERROR",
                    "operation": "GET /summary",
                    "message": f"summary failed: {message}",
                    "error_type": error_type,
                }
            state.append_journal(
                {
                    "capture_id": capture_id,
                    "trace_id": trace_id,
                    "at": _utc_now(),
                    "http_status": status,
                    "duration_ms": trace.spans[0]["duration_ms"],
                    "log": log,
                    "spans": trace.spans,
                }
            )
        return JSONResponse(status_code=status, content=payload)

    @application.get("/telemetry")
    async def telemetry(capture_id: str | None = Query(default=None)) -> dict[str, Any]:
        capture = _canonical_uuid(capture_id)
        records, dropped = state.telemetry(capture)
        return {
            "source_sha256": state.source_sha256,
            "records": records,
            "dropped_records": dropped,
        }

    return application


app = create_app()
