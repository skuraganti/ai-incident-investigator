import hashlib
import json
import time
import uuid

import httpx
import pytest

from app import source_pin
from sandbox_app.main import create_app


@pytest.fixture
def source_file(tmp_path, monkeypatch):
    source = {
        "type": "FeatureCollection",
        "metadata": {"generated": 1},
        "features": [
            {
                "id": "event-1",
                "properties": {"type": "earthquake", "mag": 2.5, "title": "Example", "time": 1},
            },
            {
                "id": "event-2",
                "properties": {"type": "quarry blast", "mag": None, "title": "Other", "time": 2},
            },
        ],
    }
    raw = json.dumps(source, separators=(",", ":")).encode()
    monkeypatch.setattr(source_pin, "SOURCE_SHA", hashlib.sha256(raw).hexdigest())
    path = tmp_path / "source.json"
    path.write_bytes(raw)
    return path


@pytest.fixture
async def client(tmp_path, source_file):
    app = create_app(source_file, tmp_path / "state" / "cache.sqlite", dependency=lambda: None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
            yield value


@pytest.mark.anyio
async def test_summary_measures_spans_and_persists_telemetry(client):
    capture_id = str(uuid.uuid4())
    response = await client.get("/summary", headers={"X-Capture-ID": capture_id})
    assert response.status_code == 200
    body = response.json()
    assert body["earthquake_records"] == 1
    assert body["null_magnitudes"] == 1
    telemetry = (await client.get("/telemetry", params={"capture_id": capture_id})).json()
    record = telemetry["records"][0]
    assert record["http_status"] == 200
    assert len(record["spans"]) == 5
    assert all(span["duration_ms"] >= 0 for span in record["spans"])
    assert "normal" not in json.dumps(record)


@pytest.mark.anyio
async def test_controlled_schema_and_sqlite_faults_are_measured(client):
    capture_id = str(uuid.uuid4())
    assert (await client.post("/control", json={"mode": "schema_mismatch"})).status_code == 200
    response = await client.get("/summary", headers={"X-Capture-ID": capture_id})
    assert response.status_code == 500
    assert response.json()["error_type"] == "KeyError"
    records = (await client.get("/telemetry", params={"capture_id": capture_id})).json()["records"]
    assert records[0]["spans"][-1]["name"] == "data.decode"

    assert (await client.post("/control", json={"mode": "sqlite_lock"})).status_code == 200
    response = await client.get("/summary", headers={"X-Capture-ID": str(uuid.uuid4())})
    assert response.status_code == 500
    assert response.json()["error_type"] == "OperationalError"


@pytest.mark.anyio
async def test_dependency_read_timeout_and_slow_span(tmp_path, source_file):
    async def timeout():
        raise httpx.ReadTimeout("local test timeout")

    app = create_app(source_file, tmp_path / "cache.sqlite", dependency=timeout)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/summary")
            assert response.status_code == 503
            assert response.json()["error_type"] == "ReadTimeout"

    app = create_app(source_file, tmp_path / "slow.sqlite", dependency=lambda: None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/control", json={"mode": "slow_aggregate"})
            capture_id = str(uuid.uuid4())
            started = time.perf_counter()
            assert (
                await client.get("/summary", headers={"X-Capture-ID": capture_id})
            ).status_code == 200
            assert time.perf_counter() - started >= 0.1
            spans = (await client.get("/telemetry", params={"capture_id": capture_id})).json()[
                "records"
            ][0]["spans"]
            assert next(item for item in spans if item["name"] == "aggregate")["duration_ms"] >= 100


@pytest.mark.anyio
async def test_invalid_control_and_capture_id_are_rejected(client):
    assert (
        await client.post("/control", json={"mode": "normal", "extra": True})
    ).status_code == 400
    assert (await client.get("/telemetry", params={"capture_id": "bad"})).status_code == 400


def test_source_or_persisted_cache_mismatch_fails_startup(tmp_path, source_file):
    import asyncio
    import sqlite3

    database = tmp_path / "cache.sqlite"

    async def start_once(app):
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(start_once(create_app(source_file, database, dependency=lambda: None)))
    original = source_file.read_bytes()
    source_file.write_bytes(b'{"type":"FeatureCollection","metadata":{},"features":[]}')
    with pytest.raises(ValueError, match="source hash mismatch"):
        asyncio.run(start_once(create_app(source_file, database, dependency=lambda: None)))
    source_file.write_bytes(original)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE source_cache SET blob=? WHERE id=1", (b"changed",))
    with pytest.raises(ValueError, match="persisted source cache mismatch"):
        asyncio.run(start_once(create_app(source_file, database, dependency=lambda: None)))


@pytest.mark.anyio
async def test_journal_eviction_and_restart_preserve_observed_records(tmp_path, source_file):
    database = tmp_path / "cache.sqlite"
    first_id, last_id = str(uuid.uuid4()), str(uuid.uuid4())
    app = create_app(source_file, database, dependency=lambda: None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            for i in range(201):
                cid = first_id if i == 0 else last_id
                assert (await c.get("/summary", headers={"X-Capture-ID": cid})).status_code == 200
            first = (await c.get("/telemetry", params={"capture_id": first_id})).json()
            last = (await c.get("/telemetry", params={"capture_id": last_id})).json()
            assert first["records"] == [] and last["dropped_records"] == 1
            assert len(last["records"]) == 200
    other = create_app(source_file, database, dependency=lambda: None)
    async with other.router.lifespan_context(other):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=other), base_url="http://test"
        ) as c:
            assert (await c.get("/telemetry", params={"capture_id": last_id})).json() == last


@pytest.mark.anyio
async def test_health_reports_required_cache_corruption(tmp_path, source_file):
    import sqlite3

    database = tmp_path / "cache.sqlite"
    app = create_app(source_file, database, dependency=lambda: None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            assert (await c.get("/health")).status_code == 200
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE source_cache SET blob=? WHERE id=1", (b"changed",))
            assert (await c.get("/health")).status_code == 503
            assert (await c.get("/summary")).status_code == 500
