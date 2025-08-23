# app.py
from __future__ import annotations

import os
import logging
from typing import Any
from flask import Flask, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

# ---------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------
def create_app() -> Flask:
    # Carga variables del .env (localmente)
    load_dotenv()

    app = Flask(__name__, static_folder="static", template_folder="templates")

    # Logging básico
    logging.basicConfig(level=logging.INFO)
    app.logger.setLevel(logging.INFO)

    # SECRET_KEY (para sesiones server-side si en algún momento las usas)
    secret = os.getenv("SECRET_KEY")
    app.config["SECRET_KEY"] = secret or "dev-not-secure"
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    # CORS solo para /api/*
    cors_origins = os.getenv("CORS_ORIGINS", "*")
    CORS(app, resources={r"/api/*": {"origins": cors_origins}})

    # Rutas básicas de salud
    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/ping")
    def ping():
        return "pong", 200

    # UI simple (login + dashboard)
    @app.get("/")
    def webapp():
        return render_template("index.html")

    # -----------------------------------------------------------------
    # Blueprints (auth + recepciones)
    #   * IMPORTA AQUÍ para evitar importaciones cíclicas.
    #   * Registro idempotente para blindarte contra doble import/reloader.
    # -----------------------------------------------------------------
    from auth import bp_auth          # bp_auth = Blueprint("auth_api", __name__, url_prefix="/api")
    from recepciones_api import bp_recepciones  # Blueprint("recepciones", __name__, url_prefix="/api")

    # Antes de registrar, loguea los ya existentes
    app.logger.info("Blueprints antes de registrar: %s", list(app.blueprints.keys()))

    # El nombre interno del blueprint de auth podría ser "auth_api" (recomendado) o "auth" (si no lo cambiaste).
    if "auth_api" not in app.blueprints and "auth" not in app.blueprints:
        app.register_blueprint(bp_auth)
    else:
        app.logger.info("Blueprint 'auth' ya estaba registrado; se omite registro duplicado.")

    if "recepciones" not in app.blueprints:
        app.register_blueprint(bp_recepciones)
    else:
        app.logger.info("Blueprint 'recepciones' ya estaba registrado; se omite registro duplicado.")

    # -----------------------------------------------------------------
    # Manejador de errores homogéneo
    # -----------------------------------------------------------------
    @app.errorhandler(Exception)
    def handle_unexpected(e: Exception):
        app.logger.exception("Unhandled error: %s", e)
        code = getattr(e, "code", 500) or 500
        # No exponemos traceback al cliente; solo code/message
        return jsonify({"error": {"code": "UNEXPECTED_ERROR", "message": str(e)}}), int(code)

    return app


# Objeto WSGI para gunicorn (wsgi:app)
app = create_app()

if __name__ == "__main__":
    # Ejecutar localmente
    port = int(os.getenv("PORT", "5000"))
    # Evita reloader duplicando imports si pones FLASK_DEBUG=0 (recomendado)
    debug_env = os.getenv("FLASK_DEBUG", "0")
    debug = False if debug_env in ("0", "false", "False") else True
    app.run(host="0.0.0.0", port=port, debug=debug)
