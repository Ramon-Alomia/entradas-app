# check_receipts_log.py
import os, sys, argparse, psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from datetime import datetime, timezone

def mask(url: str) -> str:
    try:
        from urllib.parse import urlsplit, urlunsplit
        sp = urlsplit(url)
        netloc = sp.netloc
        if "@" in netloc:
            creds, host = netloc.split("@", 1)
            user = creds.split(":", 1)[0]
            netloc = f"{user}:***@{host}"
        return urlunsplit((sp.scheme, netloc, sp.path, sp.query, sp.fragment))
    except Exception:
        return "***"

def parse_iso_dt(s: str):
    """Acepta YYYY-MM-DD o un ISO8601 completo."""
    try:
        if len(s) == 10:
            # fecha sin hora → asumimos 00:00Z
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        raise argparse.ArgumentTypeError("Fecha inválida. Usa YYYY-MM-DD o ISO8601 (e.g. 2025-09-01T00:00:00Z).")

def build_query(args):
    where = []
    params = []

    if args.since:
        where.append("created_at >= %s")
        params.append(parse_iso_dt(args.since))

    if args.until:
        where.append("created_at < %s")
        params.append(parse_iso_dt(args.until))

    if args.user:
        where.append("posted_by = %s")
        params.append(args.user)

    if args.whs:
        where.append("whs_code = %s")
        params.append(args.whs)

    if args.po:
        where.append("po_doc_entry = %s")
        params.append(int(args.po))

    if args.item:
        where.append("item_code = %s")
        params.append(args.item)

    sql = """
    SELECT
      id, created_at, po_doc_entry, po_line_num,
      item_code, whs_code, posted_qty, posted_by, sl_doc_entry, payload_json
    FROM receipts_log
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(int(args.limit))

    return sql, params

def main():
    parser = argparse.ArgumentParser(
        description="Inspecciona los últimos registros de receipts_log (Neon)."
    )
    parser.add_argument("--url", help="Override de DATABASE_URL (si no quieres usar .env).")
    parser.add_argument("--limit", type=int, default=10, help="Límites de filas (default: 10).")
    parser.add_argument("--since", help="Filtra desde esta fecha (YYYY-MM-DD o ISO8601).")
    parser.add_argument("--until", help="Filtra hasta esta fecha (YYYY-MM-DD o ISO8601, exclusivo).")
    parser.add_argument("--user", help="Filtra por posted_by (username).")
    parser.add_argument("--whs", help="Filtra por whs_code.")
    parser.add_argument("--po", help="Filtra por po_doc_entry.")
    parser.add_argument("--item", help="Filtra por item_code.")
    parser.add_argument("--json", type=int, nargs="?", const=200,
                        help="Muestra un extracto de payload_json (N chars, default 200).")
    args = parser.parse_args()

    # Carga .env si existe en la carpeta
    load_dotenv()

    db_url = args.url or os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL no está en el entorno ni se pasó con --url.")
        print("   Ejecuta desde la carpeta donde está tu .env o usa:")
        print('   PowerShell: python .\\check_receipts_log.py --url "postgresql://...sslmode=require"')
        sys.exit(1)

    sql, params = build_query(args)

    print("📦 Conectando a:", mask(db_url))
    try:
        with psycopg.connect(db_url) as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            print(f"✅ Consulta OK. Filas: {len(rows)}")
            if not rows:
                print("ℹ️ No hay registros con ese filtro. Genera un POST /api/receipts y vuelve a correr este script.")
                return

            total_qty = 0
            for r in rows:
                total_qty += float(r["posted_qty"] or 0)
                head = (
                    f"- #{r['id']} | {r['created_at']} | "
                    f"PO {r['po_doc_entry']} L{r['po_line_num']} | "
                    f"{r['item_code']} @ {r['whs_code']} | "
                    f"qty {r['posted_qty']} | user {r['posted_by']} | "
                    f"GRPO {r['sl_doc_entry']}"
                )
                print(head)
                if args.json:
                    pj = r.get("payload_json")
                    if pj is not None:
                        s = str(pj)
                        s = s[: args.json] + ("…" if len(s) > args.json else "")
                        print("   payload_json:", s)
            print(f"Σ posted_qty (estas filas): {total_qty}")

    except psycopg.errors.UndefinedTable:
        print("❌ La tabla receipts_log no existe.")
        print("   Asegúrate de haber corrido la migración/SQL de creación de la tabla.")
        sys.exit(2)
    except Exception as e:
        print("❌ Error consultando receipts_log:", repr(e))
        sys.exit(3)

if __name__ == "__main__":
    main()
