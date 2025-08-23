# check_receipts_log.py
import os, sys, argparse, psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="Override de DATABASE_URL")
    args = parser.parse_args()

    # Carga .env de la carpeta actual
    load_dotenv()

    db_url = args.url or os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ DATABASE_URL no está en el entorno ni se pasó con --url.")
        print("   Ejecuta desde la carpeta donde está tu .env o usa:")
        print('   python check_receipts_log.py --url "postgresql://...sslmode=require"')
        sys.exit(1)

    print("📦 Conectando a:", mask(db_url))
    try:
        with psycopg.connect(db_url) as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT id, created_at, po_doc_entry, po_line_num, posted_qty, posted_by, sl_doc_entry
                FROM receipts_log
                ORDER BY id DESC
                LIMIT 10;
            """)
            rows = cur.fetchall()
            print(f"✅ Consulta OK. Filas: {len(rows)}")
            if not rows:
                print("ℹ️ No hay registros en receipts_log. Genera uno con POST /api/receipts y vuelve a correr este script.")
                return
            for r in rows:
                print(f"- #{r['id']} | {r['created_at']} | PO {r['po_doc_entry']} L{r['po_line_num']} | qty {r['posted_qty']} | user {r['posted_by']} | GRPO {r['sl_doc_entry']}")
    except Exception as e:
        print("❌ Error consultando receipts_log:", repr(e))
        sys.exit(2)

if __name__ == "__main__":
    main()
