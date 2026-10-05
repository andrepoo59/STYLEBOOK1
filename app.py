"""StyleBook - Sprint 1"""
import logging
import os
import secrets

from dotenv import load_dotenv
from flask import Flask, abort, render_template, request, session

load_dotenv()

from blueprints.auth import auth_bp
from blueprints.perfil import perfil_bp
from blueprints.profesionales import profesionales_bp
from blueprints.servicios import servicios_bp
from blueprints.admin import admin_bp
from blueprints.horarios import horarios_bp
from blueprints.citas import citas_bp

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

_clave = os.environ.get("FLASK_SECRET_KEY")
if not _clave:
    # Nunca se usa una clave conocida: si falta, se genera una al azar (las sesiones
    # se pierden al reiniciar). Define FLASK_SECRET_KEY en .env para que sean estables.
    _clave = secrets.token_hex(32)
    app.logger.warning("FLASK_SECRET_KEY no está definida; se generó una temporal.")
app.secret_key = _clave

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",  # =1 en producción (HTTPS)
)

for bp in (auth_bp, perfil_bp, profesionales_bp, servicios_bp, admin_bp, horarios_bp, citas_bp):
    app.register_blueprint(bp)


# ---------- Protección CSRF para todo formulario POST ----------
def _token_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


@app.context_processor
def _inyectar_csrf():
    return {"csrf_token": _token_csrf}


@app.before_request
def _verificar_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        enviado = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        esperado = session.get("csrf_token", "")
        if not esperado or not secrets.compare_digest(enviado.encode(), esperado.encode()):
            abort(400, description="La sesión del formulario expiró. Recarga la página e inténtalo de nuevo.")


# ---------- Páginas de error ----------
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def _error_cliente(error):
    textos = {
        403: "No tienes permiso para ver esta página.",
        404: "No encontramos lo que buscas.",
    }
    mensaje = textos.get(error.code) or getattr(error, "description", "Solicitud no válida.")
    return render_template("error.html", codigo=error.code, mensaje=mensaje), error.code


@app.errorhandler(500)
def _error_servidor(error):
    return render_template("error.html", codigo=500,
                           mensaje="Algo salió mal de nuestro lado. Inténtalo de nuevo en un momento."), 500


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
