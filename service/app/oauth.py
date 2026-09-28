"""OAuth 2.1 for the MCP server — what claude.ai needs to connect.

Claude Desktop can be handed a static bearer token through a config file.
claude.ai cannot: a Custom Connector has nowhere to put one, so the only way to
reach the chat surface is the MCP authorization spec — RFC 9728 discovery,
RFC 7591 dynamic client registration, and an authorization-code flow with PKCE.

The security property from SPEC §8 is unchanged and is in fact strengthened:
**identity is decided once, by a human, on the consent screen**, and burned into
the token. Nothing the model sends afterwards can change who a record is
attributed to.

Consent is gated on the org API key. Without that, an open registration endpoint
plus an open consent screen would let anyone on the internet mint a token for any
user in the org.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import hash_key
from app.db import get_db
from app.mcp import hash_token, new_mcp_token
from app.models import McpToken, OAuthClient, OAuthCode, Org, User

router = APIRouter(tags=["oauth"])

CODE_TTL_SECONDS = 300


def base_url(request: Request) -> str:
    # Cloud Run terminates TLS upstream, so trust the forwarded proto.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}"


# --------------------------------------------------------------------------
# Discovery (RFC 9728 / RFC 8414)
# --------------------------------------------------------------------------

@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    b = base_url(request)
    return {
        "resource": f"{b}/mcp",
        "authorization_servers": [b],
        "scopes_supported": ["valueledger.read", "valueledger.write"],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server(request: Request):
    b = base_url(request)
    return {
        "issuer": b,
        "authorization_endpoint": f"{b}/oauth/authorize",
        "token_endpoint": f"{b}/oauth/token",
        "registration_endpoint": f"{b}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["valueledger.read", "valueledger.write"],
    }


# --------------------------------------------------------------------------
# Dynamic client registration (RFC 7591)
# --------------------------------------------------------------------------

@router.post("/oauth/register")
async def register(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    redirect_uris = body.get("redirect_uris") or []
    if not redirect_uris:
        return JSONResponse({"error": "invalid_redirect_uri",
                             "error_description": "redirect_uris is required"},
                            status_code=400)
    client_id = "vlc_" + secrets.token_urlsafe(24)
    db.add(OAuthClient(client_id=client_id,
                       client_name=body.get("client_name", "unknown"),
                       redirect_uris=json.dumps(redirect_uris)))
    db.commit()
    return JSONResponse({
        "client_id": client_id,
        "client_name": body.get("client_name", "unknown"),
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, status_code=201)


# --------------------------------------------------------------------------
# Authorization — the consent screen
# --------------------------------------------------------------------------

_CONSENT_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize ValueLedger</title>
<style>
:root{{--bg:#fbfbfa;--surface:#fff;--border:#e4e4e2;--text:#1c1b1a;--dim:#6b6a67;
--accent:#b8562f;--danger:#a3372a}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1a1918;--surface:#232221;--border:#35342f;
--text:#edecea;--dim:#a5a39e;--accent:#e08a5f;--danger:#e08070}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;display:flex;align-items:center;
justify-content:center;min-height:100vh;padding:16px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;
padding:26px;max-width:440px;width:100%}}
h1{{font-size:17px;margin:0 0 4px;letter-spacing:-.01em}}
h1 span{{color:var(--accent)}}
p.sub{{color:var(--dim);font-size:13px;margin:0 0 20px}}
label{{display:block;font-size:12px;color:var(--dim);margin:14px 0 4px;font-weight:520}}
input{{width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:6px;
background:var(--bg);color:var(--text);font-size:13px;font-family:inherit}}
input:focus{{outline:2px solid var(--accent);outline-offset:-1px}}
button{{width:100%;margin-top:20px;padding:10px;background:var(--accent);color:#fff;
border:none;border-radius:6px;font-size:14px;font-weight:560;cursor:pointer;
font-family:inherit}}
button:hover{{filter:brightness(1.08)}}
.client{{background:var(--bg);border:1px solid var(--border);border-radius:6px;
padding:10px 12px;font-size:12.5px;color:var(--dim);margin-bottom:6px}}
.client b{{color:var(--text)}}
.err{{color:var(--danger);font-size:13px;border:1px solid var(--danger);
border-radius:6px;padding:9px 11px;margin-bottom:14px}}
.note{{font-size:11.5px;color:var(--dim);margin-top:16px;line-height:1.5}}
</style></head><body>
<div class="card">
  <h1>Authorize <span>ValueLedger</span></h1>
  <p class="sub">Grant an application access to your attribution ledger.</p>
  {error}
  <div class="client"><b>{client_name}</b> is requesting access.</div>
  <form method="POST" action="/oauth/authorize">
    <input type="hidden" name="client_id" value="{client_id}">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="code_challenge" value="{code_challenge}">
    <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
    <label>Organization API key</label>
    <input name="api_key" type="password" required placeholder="vl_..."
           value="{prefill_key}" autocomplete="off">
    <label>Act as</label>
    <input name="user_email" type="text" required placeholder="cfo@example.com"
           value="{prefill_email}" autocomplete="off">
    <button type="submit">Authorize</button>
  </form>
  <p class="note">{demo_note}The identity you choose here is bound into the issued
  token. Everything published later is attributed to it, regardless of what the
  application sends.</p>
</div></body></html>"""


