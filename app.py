# app.py
import os
import logging
from datetime import timedelta

from flask import Flask, jsonify, render_template
from flask_cors import CORS
from flask_talisman import Talisman
from dotenv import load_dotenv

# Blueprints
from auth import bp_auth                       # /login, /logout, /me
from recepciones_api import bp_recepciones     # /orders, /receipts, ...
from admin import bp_admin_api, bp_admin_ui    # /admin (UI) y /admin/* (JSON)

# -----------------------------------------------------------------------------#
# Config helpers
# -----------------------------------------------------------------------------#

def _get_allowed_origins():
    """
    CORS_ORIGINS en .env puede ser:
      - "*"                    (no recomendado en prod)
      - "https://miapp.com"
      - "https://a.com,https://b.com"
    """
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]

def _make_app_config(app: Flask):
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY no está configurada (env)")
    app.secret_key = secret

    # Cookies seguras (Render usa HTTPS)
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        JSON_SORT_KEYS=False,
    )

def _set_security_headers(app: Flask):
    # Content Security Policy (ajústala si sirves activos externos)
    csp = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "cdnjs.cloudflare.com"],
        "style-src": ["'self'", "'unsafe-inline'", "cdnjs.cloudflare.com", "fonts.googleapis.com"],
        "font-src": ["'self'", "fonts.gstatic.com"],
        "img-src": ["'self'", "data:"],
    }
    force_https = bool(os.getenv("RENDER")) and not app.config.get("TESTING", False)
    Talisman(
        app,
        content_security_policy=csp,
        force_https=force_https,
        strict_transport_security=force_https,
        strict_transport_security_max_age=31536000,
    )

def _enable_cors(app: Flask):
    origins = _get_allowed_origins()
    if not origins:
        # Mismo origen por defecto (no abrimos CORS si no se especifica)
        return
    CORS(
        app,
        resources={
            r"/admin/.*": {"origins": origins},
            r"/orders(?:/.*)?": {"origins": origins},
            r"/receipts": {"origins": origins},
            r"/login": {"origins": origins},
            r"/logout": {"origins": origins},
            r"/me": {"origins": origins},
        },
        supports_credentials=False,
        methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=86400,
    )

# -----------------------------------------------------------------------------#
# App factory
# -----------------------------------------------------------------------------#

def create_app() -> Flask:
    # En local cargamos .env (en Render ya inyecta envs)
    if not os.getenv("RENDER"):
        load_dotenv()

    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )

    if os.getenv("PYTEST_CURRENT_TEST"):
        app.config["TESTING"] = True

    if app.config.get("TESTING"):
        app.testing = True

    # Logging básico
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    app.logger.setLevel(logging.getLogger().level)

    # Config base + seguridad
    _make_app_config(app)
    _set_security_headers(app)
    _enable_cors(app)

    # ---------------- Health / Ping ----------------
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/ping")
    def ping():
        return "pong", 200

    # ---------------- Errores homogéneos ----------------
    @app.errorhandler(400)
    def _bad_request(e):
        return jsonify({"error": {"code": "BAD_REQUEST", "message": str(e)}}), 400

    @app.errorhandler(401)
    def _unauth(e):
        return jsonify({"error": {"code": "UNAUTHORIZED", "message": "No autorizado"}}), 401

    @app.errorhandler(403)
    def _forbidden(e):
        return jsonify({"error": {"code": "FORBIDDEN", "message": "Prohibido"}}), 403

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": {"code": "NOT_FOUND", "message": "No encontrado"}}), 404

    @app.errorhandler(Exception)
    def _unhandled(e):
        app.logger.exception("Unhandled error: %s", e)
        return jsonify({"error": {"code": "UNEXPECTED_ERROR", "message": str(e)}}), 500

    # ---------------- Blueprints ----------------
    app.register_blueprint(bp_auth)          # /login, /logout, /me
    app.register_blueprint(bp_recepciones)   # /orders, /receipts, ...
    app.register_blueprint(bp_admin_api)     # /admin/* JSON
    app.register_blueprint(bp_admin_ui)      # /admin UI

    # ---------------- Raíz: sirve la SPA principal ----------------
    @app.get("/")
    def root():
        return render_template("index.html")

    app.logger.info("App lista. CORS_ORIGINS=%s", _get_allowed_origins())
    return app

# App global para Gunicorn (wsgi:app)
app = create_app()

# Dev server
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)
