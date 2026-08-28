"""Optionale Login-Schicht für den Server-Betrieb.

Ohne APP_PASSWORD / APP_PASSWORD_HASH läuft alles offen (lokaler Dev-Modus).
Setzt man eine der beiden Variablen, schützt eine Session-Cookie-Authentifizierung
die gesamte App (einfacher Einzelzugang – das Tool ist bewusst kein Multi-User-System,
es gibt ja keine personenbezogenen Daten zu trennen).

Passwort-Hashes (empfohlen für den Server) erzeugt man einmalig:
    python -c "import os,hashlib; s=os.urandom(16).hex(); \
print('APP_PASSWORD_HASH=scrypt$16384$8$1$'+s+'$'+hashlib.scrypt(b'MEIN_PASSWORT',salt=bytes.fromhex(s),n=16384,r=8,p=1).hex())"
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from .config import DATA_DIR

COOKIE_NAME = "mbf_session"
SESSION_TTL_S = 12 * 3600  # 12 h
_OPEN_PATHS = ("/login.html", "/api/login", "/api/logout", "/api/health")


class LoginBody(BaseModel):
    """Achtung: muss auf Modulebene leben – innerhalb einer Funktion definierte
    Modelle kann FastAPI (bei PEP-563-Annotationen) nicht auflösen."""
    username: str = ""
    password: str = ""


# ---------- Konfiguration (dynamisch gelesen → testbar per monkeypatch) ----------

def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v else None


def auth_enabled() -> bool:
    return bool(_env("APP_PASSWORD") or _env("APP_PASSWORD_HASH"))


def _secret() -> bytes:
    own = _env("APP_SECRET")
    if own:
        return own.encode()
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        key_file.write_bytes(secrets.token_hex(32).encode())
    return key_file.read_bytes()


# ---------- Passwort-Prüfung ----------

def verify_password(username: str, password: str) -> bool:
    if username != (_env("APP_USER") or "admin"):
        return False
    h = _env("APP_PASSWORD_HASH")
    if h:
        try:
            algo, n, r, p, salt_hex, hash_hex = h.split("$")
            if algo != "scrypt":
                return False
            hash_bytes = bytes.fromhex(hash_hex)
            # dklen aus dem gespeicherten Hash ableichen – so funktionieren
            # sowohl 32-Byte- als auch Standard-64-Byte-Hashes
            derived = hashlib.scrypt(
                password.encode(), salt=bytes.fromhex(salt_hex),
                n=int(n), r=int(r), p=int(p), dklen=len(hash_bytes))
            return hmac.compare_digest(derived, hash_bytes)
        except (ValueError, KeyError):
            return False
    ref = _env("APP_PASSWORD") or ""
    return hmac.compare_digest(password.encode(), ref.encode())


# ---------- Signierte Session-Tokens (HMAC-SHA256) ----------

def _sign(payload: bytes) -> str:
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def create_session(username: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"u": username, "exp": int(time.time()) + SESSION_TTL_S}).encode()).decode()
    return payload + "." + _sign(payload.encode())


def read_session(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    payload_b64, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64.encode()), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, TypeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return str(data.get("u"))


def current_user(request: Request) -> str | None:
    return read_session(request.cookies.get(COOKIE_NAME))


# ---------- Endpunkte (werden in main.py eingebunden) ----------

def register_auth(app) -> None:
    @app.get("/api/health")
    def health():
        return {"ok": True, "auth": auth_enabled()}

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        if not auth_enabled():
            raise HTTPException(400, "Auth ist nicht aktiviert (kein APP_PASSWORD gesetzt)")
        if not verify_password(body.username, body.password):
            raise HTTPException(401, "Zugangsdaten falsch")
        response.set_cookie(
            COOKIE_NAME, create_session(body.username),
            max_age=SESSION_TTL_S, httponly=True, samesite="lax", path="/")
        return {"status": "ok"}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"status": "ok"}


# ---------- Middleware ----------

async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if auth_enabled() and not path.startswith(_OPEN_PATHS):
        user = current_user(request)
        if not user:
            if request.method in ("GET", "HEAD") and not path.startswith("/api/"):
                return RedirectResponse("/login.html", status_code=303)
            return JSONResponse({"detail": "Nicht eingeloggt"}, status_code=401)
    return await call_next(request)
