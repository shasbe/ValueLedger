"""ValueLedger AttributionService.

The contract every other component is a client of: the collector, the MCP server,
the attribution skill, and the dashboard all write or read through this API.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.db import SessionLocal, init_db
from app.pricing import seed_price_book
from app import mcp, oauth
from app.routers import admin, events, policy, reports, review

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="ValueLedger AttributionService",
    version="0.3.0",
    description="Cost attributed to business work, joined to verifiable output.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(policy.router)
app.include_router(events.router)
app.include_router(reports.router)
app.include_router(review.router)
app.include_router(mcp.router)
app.include_router(oauth.router)


def _bootstrap(db) -> None:
    """Self-heal a demo deployment.

    Cloud Run with SQLite on the container filesystem loses everything on a cold
    start. Rather than make the operator re-seed and re-mint credentials each
    time, a deployment can pin them: if the ledger is empty and BOOTSTRAP_API_KEY
    is set, recreate the demo org with that same key (and the same MCP token), so
    clients configured once keep working across restarts.

    Only ever runs against an empty ledger, so it can never overwrite real data.
    """
    from app.auth import hash_key
    from app.mcp import hash_token
    from app.models import McpToken, Org, User
    from app.seed import seed_into_org

    api_key = os.environ.get("BOOTSTRAP_API_KEY")
    if not api_key:
        return
    if db.query(Org).count():
        return

    org_name = os.environ.get("BOOTSTRAP_ORG_NAME", "Northwind Financial")
    days = int(os.environ.get("BOOTSTRAP_DAYS", "90"))
    # A real deployment wants the org recreated on restart but NOT filled with
    # demo data — its taxonomy comes from config files instead.
    seed_demo = os.environ.get("BOOTSTRAP_SEED_DEMO", "1").lower() in ("1", "true", "yes")
    org = Org(name=org_name, api_key_hash=hash_key(api_key), privacy_mode="local")
    db.add(org)
    db.flush()
    if seed_demo:
        out = seed_into_org(db, org, days=days)
    else:
        from app.pricing import seed_price_book as _spb
        _spb(db)
        admin_email = os.environ.get("BOOTSTRAP_ADMIN",
                                     f"admin@{org_name.lower().replace(' ', '')}.com")
        db.add(User(org_id=org.id, email=admin_email, display_name="Admin",
                    role="admin"))
        out = {"sessions": 0, "total_cost_usd": 0.0}

    mcp_token = os.environ.get("BOOTSTRAP_MCP_TOKEN")
    mcp_email = os.environ.get("BOOTSTRAP_MCP_USER", "cfo@example.com")
    if mcp_token:
        db.add(McpToken(org_id=org.id, user_email=mcp_email,
                        token_hash=hash_token(mcp_token), label="bootstrap"))
    db.commit()
    print(f"[valueledger] bootstrapped {org_name}: {out['sessions']} sessions, "
          f"${out['total_cost_usd']:,.2f}"
          + (f", MCP token for {mcp_email}" if mcp_token else ""))


@app.on_event("startup")
def _startup() -> None:
    init_db()
    db = SessionLocal()
    try:
        added = seed_price_book(db)
        if added:
            print(f"[valueledger] price book seeded: {added} models")
        try:
            _bootstrap(db)
        except Exception as e:  # noqa: BLE001
            # A demo convenience must never take the service down with it.
            db.rollback()
            print(f"[valueledger] bootstrap skipped: {e}")
    finally:
        db.close()


# Google's frontend reserves the exact path /healthz and intercepts it before it
# reaches the container, so /health is the canonical one. /healthz is kept as an
# alias because it still works locally.
# /collector in the container image; ../../collector in a local checkout.
_CANDIDATES = (Path("/collector"),
               Path(__file__).resolve().parent.parent.parent / "collector")
COLLECTOR_DIR = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[-1])


def _service_url(request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}"


@app.get("/install.sh", include_in_schema=False)
def install_script(request: Request):
    """One-line installer, with this deployment's URL baked in.

    The collector has to run where the transcripts are, so something must be
    installed on the machine. This makes that one command instead of a Python
    tutorial — which matters because Cowork users are not all engineers.
    """
    src = COLLECTOR_DIR / "install.sh"
    if not src.exists():
        return PlainTextResponse("installer not bundled", status_code=404)
    body = src.read_text().replace("{{SERVICE_URL}}", _service_url(request))
    return PlainTextResponse(body, media_type="text/x-shellscript")


@app.get("/collector.tar.gz", include_in_schema=False)
def collector_tarball():
    """The collector package, so install.sh has something to fetch."""
    import io
    import tarfile
    if not COLLECTOR_DIR.exists():
        return PlainTextResponse("collector not bundled", status_code=404)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in ("valueledger_collector", "requirements.txt"):
            p = COLLECTOR_DIR / rel
            if p.exists():
                tar.add(p, arcname=rel,
                        filter=lambda ti: None if "__pycache__" in ti.name else ti)
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="application/gzip")


@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "ok", "service": "valueledger", "version": app.version}


if STATIC_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def _root():
        """Landing page — what a reviewer should hit first."""
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/admin", include_in_schema=False)
    def _admin():
        return FileResponse(str(STATIC_DIR / "admin.html"))

    @app.get("/dashboard", include_in_schema=False)
    def _dashboard():
        """The CFO view. Read-only, and the page a finance reader is sent."""
        return FileResponse(str(STATIC_DIR / "dashboard.html"))
