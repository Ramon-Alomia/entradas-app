# sap_client.py
import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests import Session, RequestException

# ----------------- ENV -----------------
SERVICE_LAYER_URL = (os.getenv("SERVICE_LAYER_URL") or "").rstrip("/")
COMPANY_DB  = os.getenv("COMPANY_DB")
SL_USER     = os.getenv("SL_USER")
SL_PASSWORD = os.getenv("SL_PASSWORD")

# TLS híbrido:
# - SAP_SL_VERIFY_SSL=false  -> NO verifica (solo para desarrollo)
# - SAP_SL_CA_BUNDLE=<ruta>  -> usa esa CA .pem
# - si existe certs/sap_sl_ca.pem -> lo usa por defecto
VERIFY_FLAG  = os.getenv("SAP_SL_VERIFY_SSL", "true").lower() not in ("0", "false", "no")
CA_ENV       = os.getenv("SAP_SL_CA_BUNDLE") or os.getenv("REQUESTS_CA_BUNDLE")
BASE_DIR     = Path(__file__).resolve().parent
DEFAULT_CA   = BASE_DIR / "certs" / "sap_sl_ca.pem"

def _resolve_verify() -> bool | str:
    if not VERIFY_FLAG:
        return False
    if CA_ENV:
        p = Path(CA_ENV)
        if not p.is_absolute():
            p = BASE_DIR / CA_ENV
        if p.exists():
            return str(p)
    if DEFAULT_CA.exists():
        return str(DEFAULT_CA)
    return True  # trust store del sistema

logger = logging.getLogger(__name__)


