# app.py
import os
import logging
from datetime import timedelta

from flask import Flask, jsonify
from flask_cors import CORS
from flask_talisman import Talisman
from dotenv import load_dotenv

import psycopg
from psycopg.rows import dict_row

# Carga .env
load_dotenv(override=False)

FLASK_ENV = os.getenv("FLASK_ENV", "production").lower()
SECRET_KEY = os.getenv("SECRET_KEY", "changeme")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
DATABASE_URL = os.getenv("DATABASE_URL")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_SECURE = (FLASK_ENV == "production"),
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = "Lax",
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30),
    JSON_SORT_KEYS = False,
)

logging.basicConfig(level=logging.DEBUG if FLASK_ENV != "production" else logging.INFO)
app.logger.setLevel(logging.DEBUG if FLASK_ENV != "production" else logging.INFO)

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

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurado")
    return psycopg.connect(DATABASE_URL)

@app.get("/health")
def health():
    return jsonify({"status":"ok","env":FLASK_ENV,"service":"recepciones-api"}), 200

@app.get("/db/ping")
def db_ping():
    try:
        with get_db_connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT 1 AS ok;")
                row = cur.fetchone()
                return jsonify({"db":"ok","result":row}), 200
    except Exception as e:
        app.logger.exception("DB ping error")
        return jsonify({"db":"error","message":str(e)}), 500

@app.get("/")
def root():
    return jsonify({"message":"API Recepciones lista. Endpoints: /api/orders, /api/orders/<docEntry>, /api/receipts"}), 200

# --- registra el blueprint ---
from recepciones_api import bp_recepciones
app.register_blueprint(bp_recepciones)

if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    app.run(host=host, port=port, debug=(FLASK_ENV != "production"))
