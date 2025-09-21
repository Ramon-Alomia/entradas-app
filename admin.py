# admin.py
from __future__ import annotations

import os, json, secrets, string
from typing import Optional, Dict, Any, List

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, jsonify, request, render_template, current_app

from argon2 import PasswordHasher
# Decoradores y helpers del auth híbrido (cookies JWT)
from auth import require_auth, login_required, decode_token

DB_URL = os.getenv("DATABASE_URL")
ph = PasswordHasher()


# -------------------- Helpers DB & Auth --------------------
def _db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg.connect(DB_URL)

def _admin_required():
    """
    Devuelve el payload JWT si el usuario está autenticado y es admin; de lo contrario None.
    Prioriza el usuario ya decodificado por require_auth/login_required en request._user.
    En su defecto, intenta decodificar desde cookie o header.
    """
    user = getattr(request, "_user", None)
    if not user:
        token = request.cookies.get("token") or request.headers.get("Authorization")
        user = decode_token(token)
    if not user or user.get("role") != "admin":
        return None
    return user

def _rand_password(n=14) -> str:
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))


# -------------------- UI (HTML) --------------------
bp_admin_ui = Blueprint("admin_ui", __name__)

@bp_admin_ui.get("/admin")
@login_required  # redirige a /login si no hay cookie/token válido
def admin_page():
    # Sólo sirve la vista. La seguridad real de datos está en los endpoints JSON (abajo).
    return render_template("admin.html")


# -------------------- API JSON (sin /api prefix) --------------------
bp_admin_api = Blueprint("admin_api", __name__, url_prefix="/admin")


# ---------- Users ----------
@bp_admin_api.get("/users")
@require_auth
def list_users():
    if not _admin_required():
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403
    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT u.username, u.role, u.active,
                   COALESCE((
                     SELECT array_agg(whscode) FROM user_warehouses w
                     WHERE w.username = u.username
                   ), '{}') AS warehouses
            FROM users u
            ORDER BY u.username
        """)
        rows = cur.fetchall()
    return jsonify({"data": rows})


@bp_admin_api.post("/users")
@require_auth
def create_user():
    admin = _admin_required()
    if not admin:
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403

    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or _rand_password()
    role     = (body.get("role") or "user").strip()
    active   = bool(body.get("active", True))
    whs_list = body.get("warehouses") or []

    if not username or len(username) > 50:
        return jsonify({"error": {"code": "VALIDATION", "message": "username inválido"}}), 400
    if role not in ("user", "admin"):
        return jsonify({"error": {"code": "VALIDATION", "message": "role inválido"}}), 400

    h = ph.hash(password)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
          INSERT INTO users(username, password, role, active)
          VALUES (%s,%s,%s,%s)
          ON CONFLICT (username) DO NOTHING
        """, (username, h, role, active))
        # Relación de almacenes
        cur.execute("DELETE FROM user_warehouses WHERE username=%s", (username,))
        for w in whs_list:
            cur.execute(
                "INSERT INTO user_warehouses(username, whscode) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (username, w)
            )
        conn.commit()
        # Audit opcional
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log(
                  id bigserial PRIMARY KEY,
                  at timestamptz NOT NULL DEFAULT now(),
                  actor text NOT NULL,
                  action text NOT NULL,
                  target text,
                  details jsonb
                )
            """)
            cur.execute(
                "INSERT INTO audit_log(actor, action, target, details) VALUES (%s,%s,%s,%s)",
                (admin["sub"], "user.create", username, json.dumps({"role": role, "warehouses": whs_list}))
            )
            conn.commit()
        except Exception as e:
            current_app.logger.warning("audit_log fail: %s", e)

    return jsonify({"ok": True, "username": username, "tempPassword": password}), 201


@bp_admin_api.patch("/users/<username>")
@require_auth
def patch_user(username: str):
    admin = _admin_required()
    if not admin:
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403

    body = request.get_json(silent=True) or {}
    updates = []
    params  = []

    if "role" in body:
        role = (body["role"] or "").strip()
        if role not in ("user", "admin"):
            return jsonify({"error": {"code": "VALIDATION", "message": "role inválido"}}), 400
        updates.append("role=%s"); params.append(role)

    if "active" in body:
        updates.append("active=%s"); params.append(bool(body["active"]))

    pw_shown = None
    if "password" in body and body["password"]:
        h = ph.hash(body["password"])
        updates.append("password=%s"); params.append(h)
        pw_shown = body["password"]

    if updates:
        with _db() as conn, conn.cursor() as cur:
            sql = "UPDATE users SET " + ", ".join(updates) + " WHERE username=%s"
            params2 = params + [username]
            cur.execute(sql, params2)
            conn.commit()
            try:
                cur.execute(
                    "INSERT INTO audit_log(actor, action, target, details) VALUES (%s,%s,%s,%s)",
                    (admin["sub"], "user.update", username, json.dumps(body))
                )
                conn.commit()
            except Exception as e:
                current_app.logger.warning("audit_log fail: %s", e)

    # warehouses add/remove
    add = body.get("warehousesAdd") or []
    rem = body.get("warehousesRemove") or []
    if add or rem:
        with _db() as conn, conn.cursor() as cur:
            for w in add:
                cur.execute(
                    "INSERT INTO user_warehouses(username, whscode) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (username, w)
                )
            for w in rem:
                cur.execute(
                    "DELETE FROM user_warehouses WHERE username=%s AND whscode=%s",
                    (username, w)
                )
            conn.commit()

    return jsonify({"ok": True, "password": pw_shown is not None})


@bp_admin_api.delete("/users/<username>")
@require_auth
def delete_user(username: str):
    """
    Borrado físico del usuario:
    - Elimina relaciones en user_warehouses
    - Elimina el registro de users
    Responde 404 si no existe.
    """
    admin = _admin_required()
    if not admin:
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403

    with _db() as conn, conn.cursor() as cur:
        # ¿existe?
        cur.execute("SELECT 1 FROM users WHERE username=%s", (username,))
        if not cur.fetchone():
            return jsonify({"error": {"code": "NOT_FOUND", "message": "Usuario no existe"}}), 404

        # elimina relaciones primero
        cur.execute("DELETE FROM user_warehouses WHERE username=%s", (username,))
        # elimina usuario
        cur.execute("DELETE FROM users WHERE username=%s", (username,))
        conn.commit()
        # audit
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log(
                  id bigserial PRIMARY KEY,
                  at timestamptz NOT NULL DEFAULT now(),
                  actor text NOT NULL,
                  action text NOT NULL,
                  target text,
                  details jsonb
                )
            """)
            cur.execute(
                "INSERT INTO audit_log(actor, action, target, details) VALUES (%s,%s,%s,%s)",
                (admin["sub"], "user.delete", username, json.dumps({}))
            )
            conn.commit()
        except Exception as e:
            current_app.logger.warning("audit_log fail: %s", e)

    return jsonify({"ok": True})


