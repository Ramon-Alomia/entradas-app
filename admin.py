# admin.py
from __future__ import annotations
import os, json, secrets, string
from typing import Optional, Dict, Any, List
import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, jsonify, request, render_template, current_app
from argon2 import PasswordHasher
from auth import require_auth, decode_token  # ya existente

DB_URL = os.getenv("DATABASE_URL")
ph = PasswordHasher()

def _db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg.connect(DB_URL)

def _admin_required():
    user = getattr(request, "_user", None) or decode_token(request.headers.get("Authorization"))
    if not user or user.get("role") != "admin":
        return None
    return user

def _rand_password(n=14) -> str:
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))

# ---------------- UI ----------------
bp_admin_ui = Blueprint("admin_ui", __name__)

@bp_admin_ui.get("/admin")
def admin_page():
    # La UI sólo sirve HTML; la seguridad real es del API /api/admin (JWT admin)
    return render_template("admin.html")

# ---------------- API ----------------
bp_admin_api = Blueprint("admin_api", __name__, url_prefix="/api/admin")

@bp_admin_api.get("/users")
@require_auth
def list_users():
    if not _admin_required():
        return jsonify({"error":{"code":"FORBIDDEN","message":"Sólo admin"}}), 403
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
        return jsonify({"error":{"code":"FORBIDDEN","message":"Sólo admin"}}), 403
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or _rand_password()
    role     = (body.get("role") or "user").strip()
    active   = bool(body.get("active", True))
    whs_list = body.get("warehouses") or []

    if not username or len(username) > 50:
        return jsonify({"error":{"code":"VALIDATION","message":"username inválido"}}), 400
    if role not in ("user","admin"):
        return jsonify({"error":{"code":"VALIDATION","message":"role inválido"}}), 400

    h = ph.hash(password)
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
          INSERT INTO users(username, password, role, active)
          VALUES (%s,%s,%s,%s)
          ON CONFLICT (username) DO NOTHING
        """, (username, h, role, active))
        cur.execute("DELETE FROM user_warehouses WHERE username=%s", (username,))
        for w in whs_list:
            cur.execute("INSERT INTO user_warehouses(username, whscode) VALUES (%s,%s) ON CONFLICT DO NOTHING", (username, w))
        conn.commit()
        # audit opcional
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log(
                  id bigserial PRIMARY KEY,
                  at timestamptz NOT NULL DEFAULT now(),
                  actor text NOT NULL,
                  action text NOT NULL,
                  target text,
                  details jsonb
                )""")
            cur.execute("INSERT INTO audit_log(actor, action, target, details) VALUES (%s,%s,%s,%s)",
                        (admin["sub"], "user.create", username, json.dumps({"role":role,"warehouses":whs_list})))
            conn.commit()
        except Exception as e:
            current_app.logger.warning("audit_log fail: %s", e)

    return jsonify({"ok": True, "username": username, "tempPassword": password}), 201

@bp_admin_api.patch("/users/<username>")
@require_auth
def patch_user(username: str):
    admin = _admin_required()
    if not admin:
        return jsonify({"error":{"code":"FORBIDDEN","message":"Sólo admin"}}), 403
    body = request.get_json(silent=True) or {}
    updates = []
    params  = []

    if "role" in body:
        role = body["role"]
        if role not in ("user","admin"):
            return jsonify({"error":{"code":"VALIDATION","message":"role inválido"}}), 400
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
                cur.execute("INSERT INTO audit_log(actor, action, target, details) VALUES (%s,%s,%s,%s)",
                            (admin["sub"], "user.update", username, json.dumps(body)))
                conn.commit()
            except Exception as e:
                current_app.logger.warning("audit_log fail: %s", e)

    # warehouses add/remove
    add = body.get("warehousesAdd") or []
    rem = body.get("warehousesRemove") or []
    if add or rem:
        with _db() as conn, conn.cursor() as cur:
            for w in add:
                cur.execute("INSERT INTO user_warehouses(username, whscode) VALUES (%s,%s) ON CONFLICT DO NOTHING", (username, w))
            for w in rem:
                cur.execute("DELETE FROM user_warehouses WHERE username=%s AND whscode=%s", (username, w))
            conn.commit()

    return jsonify({"ok": True, "password": pw_shown is not None})

@bp_admin_api.get("/warehouses")
@require_auth
def list_warehouses():
    if not _admin_required():
        return jsonify({"error":{"code":"FORBIDDEN","message":"Sólo admin"}}), 403
    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT whscode, cardcode, whsdesc FROM warehouses ORDER BY whscode")
        rows = cur.fetchall()
    return jsonify({"data": rows})

@bp_admin_api.post("/warehouses")
@require_auth
def upsert_warehouse():
    if not _admin_required():
        return jsonify({"error":{"code":"FORBIDDEN","message":"Sólo admin"}}), 403
    b = request.get_json(silent=True) or {}
    whs = (b.get("whscode") or "").strip()
    cardcode = (b.get("cardcode") or "").strip()
    whsdesc  = (b.get("whsdesc") or "").strip()
    if not whs:
        return jsonify({"error":{"code":"VALIDATION","message":"whscode requerido"}}), 400
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
