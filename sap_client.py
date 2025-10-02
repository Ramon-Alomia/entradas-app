# sap_client.py
from __future__ import annotations

import os
import time
import random
import logging
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

import requests
from requests import Response
from requests.exceptions import SSLError, ConnectionError, Timeout, HTTPError

log = logging.getLogger("sap_client")

# ----------------------------------------------------------------------
# Config de entorno
# ----------------------------------------------------------------------
BASE_URL = (
    os.getenv("SAP_SL_BASE_URL")
    or os.getenv("SERVICE_LAYER_URL")
    or "https://hwvdvsbo04.virtualdv.cloud:50000/b1s/v1"
).rstrip("/")

COMPANY_DB  = os.getenv("COMPANY_DB")
SL_USER     = os.getenv("SL_USER")
SL_PASSWORD = os.getenv("SL_PASSWORD")

# TLS bundle (local/Render)
CA_BUNDLE  = os.getenv("SAP_SL_CA_BUNDLE") or os.getenv("REQUESTS_CA_BUNDLE")
VERIFY_SSL = True
if os.getenv("SAP_SL_VERIFY_SSL", "true").lower() in ("0", "false", "no", "off"):
    VERIFY_SSL = False


def _cookie_path_for_base(base: str) -> str:
    """
    SAP recomienda fijar el path del cookie al prefijo del Service Layer.
    Detectamos si la base apunta a /b1s/v1 o /b1s/v2.
    Por omisión usamos /b1s/v1.
    """
    try:
        path = urlparse(base).path or "/b1s/v1"
        if "/b1s/v2" in path:
            return "/b1s/v2"
    except Exception:
        pass
    return "/b1s/v1"