def _demo_prefill(db: Session | None = None) -> tuple[str, str, str]:
    """On a public demo instance, prefill the credentials so a tester is not
    blocked at the door. Never active without DEMO_MODE.

    The identity offered is the org's own `member` account, resolved from the
    database rather than an environment variable. An env var drifts the moment
    the org is rebuilt, and a consent screen that suggests a different account
    from the one the instructions name is worse than no suggestion at all.
    """
    import os
    if os.environ.get("DEMO_MODE", "").lower() not in ("1", "true", "yes"):
        return "", "", ""
    key = os.environ.get("BOOTSTRAP_API_KEY", "")
    email = ""
    if db is not None and key:
        org = db.query(Org).filter(Org.api_key_hash == hash_key(key)).first()
        if org:
            u = (db.query(User)
                   .filter(User.org_id == org.id, User.role == "member")
                   .order_by(User.email).first())
            if u:
                email = u.email
    if not email:
        email = os.environ.get("BOOTSTRAP_MCP_USER", "")
    note = ("<b>Demo instance</b> &mdash; the shared test credentials are filled in "
            "for you. Press Authorize, or replace <em>Act as</em> with your own "
            "label first.<br><br>")
    return key, email, note


def _render(error: str = "", db: Session | None = None, **kw) -> HTMLResponse:
    k, e, n = _demo_prefill(db)
    kw.setdefault("prefill_key", k)
    kw.setdefault("prefill_email", e)
    kw.setdefault("demo_note", n)
    return HTMLResponse(_CONSENT_PAGE.format(
        error=f'<div class="err">{error}</div>' if error else "", **kw))


@router.get("/oauth/authorize")
def authorize_form(request: Request, client_id: str = "", redirect_uri: str = "",
                   state: str = "", code_challenge: str = "",
                   code_challenge_method: str = "S256", db: Session = Depends(get_db)):
    client = db.query(OAuthClient).filter(OAuthClient.client_id == client_id).first()
    if not client:
        return HTMLResponse("<h3>Unknown client_id</h3>", status_code=400)
    if redirect_uri not in json.loads(client.redirect_uris):
        return HTMLResponse("<h3>redirect_uri does not match this client</h3>",
                            status_code=400)
    return _render(db=db, client_name=client.client_name or "An application",
                   client_id=client_id, redirect_uri=redirect_uri, state=state,
                   code_challenge=code_challenge,
                   code_challenge_method=code_challenge_method)


@router.post("/oauth/authorize")
def authorize_submit(client_id: str = Form(...), redirect_uri: str = Form(...),
                     state: str = Form(""), code_challenge: str = Form(""),
                     code_challenge_method: str = Form("S256"),
                     api_key: str = Form(...), user_email: str = Form(...),
                     db: Session = Depends(get_db)):
    client = db.query(OAuthClient).filter(OAuthClient.client_id == client_id).first()
    if not client or redirect_uri not in json.loads(client.redirect_uris):
        return HTMLResponse("<h3>Invalid client or redirect_uri</h3>", status_code=400)

    def fail(msg: str):
        return _render(error=msg, db=db,
                       client_name=client.client_name or "An application",
                       client_id=client_id, redirect_uri=redirect_uri, state=state,
                       code_challenge=code_challenge,
                       code_challenge_method=code_challenge_method)

    org = db.query(Org).filter(Org.api_key_hash == hash_key(api_key.strip())).first()
    if not org:
        return fail("That organization API key was not recognised.")
    email = user_email.lower().strip()
    if not db.query(User).filter(User.org_id == org.id, User.email == email).first():
        return fail(f"No user '{email}' exists in {org.name}.")

    code = secrets.token_urlsafe(32)
    db.add(OAuthCode(
        code=code, client_id=client_id, redirect_uri=redirect_uri,
        code_challenge=code_challenge, code_challenge_method=code_challenge_method,
        org_id=org.id, user_email=email,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS)))
    db.commit()

    sep = "&" if "?" in redirect_uri else "?"
    target = f"{redirect_uri}{sep}code={code}"
    if state:
        target += f"&state={state}"
    return RedirectResponse(target, status_code=302)


# --------------------------------------------------------------------------
# Token exchange
# --------------------------------------------------------------------------

def _pkce_ok(verifier: str, challenge: str, method: str) -> bool:
    if not challenge:
        return True                      # client sent no challenge
    if method == "plain":
        return verifier == challenge
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=") == challenge


@router.post("/oauth/token")
def token(grant_type: str = Form(...), code: str = Form(""),
          redirect_uri: str = Form(""), client_id: str = Form(""),
          code_verifier: str = Form(""), db: Session = Depends(get_db)):
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    row = db.query(OAuthCode).filter(OAuthCode.code == code).first()
    if not row or row.used:
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "Code unknown or already used"},
                            status_code=400)
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "Code expired"}, status_code=400)
    if row.client_id != client_id or row.redirect_uri != redirect_uri:
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "client_id/redirect_uri mismatch"},
                            status_code=400)
    if not _pkce_ok(code_verifier, row.code_challenge, row.code_challenge_method):
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "PKCE verification failed"},
                            status_code=400)

    raw = new_mcp_token()
    db.add(McpToken(org_id=row.org_id, user_email=row.user_email,
                    token_hash=hash_token(raw),
                    label=f"oauth:{(row.client_id or '')[:12]}"))
    row.used = True                       # single use
    db.commit()

    return JSONResponse({
        "access_token": raw,
        "token_type": "Bearer",
        "scope": "valueledger.read valueledger.write",
    })
