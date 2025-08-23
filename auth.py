# auth.py
import os
import datetime as dt
from typing import Optional, Dict, Any

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, request, jsonify, current_app
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
import jwt

bp_auth = Blueprint("auth", __name__)
DB_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG = "HS256"
ph = PasswordHasher()

def _db():
    return psycopg.connect(DB_URL)

def _make_token(payload: Dict[str, Any]) -> str:
    now = dt.datetime.utcnow()
    to_encode = {
        "sub": payload["username"],
        "role": payload["role"],
        "warehouses": payload["warehouses"],
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(hours=8)).timestamp()),
        "iss": "recepciones-api"
    }
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALG)

@bp_auth.post("/api/login")
def login():
    if not DB_URL or not JWT_SECRET:
        return jsonify({"error": {"code": "CONFIG", "message": "Falta DATABASE_URL o JWT_SECRET"}}), 500

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")

    if not username or not password:
        return jsonify({"error": {"code": "VALIDATION", "message": "username y password son requeridos"}}), 400

    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT username, password AS hashed, role, active FROM users WHERE username=%s", (username,))
        u = cur.fetchone()
        if not u or not u.get("active", True):
            return jsonify({"error": {"code": "INVALID_CREDENTIALS", "message": "Credenciales inválidas"}}), 401

        try:
            ph.verify(u["hashed"], password)
        except (VerifyMismatchError, InvalidHashError):
            return jsonify({"error": {"code": "INVALID_CREDENTIALS", "message": "Credenciales inválidas"}}), 401

        cur.execute("SELECT whscode FROM user_warehouses WHERE username=%s", (username,))
        whs = [r["whscode"] for r in cur.fetchall()]

    token = _make_token({"username": username, "role": u["role"], "warehouses": whs})
    return jsonify({"token": token, "username": username, "role": u["role"], "warehouses": whs}), 200

# ------- Helpers para proteger endpoints -------

def decode_token(auth_header: Optional[str]) -> Optional[Dict[str, Any]]:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    try:
        # Desactiva verificación estricta de iat y agrega leeway por desfase de reloj
        data = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALG],
            options={"verify_exp": True, "verify_nbf": True, "verify_iat": False},
            leeway=120  # 2 min de tolerancia
        )
        return data
    except jwt.PyJWTError as e:
        current_app.logger.warning("JWT error: %s", e)
        return None

def require_auth(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = decode_token(request.headers.get("Authorization"))
        if not user:
            return jsonify({"error": {"code": "UNAUTHORIZED", "message": "Token inválido o ausente"}}), 401
        request._user = user
        return fn(*args, **kwargs)
    return wrapper

def user_can_access_whs(whs: Optional[str]) -> bool:
    if not whs:
        return True
    user = getattr(request, "_user", None) or decode_token(request.headers.get("Authorization"))
    if not user:
        return False
    return whs in (user.get("warehouses") or [])
