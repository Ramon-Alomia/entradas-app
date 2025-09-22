# auth.py
from __future__ import annotations

import os
import time
from functools import wraps
from typing import Optional, Dict, Any, List

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, request, jsonify, redirect, render_template
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
import jwt  # PyJWT

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
DB_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ISSUER = os.getenv("JWT_ISSUER", "recepciones-api")
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "8"))

if not DB_URL:
    raise RuntimeError("DATABASE_URL no configurada")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET no configurada")

ph = PasswordHasher()

def _db():
    return psycopg.connect(DB_URL)


def _set_jwt_cookie(resp, token: str):
    """Configura la cookie HttpOnly donde viaja el JWT."""
    secure_cookie = bool(os.getenv("RENDER")) or request.is_secure
    resp.set_cookie(
        "token",
        token,
        httponly=True,
        secure=secure_cookie,
        samesite="Lax",
        max_age=JWT_TTL_HOURS * 3600,
        path="/",
    )
    return resp


def _clear_jwt_cookie(resp):
    resp.delete_cookie("token", path="/")
    return resp

# -----------------------------------------------------------------------------
# Blueprint
# -----------------------------------------------------------------------------
# Blueprint sin prefijo: las rutas quedan /login, /logout, /me
bp_auth = Blueprint("auth", __name__)

# -----------------------------------------------------------------------------
# Utils JWT
# -----------------------------------------------------------------------------
def _bearer_token_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    auth = authorization.strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth  # si llega el token "pelado"

def decode_token(authorization_header: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decodifica el JWT del header Authorization. Retorna dict o None."""
    token = _bearer_token_from_header(authorization_header)
    if not token:
        return None
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "iat"]})
        return data  # {"sub","role","warehouses",...}
    except jwt.PyJWTError:
        return None

def _make_token(username: str, role: str, warehouses: List[str]) -> str:
    now = int(time.time())
    exp = now + JWT_TTL_HOURS * 3600
    payload = {
        "iss": JWT_ISSUER,
        "sub": username,
        "role": role,
        "warehouses": warehouses,
        "iat": now,
        "nbf": now - 60,
        "exp": exp,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def _load_user_from_request(req) -> Optional[Dict[str, Any]]:
    if req is None:
        return None
    user = getattr(req, "_user", None)
    if user:
        return user
    auth_header = None
    try:
        auth_header = req.headers.get("Authorization")
    except Exception:
        auth_header = None
    user = decode_token(auth_header)
    if user:
        return user
    # Cookie HttpOnly "token"
    try:
        cookie_token = req.cookies.get("token")
    except Exception:
        cookie_token = None
    if cookie_token:
        user = decode_token(f"Bearer {cookie_token}")
        if user:
            return user
    return None


def require_auth(fn):
    """Decorator para proteger endpoints con JWT. Inyecta request._user"""

    @wraps(fn)
    def _wrap(*args, **kwargs):
        user = _load_user_from_request(request)
        if not user:
            return jsonify({"error": {"code": "UNAUTHORIZED", "message": "Token inválido o ausente"}}), 401
        request._user = user
        return fn(*args, **kwargs)

    return _wrap


def login_required(fn):
    """Protege vistas HTML que dependen de la cookie/token JWT."""

    @wraps(fn)
    def _wrap(*args, **kwargs):
        user = _load_user_from_request(request)
        if not user:
            return redirect("/login")
        request._user = user
        return fn(*args, **kwargs)

    return _wrap


def user_can_access_whs(whs: str, request_or_header: Optional[Any] = None) -> bool:
    """Usado por recepciones_api.py para validar acceso al almacén."""
    if not whs:
        return False

    if request_or_header is None:
        user = _load_user_from_request(request)
    elif hasattr(request_or_header, "headers"):
        user = _load_user_from_request(request_or_header)
    elif isinstance(request_or_header, str):
        user = decode_token(request_or_header)
    else:
        user = None

    if not user:
        return False
    return whs in (user.get("warehouses") or [])

# -----------------------------------------------------------------------------
# Login / Logout / Me
# -----------------------------------------------------------------------------
@bp_auth.get("/login")
def login_page():
    """Renderiza el formulario de inicio de sesión."""
    if _load_user_from_request(request):
        return redirect("/")
    return render_template("login.html")


@bp_auth.post("/login")
def login():
    """
    Body JSON: { "username":"...", "password":"..." }
    Respuesta: { username, role, warehouses, token }
    """
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return jsonify({"error": {"code": "VALIDATION", "message": "username y password son requeridos"}}), 400

    # 1) Buscar usuario
    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT username, password, role, active FROM users WHERE username=%s", (username,))
        row = cur.fetchone()

    if not row or not row["active"]:
        return jsonify({"error": {"code": "AUTH", "message": "Credenciales inválidas o usuario inactivo"}}), 401

    stored = row["password"] or ""
    ok = False
    try:
        # Caso 1: es hash Argon2
        if stored.startswith("$argon2"):
            ph.verify(stored, password)
            ok = True
            # rehash si cambian parámetros
            if ph.check_needs_rehash(stored):
                new_hash = ph.hash(password)
                with _db() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE users SET password=%s WHERE username=%s", (new_hash, username))
                    conn.commit()
        else:
            # Caso 2: guardado plano (temporal) -> aceptamos una vez y re-hasheamos
            if stored == password:
                ok = True
                new_hash = ph.hash(password)
                with _db() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE users SET password=%s WHERE username=%s", (new_hash, username))
                    conn.commit()
    except (VerifyMismatchError, InvalidHashError):
        ok = False

    if not ok:
        return jsonify({"error": {"code": "AUTH", "message": "Credenciales inválidas"}}), 401

    # 2) Warehouses
    with _db() as conn, conn.cursor() as cur:
        cur.execute("SELECT whscode FROM user_warehouses WHERE username=%s", (username,))
        whs_rows = [r[0] for r in cur.fetchall()]

    role = row["role"] or "user"
    token = _make_token(username, role, whs_rows)

    response = jsonify({
        "username": username,
        "role": role,
        "warehouses": whs_rows,
        "token": token
    })
    _set_jwt_cookie(response, token)
    response.status_code = 200
    return response

@bp_auth.get("/me")
@require_auth
def me():
    """Devuelve las claims del JWT (útil para debug/UI)."""
    return jsonify({"user": getattr(request, "_user", {})})

@bp_auth.get("/logout")
def logout_page():
    resp = redirect("/login")
    return _clear_jwt_cookie(resp)


@bp_auth.post("/logout")
def logout():
    """
    No hay invalidación server-side con JWT. El cliente debe borrar el token.
    Se responde 200 por conveniencia.
    """
    resp = jsonify({"ok": True})
    _clear_jwt_cookie(resp)
    resp.status_code = 200
    return resp
