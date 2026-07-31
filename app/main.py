"""Faultline local investigator. Capturing experiments is distinct from applying repairs."""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.codec import digest
from app.evidence import Collector, validate_bundle, with_probe
from app.runner import Runner, heartbeat
from app.security import protect
from app.source_pin import SOURCE_SHA
from app.store import Store

ROOT = Path(__file__).resolve().parent


def create_app(db=None, bridge=None, evidence=None, collector=None, *, background=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(db or os.getenv("FAULTLINE_DB", "data/state/faultline.sqlite"))
        app.state.collector = collector or Collector(
            os.getenv("FAULTLINE_SANDBOX", "http://sandbox:8001")
        )
        app.state.runner = Runner(
            app.state.store,
            app.state.collector,
            bridge or os.getenv("FAULTLINE_BRIDGE", "data/bridge"),
            evidence or os.getenv("FAULTLINE_EVIDENCE", "data/evidence"),
        )
        stop = threading.Event()
        thread = threading.Thread(target=app.state.runner.serve, args=(stop,), daemon=True)
        app.state.thread = thread
        if background:
            thread.start()
        yield
        stop.set()
        if thread.is_alive():
            thread.join(timeout=12)
        app.state.collector.close()
        app.state.store.close()

    app = FastAPI(title="Faultline", lifespan=lifespan, docs_url=None, redoc_url=None)
    protect(app)

    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    @app.exception_handler(RecursionError)
    async def invalid(request, exc):
        return JSONResponse({"error": str(exc)[:300] or "Invalid bounded request"}, status_code=400)

    def alive():
        return not background or app.state.thread.is_alive()

    @app.get("/health")
    def health():
        app.state.store.rows("cases")
        if not alive():
            return JSONResponse({"status": "error", "runner_alive": False}, status_code=503)
        return {"status": "ok", "source_sha256": SOURCE_SHA, "runner_alive": alive()}

    @app.get("/api/state")
    def state():
        cases = app.state.store.rows("cases")
        analyses = app.state.store.rows("analyses")
        return {
            "cases": [
                {
                    k: r[k]
                    for k in [
                        "id",
                        "scenario",
                        "state",
                        "created",
                        "captured",
                        "evidence_digest",
                        "error",
                    ]
                }
                for r in cases
            ],
            "analyses": analyses,
            "bridge": heartbeat(app.state.runner.bridge),
            "model_reservations": sum(r["policy"] == "model" for r in analyses),
            "model_cap": 10,
            "source_sha256": SOURCE_SHA,
            "actual_cost_usd": None,
            "repairs_executable": False,
        }

    @app.post("/api/cases", status_code=202)
    async def capture(request: Request):
        if not alive():
            return JSONResponse({"error": "Local capture runner stopped"}, status_code=503)
        return {"id": app.state.store.capture(await request.json())}

    @app.get("/api/cases/{identifier}")
    def case(identifier: str):
        return app.state.store.get("cases", identifier)

    @app.post("/api/analyses", status_code=202)
    async def analyze(request: Request):
        if not alive():
            return JSONResponse({"error": "Local analysis runner stopped"}, status_code=503)
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {"case_id", "policy", "probe", "token"}:
            raise ValueError("Expected case_id, policy, probe, token")
        if value["policy"] == "model" and not heartbeat(app.state.runner.bridge)["available"]:
            return JSONResponse(
                {"error": "Approved cookllm bridge unavailable; no fallback"}, status_code=503
            )
        row = app.state.store.get("cases", value["case_id"])
        if row["state"] != "complete":
            raise ValueError("Wait for a completed evidence capture")
        bundle = with_probe(validate_bundle(row["evidence"]), value["probe"])
        return {"id": app.state.store.analyze(value, digest(bundle))}

    @app.get("/api/analyses/{identifier}")
    def analysis(identifier: str):
        row = app.state.store.get("analyses", identifier)
        row["bundle"] = app.state.runner.bundle(row)
        return row

    @app.get("/api/cases/{identifier}/export")
    def export(identifier: str):
        row = app.state.store.get("cases", identifier)
        analyses = [r for r in app.state.store.rows("analyses") if r["case_id"] == identifier]
        return JSONResponse(
            {
                "case": row,
                "analyses": analyses,
                "requires_human_approval": True,
                "repairs_executable": False,
            },
            headers={"Content-Disposition": 'attachment; filename="faultline-case.json"'},
        )

    # No approval or apply route: a model cannot authorize or execute a repair.
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static/index.html")

    return app


app = create_app()
