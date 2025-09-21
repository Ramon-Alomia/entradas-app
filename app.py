# app.py
import os
import logging
from datetime import timedelta

from flask import Flask, jsonify, render_template
from flask_cors import CORS
from flask_talisman import Talisman
from dotenv import load_dotenv

# Blueprints (asegúrate de que existan estos módulos)
from auth import bp_auth               # url_prefix="/api"
from recepciones_api import bp_recepciones  # url_prefix="/api"
from admin import bp_admin_api, bp_admin_ui  # "/api/admin" y "/admin"

# -----------------------------------------------------------------------------
# Config helpers
# -----------------------------------------------------------------------------

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
    # Secret key para sesiones CSRF en vistas Jinja (no se usa en JWT)
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY no está configurada (env)")
    app.secret_key = secret

    # Cookies seguras (ya que desplegamos en HTTPS en Render)
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        JSON_SORT_KEYS=False,
    )

def _set_security_headers(app: Flask):
    # Content Security Policy (ajusta si sirves JS de otros orígenes)
    csp = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "cdnjs.cloudflare.com"],
        "style-src": ["'self'", "'unsafe-inline'", "cdnjs.cloudflare.com", "fonts.googleapis.com"],
        "font-src": ["'self'", "fonts.gstatic.com"],
        "img-src": ["'self'", "data:"],
    }
    Talisman(
        app,
        content_security_policy=csp,
        force_https=True,
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
    )

def _enable_cors(app: Flask):
    origins = _get_allowed_origins()
    if not origins:
        # Por defecto: mismo origen; si necesitas abrir, usa CORS_ORIGINS en .env
        return
    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        supports_credentials=False,
        methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=86400,
    )

# -----------------------------------------------------------------------------
# App factory
# -----------------------------------------------------------------------------

def create_app() -> Flask:
    # Carga .env solo en entorno local (Render ya inyecta env)
    if not os.getenv("RENDER"):
        load_dotenv()

    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )

    # Logging básico
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    app.logger.setLevel(logging.getLogger().level)

    # Configuración base
    _make_app_config(app)

    # Seguridad (CSP/HSTS)
    _set_security_headers(app)

    # CORS para /api/*
    _enable_cors(app)

    # ---------------- Health / Ping ----------------
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/ping")
    def ping():
        return "pong", 200

    # ---------------- Error handlers homogéneos ----------------
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
    app.register_blueprint(bp_auth)
    app.register_blueprint(bp_recepciones)
    app.register_blueprint(bp_admin_api)
    app.register_blueprint(bp_admin_ui)

    # ---------------- Ruta raíz (login) ----------------
    @app.get("/")
    def index_page():
        # Sirve la página de login (templates/index.html)
        return render_template("index.html")

    app.logger.info("App lista. CORS_ORIGINS=%s", _get_allowed_origins())
    return app

# App global (para Gunicorn con wsgi:app)
app = create_app()

# Dev server
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)