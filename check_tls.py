import os
import sys
import json
import argparse
import pathlib
import requests

DEFAULT_CANDIDATES = [
    # 1) Respeta env si está seteado
    os.getenv("SAP_SL_CA_BUNDLE") or "",
    # 2) Rutas comunes en el repo
    "certs/fullchain.crt",
    "certs/fullchain.pem",
    "certs/sap_sl_ca.pem",
    "certs/intermediate.pem",
    "certs/intermediate.crt",
]

def info(msg): print(msg)
def warn(msg): print(f"WARNING: {msg}")
def err(msg):  print(f"ERROR: {msg}")

def resolve(path: str, base: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = base / p
    return p

def try_request(url: str, verify, timeout=(5, 20)):
    """
    Devuelve (ok_tls: bool, status: int|None, exc: Exception|None, bytes_len: int|None)
    TLS OK se interpreta como: no hubo SSLError y recibimos respuesta HTTP (cualquiera).
    """
    try:
        r = requests.get(url, verify=verify, timeout=timeout)
        return True, r.status_code, None, len(r.content)
    except requests.exceptions.SSLError as e:
        return False, None, e, None
    except Exception as e:
        # Pudo ser DNS, red, timeout, etc. TLS no confirmado.
        return False, None, e, None

def try_login(base_url: str, verify, company: str, user: str, password: str):
    """
    Intenta POST /Login con credenciales del entorno.
    Devuelve (ok_tls: bool, status: int|None, exc: Exception|None, snippet:str)
    """
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/Login",
            json={"CompanyDB": company, "UserName": user, "Password": password},
            headers={"Content-Type":"application/json", "Accept":"application/json"},
            verify=verify,
            timeout=(5,30)
        )
        snip = r.text[:200].replace("\n"," ")
        return True, r.status_code, None, snip
    except requests.exceptions.SSLError as e:
        return False, None, e, ""
    except Exception as e:
        return False, None, e, ""

def main():
    parser = argparse.ArgumentParser(
        description="Verificador de TLS/SSL contra SAP Service Layer."
    )
    parser.add_argument("--base-url", help="URL base del Service Layer (ej: https://host:50000/b1s/v1). "
                                           "Si no, usa SAP_SL_BASE_URL o el valor por defecto de tu entorno.",
                        default=os.getenv("SAP_SL_BASE_URL", "https://hwvdvsbo04.virtualdv.cloud:50000/b1s/v1"))
    parser.add_argument("--bundle", help="Ruta del CA bundle a probar primero (override).")
    parser.add_argument("--verify", choices=["true","false","auto"], default=os.getenv("SAP_SL_VERIFY_SSL","auto"),
                        help="Forzar verificación TLS: true/false/auto (default: auto, usa env/ajustes).")
    parser.add_argument("--login", action="store_true",
                        help="Además de $metadata, intenta POST /Login usando SAP_SL_COMPANY_DB, SAP_SL_USER, SAP_SL_PASSWORD.")
    parser.add_argument("--only", action="store_true",
                        help="Si se indica, solo prueba el --bundle (no recorre candidatos).")
    args = parser.parse_args()

    base_dir = pathlib.Path().resolve()
    info(f"Working dir: {base_dir}")
    info(f"Env SAP_SL_VERIFY_SSL: {os.getenv('SAP_SL_VERIFY_SSL')}")
    info(f"Env SAP_SL_CA_BUNDLE: {os.getenv('SAP_SL_CA_BUNDLE')}")
    info(f"Base URL: {args.base_url}")

    # Determinar verify para requests, siguiendo la misma lógica que tu app:
    # - verify=false → desactiva validación
    # - verify=true  → valida con CA por defecto del sistema
    # - verify=auto  → si hay bundle, úsalo; si no, True.
    verify_value = True
    if args.verify.lower() == "false":
        verify_value = False
    elif args.verify.lower() == "true":
        verify_value = True
    else:
        # auto
        if args.bundle or os.getenv("SAP_SL_CA_BUNDLE"):
            verify_value = str(resolve(args.bundle or os.getenv("SAP_SL_CA_BUNDLE"), base_dir))
        else:
            verify_value = True

    # Construir lista a probar
    candidates = []
    if isinstance(verify_value, str):
        # auto con bundle: primero ese
        candidates.append(verify_value)

    if args.bundle and args.only:
        pass  # Solo el bundle pasado
    else:
        # Recorre también los defaults (sin duplicados)
        for c in DEFAULT_CANDIDATES:
            if not c:
                continue
            p = str(resolve(c, base_dir))
            if p not in candidates:
                candidates.append(p)

        # Caso verify=True puro (sin bundle), probamos con True
        if verify_value is True and not args.only:
            candidates.insert(0, True)

    tested = set()
    any_tls_ok = False
    md_url = f"{args.base_url.rstrip('/')}/$metadata"

    for cand in candidates:
        key = str(cand)
        if key in tested:
            continue
        tested.add(key)

        info("\n--- Probar verify={} ---".format(cand))
        if isinstance(cand, str) and cand not in ("True","False"):
            p = pathlib.Path(cand)
            info(f"Ruta: {p} | Exists: {p.exists()}")
            if not p.exists():
                warn("El archivo no existe. Siguiente candidato...")
                continue

        ok_tls, status, ex, blen = try_request(md_url, verify=cand)
        if ok_tls and status is not None:
            any_tls_ok = True
            info(f"   ✅ TLS OK → HTTP {status} | bytes: {blen}")
            # 401/403/200 nos sirven para saber que TLS fue correcto.
        else:
            err(f"   ❌ TLS/HTTP error: {type(ex).__name__ if ex else 'Unknown'} {ex}")
            continue

        if args.login:
            comp = os.getenv("SAP_SL_COMPANY_DB", "")
            usr  = os.getenv("SAP_SL_USER", "")
            pwd  = os.getenv("SAP_SL_PASSWORD", "")
            if not (comp and usr and pwd):
                warn("Variables SAP_SL_COMPANY_DB/USER/PASSWORD no están completas; omito /Login.")
            else:
                info("   → Intentando /Login (esto sí requiere credenciales correctas)…")
                ok2, st2, ex2, snip = try_login(args.base_url, verify=cand, company=comp, user=usr, password=pwd)
                if ok2 and st2 is not None:
                    info(f"      /Login HTTP {st2} | body: {snip}")
                    # 200 (ok), 400/401 (credenciales) = TLS OK de cualquier modo
                else:
                    err(f"      ❌ Error en /Login: {type(ex2).__name__ if ex2 else 'Unknown'} {ex2}")
                    # login falló por red/TLS → útil saberlo, pero no invalida que $metadata ya probó TLS.

    # Como último recurso, si no hubo ningún candidato que levantara TLS,
    # intentamos verify=False para confirmar alcance de red
    if not any_tls_ok:
        info("\n--- Prueba final con verify=False (no recomendado; sólo diagnóstico) ---")
        ok_tls, status, ex, blen = try_request(md_url, verify=False)
        if ok_tls:
            info(f"   ⚠️  Con verify=False → HTTP {status} (TLS deshabilitado)")
            # Alcance de red hay, pero CA bundle no está correcto → exit 2
            sys.exit(2)
        else:
            err(f"   ❌ Ni siquiera con verify=False → {type(ex).__name__ if ex else 'Unknown'} {ex}")
            # Probable problema de red/DNS
            sys.exit(4)

    # Si llegamos aquí y hubo al menos una salida TLS OK, exit 0
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user")
        sys.exit(1)
