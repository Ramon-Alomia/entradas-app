from __future__ import annotations
import os
import logging
from datetime import timedelta

from flask import Flask, jsonify
from flask_cors import CORS
from flask_talisman import Talisman
from dotenv import load_dotenv

import psycopg
from psycopg.rows import dict_row

# -----------------------------------------------------------------------------
# Carga variables desde .env (solo en local)
# -----------------------------------------------------------------------------
load_dotenv(override=False)

# -----------------------------------------------------------------------------
# Configuración básica
# -----------------------------------------------------------------------------
FLASK_ENV = os.getenv("FLASK_ENV", "production").lower()
SECRET_KEY = os.getenv("SECRET_KEY", "changeme")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
DATABASE_URL = os.getenv("DATABASE_URL")  # Debe incluir sslmode=require en Neon

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY no está configurado")
if not DATABASE_URL:
    # No bloqueamos aquí; /db/ping mostrará error legible si falta.
    pass

# -----------------------------------------------------------------------------
# App Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_SECURE = FLASK_ENV == "production",
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = "Lax",
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30),
    JSON_SORT_KEYS = False,
)

# Logging
logging.basicConfig(level=logging.DEBUG if FLASK_ENV != "production" else logging.INFO)
app.logger.setLevel(logging.DEBUG if FLASK_ENV != "production" else logging.INFO)

# -----------------------------------------------------------------------------
# Seguridad (Talisman) y CORS
# En desarrollo NO forzamos HTTPS para que no te redirija.
# -----------------------------------------------------------------------------
csp = {
    "default-src": ["'self'"],
    "script-src":  ["'self'", "cdnjs.cloudflare.com"],
    "style-src":   ["'self'", "'unsafe-inline'", "cdnjs.cloudflare.com", "fonts.googleapis.com"],
    "font-src":    ["'self'", "fonts.gstatic.com"],
    "img-src":     ["'self'", "data:"],
}
Talisman(
    app,
    content_security_policy=csp,
    force_https=(FLASK_ENV == "production"),
    strict_transport_security=(FLASK_ENV == "production"),
)

if CORS_ORIGINS:
    CORS(app, origins=CORS_ORIGINS, supports_credentials=True)
else:
    CORS(app)

# -----------------------------------------------------------------------------
# DB helper (psycopg v3)
# -----------------------------------------------------------------------------
def get_db_connection():
    """
    Retorna una conexión psycopg v3.
    Requiere que DATABASE_URL incluya sslmode=require para Neon.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurado")
    # Si tu cadena no incluyera sslmode, puedes forzarlo aquí:
    # return psycopg.connect(DATABASE_URL, sslmode="require")
    return psycopg.connect(DATABASE_URL)

# -----------------------------------------------------------------------------
# Rutas base
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "env": FLASK_ENV,
        "service": "recepciones-base"
    }), 200

@app.get("/db/ping")
def db_ping():
    try:
        with get_db_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT 1 AS ok;")
                row = cur.fetchone()
                return jsonify({"db": "ok", "result": row}), 200
    except Exception as e:
        app.logger.exception("DB ping error")
        return jsonify({
            "db": "error",
            "message": str(e)
        }), 500

@app.get("/")
def root():
    return jsonify({
        "message": "API base de Recepciones lista. Prueba /health y /db/ping"
    }), 200

# -----------------------------------------------------------------------------
# Main de desarrollo
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    app.run(host=host, port=port, debug=(FLASK_ENV != "production"))