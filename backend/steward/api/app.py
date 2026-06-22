"""FastAPI application: REST + WebSocket surface over the Steward runtime."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from steward.actions.executor import GuardrailError
from steward.api.schemas import ActionCreate, AskRequest, FlagsUpdate, NLCheckRequest
from steward.checks.schema import Check
from steward.config import Settings, get_settings
from steward.llm.client import build_llm_client
from steward.llm.service import LLMService
from steward.models import ActionRequest
from steward.runtime import Steward

log = logging.getLogger("steward.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    steward = Steward(settings)
    app.state.steward = steward
    app.state.llm = LLMService(build_llm_client(settings))
    await steward.start()
    log.info("Steward ready (proxmox=%s, dry_run=%s, llm=%s)",
             settings.proxmox_mode, steward.is_dry_run(), settings.llm_enabled)
    try:
        yield
    finally:
        await steward.stop()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Feed the in-memory ring buffer that powers the UI log viewer.
    from steward.logbuf import ring_handler

    root = logging.getLogger()
    if ring_handler not in root.handlers:
        root.addHandler(ring_handler)
    # Set the steward logger level explicitly so our records flow regardless of
    # whatever configured the root logger (e.g. pytest's capture, uvicorn).
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger("steward").setLevel(level)
    app = FastAPI(title="Steward", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    # ----- auth -----
    def require_auth(authorization: str = "", x_steward_token: str = "") -> None:
        if not settings.auth_enabled:
            return
        token = ""
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
        token = token or x_steward_token
        if token != settings.auth_token:
            raise HTTPException(status_code=401, detail="unauthorized")

    from fastapi import Header

    def auth_dep(
        authorization: str = Header(default=""),
        x_steward_token: str = Header(default=""),
    ) -> None:
        require_auth(authorization, x_steward_token)

    def get_steward() -> Steward:
        return app.state.steward

    def get_llm() -> LLMService:
        return app.state.llm

    # ------------------------------------------------------------------ #
    # Health & state
    # ------------------------------------------------------------------ #
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/state", dependencies=[Depends(auth_dep)])
    async def state(sw: Steward = Depends(get_steward)):
        snap = sw.latest
        return {
            "snapshot": snap.model_dump(mode="json") if snap else None,
            "flags": sw.flags_dict(),
        }

    @app.get("/api/metrics", dependencies=[Depends(auth_dep)])
    async def metrics(sw: Steward = Depends(get_steward)):
        return sw.latest.model_dump(mode="json") if sw.latest else {}

    @app.get("/api/metrics/series", dependencies=[Depends(auth_dep)])
    async def metric_series(
        kind: str,
        entity: str,
        since: Optional[float] = None,
        limit: int = 2000,
        sw: Steward = Depends(get_steward),
    ):
        return sw.store.metric_series(kind, entity, since=since, limit=limit)

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    @app.get("/api/events", dependencies=[Depends(auth_dep)])
    async def events(
        severity: Optional[str] = None,
        check_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 200,
        sw: Steward = Depends(get_steward),
    ):
        rows = sw.store.list_events(
            severity=severity, check_id=check_id, since=since, limit=limit
        )
        return [e.model_dump(mode="json") for e in rows]

    @app.get("/api/logs", dependencies=[Depends(auth_dep)])
    async def logs(limit: int = 200, level: Optional[str] = None):
        from steward.logbuf import ring_handler

        return ring_handler.tail(limit=limit, level=level)

    # ------------------------------------------------------------------ #
    # Checks CRUD
    # ------------------------------------------------------------------ #
    @app.get("/api/checks", dependencies=[Depends(auth_dep)])
    async def list_checks(sw: Steward = Depends(get_steward)):
        return [c.model_dump(mode="json") for c in sw.store.list_checks()]

    @app.post("/api/checks", dependencies=[Depends(auth_dep)])
    async def create_check(check: Check, sw: Steward = Depends(get_steward)):
        sw.store.upsert_check(check)
        return check.model_dump(mode="json")

    @app.get("/api/checksets/export", dependencies=[Depends(auth_dep)])
    async def export_checks(sw: Steward = Depends(get_steward)):
        return {"version": 1, "checks": [c.model_dump(mode="json") for c in sw.store.list_checks()]}

    @app.post("/api/checksets/import", dependencies=[Depends(auth_dep)])
    async def import_checks(payload: dict, sw: Steward = Depends(get_steward)):
        raw = payload.get("checks", payload if isinstance(payload, list) else [])
        imported, errors = 0, []
        for item in raw:
            try:
                chk = Check.model_validate(item)
                sw.store.upsert_check(chk)
                imported += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        return {"imported": imported, "errors": errors}

    @app.get("/api/checks/{check_id}", dependencies=[Depends(auth_dep)])
    async def get_check(check_id: str, sw: Steward = Depends(get_steward)):
        chk = sw.store.get_check(check_id)
        if not chk:
            raise HTTPException(404, "no such check")
        return chk.model_dump(mode="json")

    @app.put("/api/checks/{check_id}", dependencies=[Depends(auth_dep)])
    async def update_check(check_id: str, check: Check, sw: Steward = Depends(get_steward)):
        if check.id != check_id:
            raise HTTPException(400, "id mismatch")
        if not sw.store.get_check(check_id):
            raise HTTPException(404, "no such check")
        sw.store.upsert_check(check)
        return check.model_dump(mode="json")

    @app.post("/api/checks/{check_id}/toggle", dependencies=[Depends(auth_dep)])
    async def toggle_check(check_id: str, sw: Steward = Depends(get_steward)):
        chk = sw.store.get_check(check_id)
        if not chk:
            raise HTTPException(404, "no such check")
        chk.enabled = not chk.enabled
        sw.store.upsert_check(chk)
        return chk.model_dump(mode="json")

    @app.delete("/api/checks/{check_id}", dependencies=[Depends(auth_dep)])
    async def delete_check(check_id: str, sw: Steward = Depends(get_steward)):
        if not sw.store.delete_check(check_id):
            raise HTTPException(404, "no such check")
        return {"deleted": check_id}

    # ------------------------------------------------------------------ #
    # Actions & approval queue
    # ------------------------------------------------------------------ #
    @app.get("/api/actions", dependencies=[Depends(auth_dep)])
    async def list_actions(
        status: Optional[str] = None, limit: int = 200, sw: Steward = Depends(get_steward)
    ):
        return [a.model_dump(mode="json") for a in sw.store.list_actions(status=status, limit=limit)]

    @app.post("/api/actions", dependencies=[Depends(auth_dep)])
    async def create_action(body: ActionCreate, sw: Steward = Depends(get_steward)):
        req = ActionRequest(
            type=body.type, params=body.params, reason=body.reason, source="manual",
            auto_execute=(body.mode == "run"),
        )
        if body.mode == "run":
            rec = await sw.executor.run(req, approved_by_human=True)
        else:
            rec = sw.executor.propose(req)
        return rec.model_dump(mode="json")

    @app.post("/api/actions/{action_id}/approve", dependencies=[Depends(auth_dep)])
    async def approve_action(action_id: int, sw: Steward = Depends(get_steward)):
        try:
            rec = await sw.executor.approve(action_id)
        except GuardrailError as exc:
            raise HTTPException(400, str(exc))
        return rec.model_dump(mode="json")

    @app.post("/api/actions/{action_id}/reject", dependencies=[Depends(auth_dep)])
    async def reject_action(action_id: int, sw: Steward = Depends(get_steward)):
        try:
            rec = sw.executor.reject(action_id)
        except GuardrailError as exc:
            raise HTTPException(400, str(exc))
        return rec.model_dump(mode="json")

    # ------------------------------------------------------------------ #
    # Flags / kill switch
    # ------------------------------------------------------------------ #
    @app.get("/api/flags", dependencies=[Depends(auth_dep)])
    async def get_flags(sw: Steward = Depends(get_steward)):
        return sw.flags_dict()

    @app.post("/api/flags", dependencies=[Depends(auth_dep)])
    async def set_flags(body: FlagsUpdate, sw: Steward = Depends(get_steward)):
        if body.paused is not None:
            sw.set_paused(body.paused)
        if body.dry_run is not None:
            sw.set_dry_run(body.dry_run)
        if body.allowlist is not None:
            sw.set_allowlist(body.allowlist)
        return sw.flags_dict()

    # ------------------------------------------------------------------ #
    # LLM features (degrade gracefully when not configured)
    # ------------------------------------------------------------------ #
    @app.get("/api/llm/status", dependencies=[Depends(auth_dep)])
    async def llm_status(llm: LLMService = Depends(get_llm)):
        return {"enabled": llm.enabled, "model": app.state.settings.llm_model}

    @app.post("/api/llm/check", dependencies=[Depends(auth_dep)])
    async def llm_check(
        body: NLCheckRequest,
        sw: Steward = Depends(get_steward),
        llm: LLMService = Depends(get_llm),
    ):
        if not llm.enabled:
            raise HTTPException(503, "LLM not configured")
        try:
            check = await llm.nl_to_check(body.request)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"could not parse a valid check: {exc}")
        sw.store.upsert_check(check)  # stored DISABLED for human review
        return check.model_dump(mode="json")

    @app.post("/api/llm/ask", dependencies=[Depends(auth_dep)])
    async def llm_ask(
        body: AskRequest,
        sw: Steward = Depends(get_steward),
        llm: LLMService = Depends(get_llm),
    ):
        if not llm.enabled:
            raise HTTPException(503, "LLM not configured")
        events = sw.store.list_events(limit=15)
        answer = await llm.answer(body.question, sw.latest, events)
        return {"answer": answer}

    @app.post("/api/llm/explain/{event_id}", dependencies=[Depends(auth_dep)])
    async def llm_explain(
        event_id: int,
        sw: Steward = Depends(get_steward),
        llm: LLMService = Depends(get_llm),
    ):
        if not llm.enabled:
            raise HTTPException(503, "LLM not configured")
        events = sw.store.list_events(limit=500)
        event = next((e for e in events if e.id == event_id), None)
        if not event:
            raise HTTPException(404, "no such event")
        return {"answer": await llm.explain(event, sw.latest)}

    # ------------------------------------------------------------------ #
    # Demo / load injection (mock client only)
    # ------------------------------------------------------------------ #
    @app.post("/api/demo/inject", dependencies=[Depends(auth_dep)])
    async def demo_inject(
        vmid: int,
        cpu_pct: Optional[float] = None,
        mem_pct: Optional[float] = None,
        sw: Steward = Depends(get_steward),
    ):
        from steward.proxmox.mock import MockProxmoxClient

        if not isinstance(sw.client, MockProxmoxClient):
            raise HTTPException(400, "load injection only available with the mock client")
        sw.client.inject_load(vmid=vmid, cpu_pct=cpu_pct, mem_pct=mem_pct)
        return {"injected": {"vmid": vmid, "cpu_pct": cpu_pct, "mem_pct": mem_pct}}

    @app.post("/api/demo/clear", dependencies=[Depends(auth_dep)])
    async def demo_clear(vmid: Optional[int] = None, sw: Steward = Depends(get_steward)):
        from steward.proxmox.mock import MockProxmoxClient

        if not isinstance(sw.client, MockProxmoxClient):
            raise HTTPException(400, "only available with the mock client")
        sw.client.clear_load(vmid)
        return {"cleared": vmid or "all"}

    # ------------------------------------------------------------------ #
    # WebSocket: live ticks
    # ------------------------------------------------------------------ #
    @app.websocket("/ws")
    async def ws(websocket: WebSocket, token: str = Query(default="")):
        if settings.auth_enabled and token != settings.auth_token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        sw: Steward = app.state.steward
        q = sw.subscribe()
        # send an immediate snapshot so the UI paints without waiting a cycle
        if sw.latest:
            await websocket.send_json({
                "type": "tick",
                "snapshot": sw.latest.model_dump(mode="json"),
                "events": [],
                "flags": sw.flags_dict(),
            })
        try:
            while True:
                payload = await q.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            sw.unsubscribe(q)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA if present (frontend/dist). Dev uses the Vite proxy."""
    here = os.path.dirname(os.path.abspath(__file__))
    dist = os.path.abspath(os.path.join(here, "..", "..", "..", "frontend", "dist"))
    if not os.path.isdir(dist):
        @app.get("/")
        async def no_ui():
            return {"detail": "UI not built. Run `npm run build` in frontend/ or use the dev server."}
        return

    app.mount("/assets", StaticFiles(directory=os.path.join(dist, "assets")), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(dist, "index.html"))

    @app.get("/{path:path}")
    async def spa(path: str):
        candidate = os.path.join(dist, path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(dist, "index.html"))
