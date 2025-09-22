import importlib

import pytest
from requests.exceptions import HTTPError

import sap_client as sap_client_module


class DummyResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture
def sap(monkeypatch):
    monkeypatch.setenv("SAP_SL_BASE_URL", "https://sap.example.com/b1s/v1")
    monkeypatch.setenv("COMPANY_DB", "TESTDB")
    monkeypatch.setenv("SL_USER", "user")
    monkeypatch.setenv("SL_PASSWORD", "pass")
    module = importlib.reload(sap_client_module)
    module.SapClient._preferred_date_format = None
    return module


def _sample_payload():
    return {
        "@odata.count": 1,
        "value": [
            {
                "DocEntry": 1,
                "DocNum": 42,
                "DocDueDate": "2025-09-05",
                "CardCode": "V100",
                "CardName": "Proveedor 100",
            }
        ],
    }


def test_get_open_purchase_orders_uses_datetimeoffset(monkeypatch, sap):
    client = sap.SapClient()
    captured = {}

    def fake_request(self, method, path, params=None, **kwargs):
        captured["params"] = params or {}
        return DummyResponse(_sample_payload())

    monkeypatch.setattr(sap.SapClient, "_request", fake_request, raising=False)

    result = client.get_open_purchase_orders("2025-09-01", "2025-09-30", None, "WH1")

    flt = captured["params"]["$filter"]
    assert "datetimeoffset'2025-09-01T00:00:00Z'" in flt
    assert "datetimeoffset'2025-09-30T00:00:00Z'" in flt
    assert result["total"] == 1
    assert result["data"][0]["docEntry"] == 1


def test_get_open_purchase_orders_fallbacks_on_400(monkeypatch, sap):
    client = sap.SapClient()
    filters = []

    def fake_request(self, method, path, params=None, **kwargs):
        filters.append((params or {}).get("$filter", ""))
        if "datetimeoffset" in filters[-1]:
            raise HTTPError("bad request", response=DummyResponse(status_code=400))
        return DummyResponse({"@odata.count": 0, "value": []})

    monkeypatch.setattr(sap.SapClient, "_request", fake_request, raising=False)

    result = client.get_open_purchase_orders("2025-09-01", None, None, "WH1")

    assert any("datetimeoffset" in f for f in filters)
    assert any("datetime'" in f for f in filters[1:])
    assert result["total"] == 0
    assert sap.SapClient._preferred_date_format == "datetime"
