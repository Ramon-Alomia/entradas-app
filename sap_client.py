# sap_client.py
from __future__ import annotations

import os
import logging
from typing import Optional, Dict, Any, List

import requests

log = logging.getLogger("sap_client")

BASE_URL    = os.getenv("SERVICE_LAYER_URL", "").rstrip("/")
COMPANY_DB  = os.getenv("COMPANY_DB")
SL_USER     = os.getenv("SL_USER")
SL_PASSWORD = os.getenv("SL_PASSWORD")

# TLS bundle (local/Render)
CA_BUNDLE = os.getenv("SAP_SL_CA_BUNDLE") or os.getenv("REQUESTS_CA_BUNDLE")
VERIFY_SSL = True
if os.getenv("SAP_SL_VERIFY_SSL", "true").lower() in ("0", "false", "no", "off"):
    VERIFY_SSL = False


class SapClient:
    def __init__(self) -> None:
        self.base = BASE_URL
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json"})
        self.s.verify = CA_BUNDLE if VERIFY_SSL and CA_BUNDLE else VERIFY_SSL
        log.info("SAP SL base=%s | verify=%s", self.base, self.s.verify)
        self._session_id: Optional[str] = None

    # ------------------ sesión ------------------
    def login(self) -> None:
        r = self.s.post(
            f"{self.base}/Login",
            json={"CompanyDB": COMPANY_DB, "UserName": SL_USER, "Password": SL_PASSWORD},
            timeout=(5, 30),
        )
        r.raise_for_status()
        data = r.json()
        sid = data.get("SessionId")
        route = r.cookies.get("ROUTEID")
        # reset jar con paths correctos
        self.s.cookies.clear()
        self.s.cookies.set("B1SESSION", sid, path="/b1s/v1")
        if route:
            self.s.cookies.set("ROUTEID", route, path="/")
        self._session_id = sid

    def ensure_session(self) -> None:
        if not self._session_id:
            self.login()

    # ------------------ helpers ------------------
    @staticmethod
    def _date_iso(d: Optional[str]) -> Optional[str]:
        if not d:
            return None
        # 'YYYY-MM-DD' -> 'YYYY-MM-DDT00:00:00Z'
        return f"{d}T00:00:00Z"

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
        """Lista OCs abiertas con alguna línea abierta en el almacén solicitado."""
        self.ensure_session()

        filters: List[str] = ["DocumentStatus eq 'bost_Open'"]
        if due_from:
            filters.append(f"DocDueDate ge {self._date_iso(due_from)}")
        if due_to:
            filters.append(f"DocDueDate le {self._date_iso(due_to)}")
        if vendor:
            filters.append(f"CardCode eq '{vendor}'")
        if whs:
            filters.append(
                f"DocumentLines/any(d:d/WarehouseCode eq '{whs}' and d/OpenQuantity gt 0)"
            )

        filter_str = " and ".join(filters)
        top = max(1, min(int(page_size), 100))
        skip = max(0, (max(1, int(page)) - 1) * top)

        params = {
            "$select": "DocEntry,DocNum,DocDueDate,CardCode,CardName",
            "$filter": filter_str,
            "$count": "true",
            "$orderby": "DocDueDate asc,DocEntry asc",
            "$top": str(top),
            "$skip": str(skip),
        }

        r = self.s.get(f"{self.base}/PurchaseOrders", params=params, timeout=(5, 30))
        if r.status_code == 401:
            self.login()
            r = self.s.get(f"{self.base}/PurchaseOrders", params=params, timeout=(5, 30))
        r.raise_for_status()

        payload = r.json()
        total = int(payload.get("@odata.count", 0))
        rows = payload.get("value", [])

        data = []
        for it in rows:
            data.append(
                {
                    "docEntry": it["DocEntry"],
                    "docNum": it["DocNum"],
                    "docDueDate": it.get("DocDueDate"),
                    "vendorCode": it.get("CardCode"),
                    "vendorName": it.get("CardName"),
                    "openLines": None,
                    "totalOpenQty": None,
                }
            )
        return {"data": data, "page": int(page), "pageSize": top, "total": total}

    def get_purchase_order(self, doc_entry: int, whs: Optional[str]) -> Dict[str, Any]:
        """Detalle de OC; si whs se envía, filtra líneas por ese almacén."""
        self.ensure_session()

        expand = "DocumentLines($select=LineNum,ItemCode,ItemDescription,Quantity,OpenQuantity,WarehouseCode)"
        if whs:
            expand = (
                "DocumentLines("
                f"$filter=WarehouseCode eq '{whs}';"
                "$select=LineNum,ItemCode,ItemDescription,Quantity,OpenQuantity,WarehouseCode)"
            )

        params = {"$select": "DocEntry,DocNum,DocDueDate", "$expand": expand}

        r = self.s.get(f"{self.base}/PurchaseOrders({doc_entry})", params=params, timeout=(5, 30))
        if r.status_code == 401:
            self.login()
            r = self.s.get(f"{self.base}/PurchaseOrders({doc_entry})", params=params, timeout=(5, 30))
        r.raise_for_status()
        o = r.json()

        lines_out = []
        for d in (o.get("DocumentLines") or []):
            lines_out.append(
                {
                    "lineNum": d["LineNum"],
                    "itemCode": d["ItemCode"],
                    "description": d.get("ItemDescription"),
                    "orderedQty": float(d.get("Quantity", 0) or 0),
                    "receivedQty": float(d.get("Quantity", 0) or 0)
                    - float(d.get("OpenQuantity", 0) or 0),
                    "openQty": float(d.get("OpenQuantity", 0) or 0),
                    "warehouseCode": d.get("WarehouseCode"),
                }
            )
        return {
            "docEntry": o["DocEntry"],
            "docNum": o["DocNum"],
            "docDueDate": o.get("DocDueDate"),
            "lines": lines_out,
        }

    def post_grpo(
        self,
        po_doc_entry: int,
        whs: str,
        lines: List[Dict[str, Any]],
        supplier_ref: Optional[str],
    ) -> Dict[str, Any]:
        """
        Crea GRPO desde líneas de la OC.
        lines: [{lineNum, quantity}]
        """
        self.ensure_session()

        doc_lines = []
        for l in lines:
            doc_lines.append(
                {
                    "BaseType": 22,  # Purchase Orders
                    "BaseEntry": po_doc_entry,
                    "BaseLine": int(l["lineNum"]),
                    "Quantity": float(l["quantity"]),
                    "WarehouseCode": whs,
                }
            )

        payload = {
            "DocDate": None,  # usa hoy
            "U_SupplierRef": supplier_ref if supplier_ref else None,
            "DocumentLines": doc_lines,
        }

        r = self.s.post(
            f"{self.base}/PurchaseDeliveryNotes", json=payload, timeout=(5, 60)
        )
        if r.status_code == 401:
            self.login()
            r = self.s.post(
                f"{self.base}/PurchaseDeliveryNotes", json=payload, timeout=(5, 60)
            )
        r.raise_for_status()
        return r.json()
