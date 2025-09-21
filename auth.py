from __future__ import annotations

import os
import datetime as dt
from typing import Optional, Dict, Any
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, request, jsonify, current_app, redirect, make_response, render_template

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
import jwt

# --- Config ---
DB_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG = "HS256"

# Nombre único para el blueprint de autenticación (sin prefijo '/api')
bp_auth = Blueprint("auth", __name__)

# Hasher Argon2id
ph = PasswordHasher()


def _db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no está configurada")
    return psycopg.connect(DB_URL)


def _make_token(payload: Dict[str, Any]) -> str:
    now = dt.datetime.utcnow()
    to_encode = {
        "sub": payload["username"],
        "role": payload["role"],
        "warehouses": payload.get("warehouses", []),
        "iat": int(now.timestamp()),
        "nbf": int((now - dt.timedelta(seconds=60)).timestamp()),
        "exp": int((now + dt.timedelta(hours=8)).timestamp()),
        "iss": "recepciones-api",
    }
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    # Acepta token en formato "Bearer <token>" o token plano
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    try:
        data = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALG],
            options={"verify_exp": True, "verify_nbf": True, "verify_iat": False},
            leeway=120,
        )
        return data
    except jwt.PyJWTError as e:
        current_app.logger.warning("JWT error: %s", e)
        return None


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not JWT_SECRET:
            return jsonify({"error": {"code": "CONFIG", "message": "Falta JWT_SECRET"}}), 500
        # Verificar cookie primero, luego header Authorization
        token = request.cookies.get("token") or request.headers.get("Authorization")
        user = decode_token(token)
        if not user:
            return jsonify({"error": {"code": "UNAUTHORIZED", "message": "Token inválido o ausente"}}), 401
        request._user = user
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.cookies.get("token")
        user = decode_token(token)
        if not user:
            return redirect("/login")
        request._user = user
        return fn(*args, **kwargs)
    return wrapper


@bp_auth.get("/login")
def login_form():
    token = request.cookies.get("token")
    if token and decode_token(token):
        # Ya autenticado, redirigir a admin
        return redirect("/admin")
    return render_template("login.html")


@bp_auth.post("/login")
def login():
    if not DB_URL or not JWT_SECRET:
        return jsonify({"error": {"code": "CONFIG", "message": "Falta DATABASE_URL o JWT_SECRET"}}), 500

    # Obtener credenciales de JSON o formulario
    if request.is_json:
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
    else:
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

    if not username or not password:
        return jsonify({"error": {"code": "VALIDATION", "message": "username y password son requeridos"}}), 400

    # Validar contra DB
    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT username, password AS hashed, role, active "
            "FROM users WHERE username=%s",
            (username,),
        )
        u = cur.fetchone()
        if not u or not u.get("active", True):
            return jsonify({"error": {"code": "INVALID_CREDENTIALS", "message": "Credenciales inválidas"}}), 401

        try:
            ph.verify(u["hashed"], password)
        except (VerifyMismatchError, InvalidHashError):
            return jsonify({"error": {"code": "INVALID_CREDENTIALS", "message": "Credenciales inválidas"}}), 401

        # Obtener almacenes del usuario
        cur.execute("SELECT whscode FROM user_warehouses WHERE username=%s", (username,))
        whs = [r["whscode"] for r in cur.fetchall()]

    token = _make_token({"username": username, "role": u["role"], "warehouses": whs})

    # Set JWT en cookie HttpOnly
    resp = make_response(redirect("/admin"))
    secure = current_app.config.get("SESSION_COOKIE_SECURE", True)
    http_only = current_app.config.get("SESSION_COOKIE_HTTPONLY", True)
    same_site = current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax")
    resp.set_cookie("token", token, httponly=http_only, secure=secure, samesite=same_site)
    return resp


@bp_auth.get("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("token")
    return resp