# ---------- Warehouses ----------
@bp_admin_api.get("/warehouses")
@require_auth
def list_warehouses():
    if not _admin_required():
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403
    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT whscode, cardcode, whsdesc FROM warehouses ORDER BY whscode")
        rows = cur.fetchall()
    return jsonify({"data": rows})


@bp_admin_api.post("/warehouses")
@require_auth
def upsert_warehouse():
    if not _admin_required():
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403

    b = request.get_json(silent=True) or {}
    whs = (b.get("whscode") or "").strip()
    cardcode = (b.get("cardcode") or "").strip()
    whsdesc  = (b.get("whsdesc") or "").strip()

    if not whs:
        return jsonify({"error": {"code": "VALIDATION", "message": "whscode requerido"}}), 400

    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO warehouses(whscode, cardcode, whsdesc)
            VALUES (%s,%s,%s)
            ON CONFLICT (whscode) DO UPDATE SET
                cardcode=EXCLUDED.cardcode,
                whsdesc=EXCLUDED.whsdesc
        """, (whs, cardcode, whsdesc))
        conn.commit()
    return jsonify({"ok": True})


@bp_admin_api.delete("/warehouses/<whscode>")
@require_auth
def delete_warehouse(whscode: str):
    """
    Borrado físico del almacén:
    - Elimina relaciones en user_warehouses
    - Elimina el registro en warehouses
    Si existieran FKs en otras tablas, el DELETE podría fallar (se devolverá 409).
    """
    admin = _admin_required()
    if not admin:
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Sólo admin"}}), 403

    whscode = (whscode or "").strip()
    if not whscode:
        return jsonify({"error": {"code": "VALIDATION", "message": "whscode requerido"}}), 400

    try:
        with _db() as conn, conn.cursor() as cur:
            # ¿existe?
            cur.execute("SELECT 1 FROM warehouses WHERE whscode=%s", (whscode,))
            if not cur.fetchone():
                return jsonify({"error": {"code": "NOT_FOUND", "message": "Almacén no existe"}}), 404

            # elimina relaciones primero
            cur.execute("DELETE FROM user_warehouses WHERE whscode=%s", (whscode,))
            # elimina almacén
            cur.execute("DELETE FROM warehouses WHERE whscode=%s", (whscode,))
            conn.commit()
            # audit
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log(
                      id bigserial PRIMARY KEY,
                      at timestamptz NOT NULL DEFAULT now(),
                      actor text NOT NULL,
                      action text NOT NULL,
                      target text,
                      details jsonb
                    )
                """)
                cur.execute(
                    "INSERT INTO audit_log(actor, action, target, details) VALUES (%s,%s,%s,%s)",
                    (admin["sub"], "warehouse.delete", whscode, json.dumps({}))
                )
                conn.commit()
            except Exception as e:
                current_app.logger.warning("audit_log fail: %s", e)

    except Exception as e:
        # Si hubiera FKs (p. ej. receipts_log referenciando whscode) generaríamos 409
        current_app.logger.warning("delete_warehouse fail: %s", e)
        return jsonify({"error": {"code": "CONFLICT", "message": "No se puede eliminar: registros relacionados"}}), 409

    return jsonify({"ok": True})
