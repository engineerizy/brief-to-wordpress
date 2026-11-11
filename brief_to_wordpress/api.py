from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .integrations import IntegrationError, Settings, demo_plan, fixture, load_design, read_catalog
from .models import ApproveRun, CreateRun, DeliverRun
from .render import wordpress_blocks
from .store import MissingRun, StateConflict, Store


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    destination = settings.wordpress.base_url if settings.wordpress else "Local sample draft store"
    planner_name = "OpenAI" if settings.planner else "Sample planner"
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=False, timeout=httpx.Timeout(20, connect=8),
            trust_env=settings.planner is not None and transport is None,
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(title="Brief to WordPress", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "terminal.local", "testserver"]
    )
    app.mount("/assets", StaticFiles(directory=static), name="assets")

    @app.middleware("http")
    async def require_same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Use the workflow console on the same origin."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(MissingRun)
    async def missing_run(_request, _error):
        return JSONResponse({"detail": "Run not found."}, status_code=404)

    @app.exception_handler(StateConflict)
    async def state_conflict(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/settings")
    def settings_summary():
        return {
            "planner": planner_name, "destination": destination,
            "supports_figma": bool(settings.figma_token), "sample_delivery": settings.wordpress is None,
            "example": fixture("brief.json"),
        }

    @app.get("/api/runs")
    def recent():
        return store.recent()

    @app.post("/api/runs", status_code=201)
    async def create(request: Request, data: CreateRun):
        events = []

        def record(kind, detail):
            events.append((kind, detail))

        client = request.app.state.client
        try:
            design = await load_design(data.design, settings.figma_token, client)
            record("design_loaded", f"Read {len(design['sections'])} sections from {design['source']}.")
            if settings.planner:
                plan = await settings.planner.plan(client, data.brief, design, record)
            else:
                catalog = await read_catalog()
                record("catalog_read", "Read the component library from a local MCP server over stdio.")
                plan = demo_plan(data.brief, catalog)
                record("plan_received", "The sample planner assembled a page using the brief and approved components.")
            blocks = wordpress_blocks(plan, data.brief)
            record("validated", "Checked the component order and generated editable Gutenberg blocks.")
        except IntegrationError as exc:
            raise HTTPException(502, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(502, "The planner returned content outside the approved page schema.") from exc
        except (httpx.HTTPError, TimeoutError, OSError, ExceptionGroup, ValueError) as exc:
            raise HTTPException(502, "The design or planning service could not complete the request.") from exc
        return store.create(
            data.brief.model_dump(), design, plan.model_dump(), blocks, destination, planner_name, events
        )

    @app.get("/api/runs/{run_id}")
    def get(run_id: str):
        return store.get(run_id)

    @app.get("/api/runs/{run_id}/blocks", response_class=PlainTextResponse)
    def blocks(run_id: str):
        return store.get(run_id)["blocks"]

    @app.post("/api/runs/{run_id}/approve")
    def approve(run_id: str, data: ApproveRun):
        store.approve(run_id, data.revision)
        return store.get(run_id)

    @app.post("/api/runs/{run_id}/deliver")
    async def deliver(request: Request, run_id: str, data: DeliverRun):
        if data.simulate_lost_reply and settings.wordpress:
            raise HTTPException(400, "The lost-reply simulation requires the local sample destination.")
        if not store.claim_delivery(run_id, destination):
            return store.get(run_id)
        run = store.get(run_id)
        try:
            if settings.wordpress:
                draft = await settings.wordpress.create(request.app.state.client, run)
            else:
                draft = store.save_sample_draft(run)
                if data.simulate_lost_reply:
                    store.needs_check(run_id, "The sample draft was written and its reply was lost in the simulation.")
                    return store.get(run_id)
        except (IntegrationError, httpx.HTTPError, KeyError, TypeError, ValueError):
            store.needs_check(run_id, "The destination did not confirm the draft. Look it up by slug.")
        else:
            store.delivered(run_id, draft, recovered=False)
        return store.get(run_id)

    @app.post("/api/runs/{run_id}/reconcile")
    async def reconcile(request: Request, run_id: str):
        run = store.get(run_id)
        if run["state"] == "draft_created":
            return run
        if run["state"] not in {"sending", "needs_check"} or run["destination"] != destination:
            raise StateConflict("This run does not have a pending delivery at this destination.")
        try:
            if settings.wordpress:
                draft = await settings.wordpress.find(request.app.state.client, run)
            else:
                draft = store.find_sample_draft(run)
        except (IntegrationError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(502, "The destination lookup could not confirm a matching draft.") from exc
        if draft:
            store.delivered(run_id, draft, recovered=True)
        else:
            store.note(run_id, "lookup_empty", "No matching draft was found. Check the destination before starting a new run.")
        return store.get(run_id)

    @app.get("/api/sample-drafts/{draft_id}")
    def sample_draft(draft_id: int):
        draft = store.sample_draft(draft_id)
        if draft is None:
            raise HTTPException(404, "Sample draft not found.")
        return draft

    return app
