# recepciones_api.py
from __future__ import annotations
import os, hashlib, json, datetime as dt
from typing import Optional, Dict, Any, List

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, request, jsonify

from auth import require_auth, decode_token, user_can_access_whs
from sap_client import SapClient

bp_recepciones = Blueprint("recepciones", __name__, url_prefix="/api")

DB_URL = os.getenv("DATABASE_URL")

def _db():
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    return psycopg.connect(DB_URL)

def _get_user() -> Dict[str, Any]:
    return decode_token(request.headers.get("Authorization")) or {}

def _default_whs_from_user() -> Optional[str]:
    u = _get_user()
    ws = u.get("warehouses") or []
    return ws[0] if ws else None

@bp_recepciones.get("/orders")
@require_auth
def list_orders():
    q = request.args
    due_from   = q.get("due_from")
    due_to     = q.get("due_to")
    vendor     = q.get("vendorCode")
    whs        = q.get("whsCode") or _default_whs_from_user()
    page       = int(q.get("page", 1))
    page_size  = int(q.get("pageSize", 20))

    if not whs:
        return jsonify({"error":{"code":"VALIDATION","message":"No se pudo determinar el almacén del usuario"}}), 400
    if not user_can_access_whs(whs):
        return jsonify({"error":{"code":"FORBIDDEN","message":f"Usuario sin acceso a almacén {whs}"}}), 403

    client = SapClient()
    data = client.get_open_purchase_orders(due_from, due_to, vendor, whs, page, page_size)
    return jsonify(data), 200

@bp_recepciones.get("/orders/<int:doc_entry>")
@require_auth
def get_order(doc_entry: int):
    whs = request.args.get("whsCode") or _default_whs_from_user()
    if not whs:
        return jsonify({"error":{"code":"VALIDATION","message":"No se pudo determinar el almacén del usuario"}}), 400
    if not user_can_access_whs(whs):
        return jsonify({"error":{"code":"FORBIDDEN","message":f"Usuario sin acceso a almacén {whs}"}}), 403

    client = SapClient()
    data = client.get_purchase_order(doc_entry, whs)
    # (opcional) si no hay líneas después de filtrar por whs -> 404 "no hay líneas para ese almacén"
    if not data.get("lines"):
        return jsonify({"error":{"code":"NOT_FOUND","message":"La OC no tiene líneas abiertas para ese almacén"}}), 404
    return jsonify(data), 200

@bp_recepciones.post("/receipts")
@require_auth
def post_receipt():
    body = request.get_json(silent=True) or {}
    doc_entry   = body.get("docEntry")
    whs         = body.get("whsCode") or _default_whs_from_user()
    supplierRef = body.get("supplierRef")
    lines       = body.get("lines") or []

    if not isinstance(doc_entry, int):
        return jsonify({"error":{"code":"VALIDATION","message":"docEntry inválido"}}), 400
    if not whs:
        return jsonify({"error":{"code":"VALIDATION","message":"whsCode requerido"}}), 400
    if not user_can_access_whs(whs):
        return jsonify({"error":{"code":"FORBIDDEN","message":f"Usuario sin acceso a almacén {whs}"}}), 403
    if not lines or not isinstance(lines, list):
        return jsonify({"error":{"code":"VALIDATION","message":"lines es requerido"}}), 400

    # Validación contra OpenQty actual
    client = SapClient()
    detail = client.get_purchase_order(doc_entry, whs)
    open_by_line = { int(l["lineNum"]): float(l["openQty"]) for l in (detail.get("lines") or []) }

    to_post: List[Dict[str, Any]] = []
    for l in lines:
        try:
            ln = int(l["lineNum"]); qty = float(l["quantity"])
        except Exception:
            return jsonify({"error":{"code":"VALIDATION","message":"lines: formato inválido"}}), 400
        if qty < 0:
            return jsonify({"error":{"code":"VALIDATION","message":f"Cantidad negativa en línea {ln}"}}), 400
        max_open = open_by_line.get(ln, 0.0)
        if qty > max_open:
            return jsonify({"error":{"code":"VALIDATION","message":f"Cantidad {qty} > OpenQty {max_open} en línea {ln}"}}), 400
        if qty > 0:
            to_post.append({"lineNum": ln, "quantity": qty})

    if not to_post:
        return jsonify({"error":{"code":"VALIDATION","message":"No hay cantidades > 0"}}), 400

    # Idempotencia simple: hash por usuario + doc + líneas + fecha (día)
    user = _get_user()
    op_str = json.dumps({
        "sub": user.get("sub"),
        "docEntry": doc_entry,
        "whs": whs,
        "lines": sorted(to_post, key=lambda x: x["lineNum"]),
        "date": dt.date.today().isoformat()
    }, separators=(",",":"), sort_keys=True).encode("utf-8")
    op_hash = hashlib.sha256(op_str).hexdigest()

    with _db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT 1 FROM receipts_log WHERE op_hash=%s", (op_hash,))
        if cur.fetchone():
            return jsonify({"error":{"code":"DUPLICATE","message":"Operación ya registrada (idempotencia)"}}), 409

    # Post a SAP
    res = client.post_grpo(doc_entry, whs, to_post, supplierRef)
    grpo_doc = int(res.get("DocEntry", 0))

    # Log
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO receipts_log (po_doc_entry, po_line_num, item_code, whs_code, posted_qty, posted_by, sl_doc_entry, payload_json, op_hash)
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
        conn.commit()

    return jsonify({"grpoDocEntry": grpo_doc, "opHash": op_hash}), 201
