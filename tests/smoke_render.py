# tests/smoke_render.py
import argparse, sys, requests, json

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Base URL, ej. https://entradas-app.onrender.com")
    ap.add_argument("--user", required=True, help="Usuario")
    ap.add_argument("--password", required=True, help="Password")
    ap.add_argument("--due_from", default="2025-08-01")
    ap.add_argument("--due_to",   default="2025-09-01")
    ap.add_argument("--whs", help="Whs opcional para probar detalle", default=None)
    args = ap.parse_args()

    base = args.base.rstrip("/")

    # 1) LOGIN
    print(f"[1] POST {base}/login")
    r = requests.post(f"{base}/login", json={"username": args.user, "password": args.password}, timeout=60)
    print("   status:", r.status_code)
    if r.status_code != 200:
        print("   body:", r.text)
        sys.exit(1)
    data = r.json()
    token = data.get("token")
    if not token:
        print("   ❌ Sin token en respuesta")
        print("   body:", r.text)
        sys.exit(1)
    print("   ✅ token len:", len(token))
    headers = {"Authorization": f"Bearer {token}"}
    # Debug: muestra 20 chars del token
    print("   hdr Authorization:", f"Bearer {token[:20]}...")

    # 2) /me (opcional, confirma que el token se acepta)
    print(f"[2] GET  {base}/me")
    r = requests.get(f"{base}/me", headers=headers, timeout=60)
    print("   status:", r.status_code)
    print("   body:", r.text[:300])
    if r.status_code != 200:
        print("   ❌ El token no fue aceptado en /me")
        sys.exit(2)

    # 3) /orders
    print(f"[3] GET  {base}/orders")
    params = {"due_from": args.due_from, "due_to": args.due_to, "page": "1", "pageSize": "10"}
    r = requests.get(f"{base}/orders", params=params, headers=headers, timeout=60)
    print("   url:", r.url)
    print("   status:", r.status_code)
    print("   body:", r.text[:500])
    if r.status_code != 200:
        print("   ❌ No se pudo listar órdenes.")
        sys.exit(3)

    # 4) (opcional) si quieres probar detalle rápido:
    try:
        doc_entry = (r.json().get("data") or [])[0]["docEntry"]
        print(f"[4] GET  {base}/orders/{doc_entry}")
        detail_url = f"{base}/orders/{doc_entry}"
        if args.whs:
            detail_url += f"?whsCode={args.whs}"
        r2 = requests.get(detail_url, headers=headers, timeout=60)
        print("    status:", r2.status_code)
        print("    body:", r2.text[:500])
    except Exception as e:
        print("   (info) No se probó detalle:", e)

if __name__ == "__main__":
    main()