class SapClient:
    _DATE_FORMATS = ("datetimeoffset", "datetime", "iso")
    _preferred_date_format: Optional[str] = None

    def __init__(self) -> None:
        if not BASE_URL:
            raise RuntimeError("Falta SERVICE_LAYER_URL/SAP_SL_BASE_URL en el entorno")
        if not (COMPANY_DB and SL_USER and SL_PASSWORD):
            raise RuntimeError("Faltan COMPANY_DB / SL_USER / SL_PASSWORD en el entorno")

        self.base = BASE_URL
        self.cookie_path = _cookie_path_for_base(self.base)
        self.logger = log

        self.s = requests.Session()
        self.s.headers.update({
            "Accept": "application/json",
            "Connection": "keep-alive",
        })
        # TLS verify: bool o ruta de CA bundle
        self.s.verify = (CA_BUNDLE if VERIFY_SSL and CA_BUNDLE else VERIFY_SSL)

        log.info("SAP SL base=%s | verify=%s | cookie_path=%s", self.base, self.s.verify, self.cookie_path)
        self._session_id: Optional[str] = None
        # Formato preferido para literales de fecha en filtros DocDueDate
        self._date_format = SapClient._preferred_date_format or SapClient._DATE_FORMATS[0]

    # ------------------ sesión ------------------
    def login(self) -> None:
        """
        Inicia sesión y setea las cookies B1SESSION/ROUTEID con los paths adecuados.
        """
        r = self.s.post(
            f"{self.base}/Login",
            json={"CompanyDB": COMPANY_DB, "UserName": SL_USER, "Password": SL_PASSWORD},
            timeout=(5, 30),
        )
        r.raise_for_status()
        data = r.json()
        sid = data.get("SessionId")
        route = r.cookies.get("ROUTEID")

        # Reset jar y configurar paths
        self.s.cookies.clear()
        if sid:
            self.s.cookies.set("B1SESSION", sid, path=self.cookie_path)
        if route:
            # ROUTEID suele ir a path "/" en la práctica
            self.s.cookies.set("ROUTEID", route, path="/")
        self._session_id = sid

    def ensure_session(self) -> None:
        if not self._session_id:
            self.login()

    # ------------------ core request con reintentos ------------------
    def _request(self, method: str, path: str, **kwargs) -> Response:
        """
        Envoltorio para requests con:
          - ensure_session()
          - reintento automático 1 vez si 401 → re-login
          - backoff breve para errores transitorios
        """
        self.ensure_session()
        url = path if path.startswith("http") else f"{self.base}{path}"

        # Timeouts por defecto si no vienen
        if "timeout" not in kwargs:
            kwargs["timeout"] = (5, 30 if method.upper() != "POST" else 60)

        # Primer intento
        try:
            r = self.s.request(method, url, **kwargs)
        except (SSLError, ConnectionError, Timeout) as e:
            # backoff y un reintento rápido
            delay = 0.4 + random.random() * 0.6
            time.sleep(delay)
            r = self.s.request(method, url, **kwargs)

        # Si no es 401, devolver o levantar
        if r.status_code != 401:
            r.raise_for_status()
            return r

        # 401: un re-login y segundo intento
        self.login()
        r2 = self.s.request(method, url, **kwargs)
        r2.raise_for_status()
        return r2

    # ------------------ helpers ------------------
    @staticmethod
    def _escape_single_quotes(value: str) -> str:
        """Escapa comillas simples para evitar romper el $filter."""
        return value.replace("'", "''")

    @classmethod
    def _format_date_literal(cls, date_str: str, fmt: str) -> str:
        return f"{date_str}T00:00:00Z"

    def _date_literal(self, d: Optional[str], fmt: Optional[str]) -> Optional[str]:
        if not d:
            return None
        if not fmt:
            fmt = self._date_format
        return self._format_date_literal(d, fmt)

    def _build_filter(
        self,
        due_from: Optional[str],
        due_to: Optional[str],
        vendor: Optional[str],
        whs: Optional[str],
        fmt: Optional[str],
    ) -> str:
        filters: List[str] = ["DocumentStatus eq 'bost_Open'"]
        if due_from:
            filters.append(f"DocDueDate ge {self._date_literal(due_from, fmt)}")
        if due_to:
            filters.append(f"DocDueDate le {self._date_literal(due_to, fmt)}")
        if vendor:
            vendor_escaped = self._escape_single_quotes(vendor)
            filters.append(f"CardCode eq '{vendor_escaped}'")
        if whs:
            # Nota: El Service Layer de SAP B1 no soporta operadores lambda (any/all) en $filter.
            # El filtrado por almacén se resuelve en Python tras expandir DocumentLines.
            pass
        return " and ".join(filters)

    # ------------------ endpoints ------------------
    def get_open_purchase_orders(
        self,
        due_from: Optional[str],
        due_to: Optional[str],
        vendor: Optional[str],
        whs: Optional[str],
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        Lista OCs abiertas con alguna línea abierta en el almacén solicitado.
        El Service Layer no soporta filtros lambda (any/all) ni $filter dentro de
        $expand en subcolecciones, por lo que cuando se especifica un almacén se
        expanden todas las líneas y el filtrado se hace en Python.
        NOTA: Para rendimiento, aquí no calculamos totalOpenQty; se puede
        derivar en la vista detalle. (Se deja en None para UI.)
        """
        # UX: la paginación y el contador provienen del servidor y pueden no
        # coincidir con la cantidad visible tras el filtrado de líneas en Python.
        top = max(1, min(int(page_size), 100))
        skip = max(0, (max(1, int(page)) - 1) * top)

        base_params = {
            "$select": "DocEntry,DocNum,DocDueDate,CardCode,CardName",
            "$count": "true",
            "$orderby": "DocDueDate asc,DocEntry asc",
            "$top": str(top),
            "$skip": str(skip),
        }

        date_filters_present = bool(due_from or due_to)
        if date_filters_present:
            seen: set[str] = set()
            formats_to_try: List[Optional[str]] = []
            for fmt in (self._date_format, *SapClient._DATE_FORMATS):
                if fmt and fmt not in seen:
                    formats_to_try.append(fmt)
                    seen.add(fmt)
        else:
            formats_to_try = [None]

        last_error: Optional[HTTPError] = None
        for idx, fmt in enumerate(formats_to_try):
            params = dict(base_params)
            params["$filter"] = self._build_filter(due_from, due_to, vendor, whs, fmt)
            # Si se filtra por almacén, expandir DocumentLines y filtrar luego en Python
            if whs:
                params["$expand"] = "DocumentLines($select=LineNum,WarehouseCode,OpenQuantity)"
                self.logger.debug(
                    "SL expand sin filtro; filtrado de líneas por almacén en Python: whs=%s",
                    whs,
                )

            try:
                r = self._request("GET", "/PurchaseOrders", params=params)
            except HTTPError as exc:
                last_error = exc
                should_retry = (
                    date_filters_present
                    and exc.response is not None
                    and exc.response.status_code == 400
                    and idx + 1 < len(formats_to_try)
                )
                if should_retry:
                    log.warning(
                        "SAP rechazó filtro DocDueDate con formato '%s'; reintentando con '%s'",
                        fmt,
                        formats_to_try[idx + 1],
                    )
                    continue
                raise

            if date_filters_present and fmt:
                self._date_format = fmt
                SapClient._preferred_date_format = fmt

            payload = r.json()
            total = int(payload.get("@odata.count", 0))
            rows = payload.get("value", [])
            # Post-filtrado: si hay whs, conservar solo órdenes que tengan líneas expandidas
            if whs:
                filtered_rows = []
                for order in rows:
                    doc_lines = order.get("DocumentLines") or []
                    keep = [
                        d
                        for d in doc_lines
                        if d.get("WarehouseCode") == whs
                        and float(d.get("OpenQuantity") or 0) > 0
                    ]
                    if keep:
                        # El Service Layer no soporta $filter dentro de $expand;
                        # traemos todas las líneas y filtramos aquí.
                        order["DocumentLines"] = keep
                        filtered_rows.append(order)
                rows = filtered_rows

            data = []
            for it in rows:
                data.append(
                    {
                        "docEntry": it["DocEntry"],
                        "docNum": it["DocNum"],
                        "docDueDate": it.get("DocDueDate"),
                        "vendorCode": it.get("CardCode"),
                        "vendorName": it.get("CardName"),
                        "openLines": None,      # opcional (no calculado aquí)
                        "totalOpenQty": None,   # opcional (no calculado aquí)
                    }
                )
            return {"data": data, "page": int(page), "pageSize": top, "total": total}

        if last_error:
            raise last_error

        return {"data": [], "page": int(page), "pageSize": top, "total": 0}

    def get_purchase_order(self, doc_entry: int, whs: Optional[str]) -> Dict[str, Any]:
        """
        Detalle de OC; si whs se envía, filtra líneas por ese almacén.
        """
        params = {
            "$select": "DocEntry,DocNum,DocDueDate",
            "$expand": "DocumentLines($select=LineNum,ItemCode,ItemDescription,Quantity,OpenQuantity,WarehouseCode)",
        }
        r = self._request("GET", f"/PurchaseOrders({doc_entry})", params=params)
        o = r.json()

        filtered_lines = [
            d
            for d in o.get("DocumentLines", [])
            if not whs or (d.get("WarehouseCode") == whs and float(d.get("OpenQuantity") or 0) > 0)
        ]

        lines_out = []
        for d in filtered_lines:
            qty = float(d.get("Quantity", 0) or 0)
            openq = float(d.get("OpenQuantity", 0) or 0)
            lines_out.append(
                {
                    "lineNum": d["LineNum"],
                    "itemCode": d["ItemCode"],
                    "description": d.get("ItemDescription"),
                    "orderedQty": qty,
                    "receivedQty": qty - openq,
                    "openQty": openq,
                    "warehouseCode": d.get("WarehouseCode"),
                }
            )
        o["DocumentLines"] = lines_out
        return {
            "docEntry": o["DocEntry"],
            "docNum": o["DocNum"],
            "docDueDate": o.get("DocDueDate"),
            "lines": o.get("DocumentLines", []),
        }

    def post_grpo(
        self,
        po_doc_entry: int,
        whs: str,
        lines: List[Dict[str, Any]],
        supplier_ref: Optional[str],
    ) -> Dict[str, Any]:
        """
        Crea GRPO (PurchaseDeliveryNotes) desde líneas de la OC.
        lines: [{lineNum, quantity}]
        """
        # Construcción de líneas
        doc_lines = []
        for l in lines:
            doc_lines.append(
                {
                    "BaseType": 22,  # Purchase Orders
                    "BaseEntry": int(po_doc_entry),
                    "BaseLine": int(l["lineNum"]),
                    "Quantity": float(l["quantity"]),
                    "WarehouseCode": whs,
                }
            )

        payload: Dict[str, Any] = {
            # DocDate omitido → SAP usa "hoy"
            "DocumentLines": doc_lines,
        }
        # supplier_ref como campo estándar Reference2 (visible en el encabezado del doc)
        if supplier_ref:
            payload["Reference2"] = supplier_ref
            # Si tienes UDF "U_SupplierRef" y quieres poblarla, descomenta:
            # payload["U_SupplierRef"] = supplier_ref

        headers = {"Prefer": "return=representation", "Content-Type": "application/json"}

        r = self._request("POST", "/PurchaseDeliveryNotes", json=payload, headers=headers, timeout=(5, 60))
        return r.json()
