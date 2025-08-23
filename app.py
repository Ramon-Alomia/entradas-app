# app.py
from __future__ import annotations

import os
import logging
from typing import Any, Dict

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Carga variables del .env (local). En Render las obtiene del entorno.
load_dotenv()

# -------- Blueprints (asegúrate de que estos archivos existen) --------
from auth import bp_auth          # /api/login (JWT)
from recepciones_api import bp_recepciones  # /api/orders, /api/receipts


def create_app() -> Flask:
    app = Flask(__name__)

    # --- Logging básico
    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)

    # --- Secret key (para sesiones/firmas si algún día usas server-side sessions)
    secret = os.getenv("SECRET_KEY")
    if not secret:
        app.logger.warning("SECRET_KEY no está definido; defínelo en .env / Render.")
    app.config["SECRET_KEY"] = secret or "dev-not-secure"

    # --- CORS
    #  - En dev: por defecto '*'
    #  - En prod: pon CORS_ORIGINS="https://recepciones.bersacloud.app"
    cors_origins = os.getenv("CORS_ORIGINS", "*")
    CORS(app, resources={r"/api/*": {"origins": cors_origins}})

    # --- Rutas de salud
    @app.get("/health")
    def health():
        return jsonify({"ok": True}), 200

    @app.get("/ping")
    def ping():
        return "pong", 200

    # --- Registro de blueprints
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_recepciones)

    # --- Manejo homogéneo de errores no controlados (JSON)
    @app.errorhandler(Exception)
    def handle_unexpected(e: Exception):
        app.logger.exception("Unhandled error: %s", e)
        code = getattr(e, "code", 500) or 500
        return jsonify({"error": {"code": "UNEXPECTED_ERROR", "message": str(e)}}), int(code)

    return app


# WSGI entrypoint
app = create_app()

if __name__ == "__main__":
    # Ejecuta en dev; en Render se usa gunicorn con wsgi:app
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") not in ("0", "false", "False")
    app.run(host="0.0.0.0", port=port, debug=debug)
