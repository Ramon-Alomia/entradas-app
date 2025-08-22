# recepciones_api.py
import os
import json
import hashlib
from datetime import date
from typing import Optional

from flask import Blueprint, request, jsonify
from pydantic import BaseModel, Field, ValidationError, conint, confloat

import psycopg
from psycopg.rows import dict_row

from sap_client import SAPClient

bp_recepciones = Blueprint("recepciones_api", __name__)

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL no está configurado")

def get_db():
    # Tu cadena Neon ya trae sslmode=require
    return psycopg.connect(DB_URL)

# --------- Schemas ----------
class OrdersQuery(BaseModel):
    due_from: Optional[str] = None  # YYYY-MM-DD
    due_to:   Optional[str] = None
    vendorCode: Optional[str] = None
    whsCode:   Optional[str] = None  # usado para filtrar líneas en detalle (opcional)
    page: conint(ge=1) = 1
    pageSize: conint(ge=1, le=100) = 20

class ReceiptLineIn(BaseModel):
    lineNum: int = Field(ge=0)
    quantity: confloat(ge=0)

class ReceiptIn(BaseModel):
    docEntry: int
    whsCode: str
    # Pydantic v2: usa list[...] + Field(min_length=1)
    lines: list[ReceiptLineIn] = Field(min_length=1)
    supplierRef: Optional[str] = None

# --------- Helpers ----------
def whoami() -> str:
    # Mientras no tenemos auth, usa header opcional X-User o "api"
    return (request.headers.get("X-User") or "api").strip() or "api"

def op_hash(user: str, payload: dict) -> str:
    # Idempotencia por día (ajusta si quieres otra ventana)
    raw = json.dumps({"user": user, **payload, "date": str(date.today())}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

# --------- Endpoints ----------
@bp_recepciones.get("/api/orders")
def list_orders():
    try:
        q = OrdersQuery(**request.args.to_dict())
    except ValidationError as e:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(e)}}), 400

    client = SAPClient()
    data = client.get_open_purchase_orders(
        due_from=q.due_from, due_to=q.due_to, vendor=q.vendorCode,
        page=q.page, page_size=q.pageSize
    )
    out = []
    for it in data["value"]:
        out.append({
            "docEntry": it.get("DocEntry"),
            "docNum": it.get("DocNum"),
            "vendorCode": it.get("CardCode"),
            "vendorName": it.get("CardName"),
            "docDueDate": it.get("DocDueDate"),
            "openLines": None,       # cálculo fino en detalle
            "totalOpenQty": None
        })
    return jsonify({"page": q.page, "pageSize": q.pageSize, "total": len(out), "data": out}), 200

@bp_recepciones.get("/api/orders/<int:doc_entry>")
def order_detail(doc_entry: int):
    # Filtrado opcional por whsCode via query
    whs_filter = request.args.get("whsCode")
    client = SAPClient()
    po = client.get_purchase_order(doc_entry)
    lines = []
    for ln in po["Lines"]:
        if whs_filter and ln["WarehouseCode"] != whs_filter:
            continue
        lines.append({
            "lineNum": ln["LineNum"],
            "itemCode": ln["ItemCode"],
            "description": ln["ItemDescription"],
            "warehouseCode": ln["WarehouseCode"],
            "orderedQty": ln["OrderedQty"],
            "receivedQty": ln["ReceivedQty"],
            "openQty": ln["OpenQty"],
        })
    return jsonify({
        "docEntry": po["DocEntry"],
        "docNum": po["DocNum"],
        "vendorCode": po["CardCode"],
        "vendorName": po["CardName"],
        "docDueDate": po["DocDueDate"],
        "lines": lines
    }), 200

@bp_recepciones.post("/api/receipts")
def post_receipt():
    try:
        payload = ReceiptIn(**request.get_json(force=True))
    except ValidationError as e:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(e)}}), 400

    user = whoami()

    # Revalida openQty en tiempo real contra SL
    client = SAPClient()
    po = client.get_purchase_order(payload.docEntry)
    # Solo líneas del almacén indicado
    open_by_line = {
        int(ln["LineNum"]): float(ln["OpenQty"])
        for ln in po["Lines"] if ln["WarehouseCode"] == payload.whsCode
    }

    for ln in payload.lines:
        if ln.lineNum not in open_by_line:
            return jsonify({"error":{
                "code":"VALIDATION_ERROR",
                "message": f"Línea {ln.lineNum} no pertenece al almacén {payload.whsCode} o no existe"
            }}), 409
        if ln.quantity > open_by_line[ln.lineNum]:
            return jsonify({"error":{
                "code":"VALIDATION_ERROR",
                "message":"quantity exceeds openQty",
                "details":{"lineNum": ln.lineNum, "quantity": ln.quantity, "openQty": open_by_line[ln.lineNum]}
            }}), 409

    # Idempotencia por bloque (docEntry + líneas + whs + user + fecha)
    oph = op_hash(user, payload.model_dump())

    # Verifica idempotencia y registra log tras crear GRPO
    with get_db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT 1 FROM receipts_log WHERE op_hash=%s", (oph,))
        if cur.fetchone():
            return jsonify({"error":{
                "code":"IDEMPOTENT_REPLAY",
                "message":"Este payload ya fue procesado hoy.",
                "details":{"opHash": oph}
            }}), 409

        # Crear GRPO en SL
        sl = client.post_grpo(
            doc_entry=payload.docEntry,
            whs_code=payload.whsCode,
            lines=[{"lineNum": l.lineNum, "quantity": l.quantity} for l in payload.lines],
            supplier_ref=payload.supplierRef
        )
        grpo_de = sl.get("DocEntry")

        # Log por línea
        for l in payload.lines:
            cur.execute("""
                INSERT INTO receipts_log
                  (po_doc_entry, po_line_num, item_code, whs_code, posted_qty, posted_by, sl_doc_entry, payload_json, op_hash, created_at)
                VALUES
                  (%s, %s, NULL, %s, %s, %s, %s, %s::jsonb, %s, NOW())
            """, (payload.docEntry, l.lineNum, payload.whsCode, l.quantity, user, grpo_de, json.dumps(sl), oph))
        conn.commit()

    return jsonify({
        "grpoDocEntry": grpo_de,
        "opHash": oph,
        "lines": [{"lineNum": l.lineNum, "postedQty": l.quantity} for l in payload.lines]
    }), 201