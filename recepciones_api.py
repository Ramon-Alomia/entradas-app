# recepciones_api.py
from __future__ import annotations

import os
import json
import hashlib
import datetime as dt
from typing import Optional, Dict, Any, List

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, request, jsonify, current_app

from auth import require_auth, decode_token, user_can_access_whs
from sap_client import SapClient

# SIN prefijo /api: las rutas serán /orders, /orders/<docEntry>, /receipts
bp_recepciones = Blueprint("recepciones", __name__)

DB_URL = os.getenv("DATABASE_URL")


def _db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg.connect(DB_URL)


def _get_user() -> Dict[str, Any]:
    """
    Recupera el usuario desde:
      - request._user (inyectado por @require_auth)
      - o decodifica el header/cookie si no está (fallback defensivo)
    """
    u = getattr(request, "_user", None)
    if u:
        return u
    # Fallbacks defensivos (no deberían ser necesarios si @require_auth está activo):
    authz = request.headers.get("Authorization") or ""
    cu = decode_token(authz)
    if cu:
        return cu
    # Intenta cookie 'token'
    try:
        token = request.cookies.get("token")
        if token:
            cu = decode_token(f"Bearer {token}")
            if cu:
                return cu
    except Exception:
        pass
    return {}


def _default_whs_from_user() -> Optional[str]:
    u = _get_user()
    ws = u.get("warehouses") or []
    return ws[0] if ws else None


@bp_recepciones.get("/orders")
@require_auth
def list_orders():
    """
    Lista POs abiertos filtrando por:
      - due_from, due_to (YYYY-MM-DD)
      - vendorCode (CardCode del proveedor)
      - whsCode (si no se envía, se toma el primer almacén del token)
      - page, pageSize (paginación; por defecto 1 y 20)
    Además se fuerza el filtrado por almacén del usuario (autorización).
    """
    q = request.args
    due_from = q.get("due_from")
    due_to = q.get("due_to")
    vendor = q.get("vendorCode")
    whs = q.get("whsCode") or _default_whs_from_user()
    page = int(q.get("page", 1))
    page_size = int(q.get("pageSize", 20))

    if not whs:
        return jsonify({"error": {"code": "VALIDATION", "message": "No se pudo determinar el almacén del usuario"}}), 400
    if not user_can_access_whs(whs):
        return jsonify({"error": {"code": "FORBIDDEN", "message": f"Usuario sin acceso a almacén {whs}"}}), 403

    client = SapClient()
    data = client.get_open_purchase_orders(
        due_from=due_from, due_to=due_to, vendor=vendor, whs=whs, page=page, page_size=page_size
    )
    # data esperado: {"data":[...], "total":N}
    return jsonify(data), 200


@bp_recepciones.get("/orders/<int:doc_entry>")
@require_auth
def get_order(doc_entry: int):
    """
    Detalle de una OC (filtrado por whsCode). Si no hay líneas abiertas para ese almacén → 404.
    """
    whs = request.args.get("whsCode") or _default_whs_from_user()
    if not whs:
        return jsonify({"error": {"code": "VALIDATION", "message": "No se pudo determinar el almacén del usuario"}}), 400
    if not user_can_access_whs(whs):
        return jsonify({"error": {"code": "FORBIDDEN", "message": f"Usuario sin acceso a almacén {whs}"}}), 403

    client = SapClient()
    data = client.get_purchase_order(doc_entry, whs)
    if not data.get("lines"):
        return jsonify({"error": {"code": "NOT_FOUND", "message": "La OC no tiene líneas abiertas para ese almacén"}}), 404
    return jsonify(data), 200


