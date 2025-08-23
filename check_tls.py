import os, pathlib, requests

BASE = pathlib.Path().resolve()
print("Working dir:", BASE)
print("Env SAP_SL_VERIFY_SSL:", os.getenv("SAP_SL_VERIFY_SSL"))
print("Env SAP_SL_CA_BUNDLE:", os.getenv("SAP_SL_CA_BUNDLE"))

# Ajusta/añade aquí los nombres que tengas en certs/
candidates = [
    os.getenv("SAP_SL_CA_BUNDLE") or "",
    "certs/fullchain.crt",
    "certs/fullchain.pem",
    "certs/sap_sl_ca.pem",
    "certs/intermediate.pem",
    "certs/intermediate.crt",
]

tested = set()
url = "https://hwvdvsbo04.virtualdv.cloud:50000/b1s/v1/$metadata"

for cand in candidates:
    if not cand:
        continue
    p = pathlib.Path(cand)
    if not p.is_absolute():
        p = BASE / p
    if str(p) in tested:
        continue
    tested.add(str(p))

    print("\n--- Trying:", p)
    print("Exists:", p.exists())
    if p.exists():
        try:
            r = requests.get(url, verify=str(p), timeout=20)
            print("HTTP:", r.status_code, "| bytes:", len(r.content))
        except Exception as e:
            print("ERROR:", type(e).__name__, e)

# Conexión base (solo para confirmar alcance de red)
try:
    r = requests.get(url, verify=False, timeout=10)
    print("\nVerify=False test -> HTTP:", r.status_code)
except Exception as e:
    print("\nVerify=False test ERROR:", type(e).__name__, e)