class SAPClient:
    """
    Cliente SAP B1 Service Layer (v1).
    - Maneja login con jar "limpio" y cookies B1SESSION/ROUTEID
    - Cookie B1SESSION con path '/b1s/v1' (igual que tu app anterior)
    - TLS configurable (CA propia o verify=False)
    - Reintento automático al recibir 401
    """

    def __init__(self) -> None:
        if not SERVICE_LAYER_URL or not COMPANY_DB or not SL_USER or not SL_PASSWORD:
            raise RuntimeError("Faltan variables SAP: SERVICE_LAYER_URL, COMPANY_DB, SL_USER, SL_PASSWORD")

        self.base = SERVICE_LAYER_URL
        self.s: Session = requests.Session()
        self.s.verify = _resolve_verify()
        self.s.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        self._logged_at: float = 0.0
        logger.info("SAP SL base=%s | verify=%s", self.base, self.s.verify)

    # ---------- Cookies / Login ----------
    def _reset_jar_and_set_cookies(self, auth_resp: requests.Response) -> None:
        """Replica el patrón 'reset jar + set cookie con path' de tu app anterior."""
        data  = auth_resp.json()
        sid   = data.get("SessionId")
        route = auth_resp.cookies.get("ROUTEID")

        # reset jar
        self.s.cookies.clear()
        # B1SESSION debe ir con el path EXACTO de v1
        self.s.cookies.set("B1SESSION", sid, path="/b1s/v1")
        if route:
            self.s.cookies.set("ROUTEID", route, path="/")
        logger.debug("Jar reseteado: %s", self.s.cookies.get_dict())

    def login(self) -> None:
        r = self.s.post(
            f"{self.base}/Login",
            json={"CompanyDB": COMPANY_DB, "UserName": SL_USER, "Password": SL_PASSWORD},
            timeout=(5, 30)
        )
        r.raise_for_status()
        self._reset_jar_and_set_cookies(r)
        self._logged_at = time.time()

    def ensure_session(self) -> None:
        # renueva si no hay cookie o si pasaron ~25 min
        if "B1SESSION" not in self.s.cookies or (time.time() - self._logged_at) > (25 * 60):
            self.login()

    def _retry_401(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            r = self.s.request(method, url, **kwargs)
            if r.status_code == 401:
                self.login()
                time.sleep(0.2)
                r = self.s.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except RequestException as e:
            # logs útiles
            logger.error("SL %s %s error: %s", method, url, e, exc_info=True)
            raise

    # ---------- Helpers ----------
    @staticmethod
    def _po_filter(due_from: Optional[str], due_to: Optional[str], vendor: Optional[str]) -> str:
        parts = ["DocumentStatus eq 'bost_Open'"]
        if due_from: parts.append(f"DocDueDate ge {due_from!r}")
        if due_to:   parts.append(f"DocDueDate le {due_to!r}")
        if vendor:   parts.append(f"CardCode eq {vendor!r}")
        return " and ".join(parts)

    # ---------- Métodos públicos ----------
    def get_open_purchase_orders(
        self, *, due_from: Optional[str], due_to: Optional[str],
        vendor: Optional[str], page: int, page_size: int
    ) -> Dict[str, Any]:
        self.ensure_session()
        top  = max(1, min(page_size, 100))
        skip = (max(1, page) - 1) * top
        flt  = self._po_filter(due_from, due_to, vendor)
        url = (
            f"{self.base}/PurchaseOrders"
            f"?$select=DocEntry,DocNum,CardCode,CardName,DocDueDate,DocumentStatus"
            f"&$filter={flt}"
            f"&$orderby=DocDueDate asc,DocNum asc"
            f"&$top={top}&$skip={skip}"
        )
        r = self._retry_401("GET", url, timeout=(5, 30))
        return {"value": r.json().get("value", [])}

    def get_purchase_order(self, doc_entry: int) -> Dict[str, Any]:
        self.ensure_session()
        url = (
            f"{self.base}/PurchaseOrders({doc_entry})"
            f"?$select=DocEntry,DocNum,CardCode,CardName,DocDueDate,DocumentLines"
        )
        r = self._retry_401("GET", url, timeout=(5, 30))
        po = r.json()
        lines_out: List[Dict[str, Any]] = []
        for ln in po.get("DocumentLines", []):
            qty = float(ln.get("Quantity", 0) or 0)
            rec = float(ln.get("ReceivedQuantity", 0) or 0)
            open_qty = max(qty - rec, 0.0)
            lines_out.append({
                "LineNum": ln.get("LineNum"),
                "ItemCode": ln.get("ItemCode"),
                "ItemDescription": ln.get("ItemDescription"),
                "WarehouseCode": ln.get("WarehouseCode"),
                "OrderedQty": qty,
                "ReceivedQty": rec,
                "OpenQty": open_qty,
            })
        return {
            "DocEntry": po.get("DocEntry"),
            "DocNum": po.get("DocNum"),
            "CardCode": po.get("CardCode"),
            "CardName": po.get("CardName"),
            "DocDueDate": po.get("DocDueDate"),
            "Lines": lines_out,
        }

    def post_grpo(
        self, *, doc_entry: int, whs_code: str,
        lines: List[Dict[str, Any]], supplier_ref: Optional[str]
    ) -> Dict[str, Any]:
        """
        Crea un GRPO (Goods Receipt PO) en PurchaseDeliveryNotes.
        'lines' => [{'lineNum': int, 'quantity': float}, ...]
        """
        self.ensure_session()
        doc_lines = [{
            "BaseType": 22,                      # 22 = Purchase Orders
            "BaseEntry": int(doc_entry),
            "BaseLine":  int(ln["lineNum"]),
            "Quantity":  float(ln["quantity"]),
            "WarehouseCode": whs_code
        } for ln in lines]

        payload: Dict[str, Any] = {
            "DocDate": time.strftime("%Y-%m-%d"),
            "Comments": f"Recepción portal - PO {doc_entry}",
            "DocumentLines": doc_lines
        }
        if supplier_ref:
            payload["Comments"] += f" | Ref: {supplier_ref}"

        r = self._retry_401(
            "POST", f"{self.base}/PurchaseDeliveryNotes",
            json=payload,
            headers={"Prefer": "return=representation"},
            timeout=(5, 60)
        )
        return r.json()