@bp_recepciones.post("/receipts")
@require_auth
def post_receipt():
    """
    Crea un GRPO (PurchaseDeliveryNote) parcial o total.
    Payload:
      {
        "docEntry": 104624,
        "whsCode": "HTLSDS",                 // opcional: se toma del token si no viene
        "supplierRef": "REM-123",            // opcional
        "lines": [ { "lineNum": 0, "quantity": 5 }, ... ]
      }

    Reglas:
      - 0 <= qty <= openQty (validado contra SL justo antes de postear)
      - idempotencia por usuario+doc+líneas+fecha (op_hash)
      - log completo en receipts_log
    """
    body = request.get_json(silent=True) or {}
    doc_entry = body.get("docEntry")
    whs = body.get("whsCode") or _default_whs_from_user()
    supplierRef = body.get("supplierRef")
    lines = body.get("lines") or []

    if not isinstance(doc_entry, int):
        return jsonify({"error": {"code": "VALIDATION", "message": "docEntry inválido"}}), 400
    if not whs:
        return jsonify({"error": {"code": "VALIDATION", "message": "whsCode requerido"}}), 400
    if not user_can_access_whs(whs):
        return jsonify({"error": {"code": "FORBIDDEN", "message": f"Usuario sin acceso a almacén {whs}"}}), 403
    if not lines or not isinstance(lines, list):
        return jsonify({"error": {"code": "VALIDATION", "message": "lines es requerido"}}), 400

    # Validación contra OpenQty actual
    client = SapClient()
    detail = client.get_purchase_order(doc_entry, whs)
    open_by_line = {int(l["lineNum"]): float(l["openQty"]) for l in (detail.get("lines") or [])}

    to_post: List[Dict[str, Any]] = []
    for l in lines:
        try:
            ln = int(l["lineNum"])
            qty = float(l["quantity"])
        except Exception:
            return jsonify({"error": {"code": "VALIDATION", "message": "lines: formato inválido"}}), 400

        if qty < 0:
            return jsonify({"error": {"code": "VALIDATION", "message": f"Cantidad negativa en línea {ln}"}}), 400

        max_open = open_by_line.get(ln, 0.0)
        if qty > max_open:
            return jsonify({
                "error": {
                    "code": "VALIDATION",
                    "message": f"Cantidad {qty} > OpenQty {max_open} en línea {ln}"
                }
            }), 400

        if qty > 0:
            to_post.append({"lineNum": ln, "quantity": qty})

    if not to_post:
        return jsonify({"error": {"code": "VALIDATION", "message": "No hay cantidades > 0"}}), 400

    # Idempotencia simple:
    # hash por usuario + doc + whs + líneas + fecha (día) + supplierRef (si viene)
    user = _get_user()
    op_payload = {
        "sub": user.get("sub"),
        "docEntry": doc_entry,
        "whs": whs,
        "lines": sorted(to_post, key=lambda x: x["lineNum"]),
        "date": dt.date.today().isoformat()
    }
    if supplierRef:
        op_payload["supplierRef"] = supplierRef

    op_hash = hashlib.sha256(
        json.dumps(op_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()

    # Evita duplicados por idempotencia
    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute("SELECT 1 FROM receipts_log WHERE op_hash=%s", (op_hash,))
            if cur.fetchone():
                return jsonify({"error": {"code": "DUPLICATE", "message": "Operación ya registrada (idempotencia)"}}), 409
        except psycopg.Error:
            # Si no existe la columna op_hash, no rompemos: continuamos sin idempotencia en DB
            current_app.logger.warning("receipts_log.op_hash no existe; idempotencia solo en memoria/hash.")
            pass

    # Post a SAP
    res = client.post_grpo(doc_entry, whs, to_post, supplierRef)
    grpo_doc = int(res.get("DocEntry", 0))

    # Log
    with _db() as conn, conn.cursor() as cur:
        # Insert mínimo; si tu tabla tiene más columnas/constraints, ajusta aquí.
        try:
            cur.execute(
                """
                INSERT INTO receipts_log (
                  po_doc_entry, po_line_num, item_code, whs_code,
                  posted_qty, posted_by, sl_doc_entry, payload_json, op_hash
                )
                VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s, %s)
                """,
                (
                    doc_entry,
                    whs,
                    sum(x["quantity"] for x in to_post),
                    user.get("sub"),
                    grpo_doc,
                    json.dumps(res)[:65000],
                    op_hash
                )
            )
        except psycopg.Error:
            # Tabla sin op_hash → insertar sin esa columna
            cur.execute(
                """
                INSERT INTO receipts_log (
                  po_doc_entry, po_line_num, item_code, whs_code,
                  posted_qty, posted_by, sl_doc_entry, payload_json
                )
                VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s)
                """,
                (
                    doc_entry,
                    whs,
                    sum(x["quantity"] for x in to_post),
                    user.get("sub"),
                    grpo_doc,
                    json.dumps(res)[:65000],
                )
            )
        conn.commit()

    return jsonify({"grpoDocEntry": grpo_doc, "opHash": op_hash}), 201
