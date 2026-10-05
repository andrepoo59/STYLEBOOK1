"""
Utilidades compartidas: sesión, roles, validaciones, redirección segura y
traducción de errores técnicos a mensajes para el usuario.
"""
import logging
import re
from functools import wraps
from urllib.parse import urlparse

from flask import session, redirect, url_for, request, abort

from supabase_client import sb_como_usuario

log = logging.getLogger("stylebook")

# RN-02: tipos de cuenta. "admin" es interno (se asigna desde la base de datos).
ROLES_PROVEEDOR = ("establecimiento", "profesional")

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RE_TELEFONO = re.compile(r"^\+?[0-9][0-9\s().-]{6,19}$")


# ---------- Sesión y roles (RF-02, RN-03) ----------

def _a_login():
    return redirect(url_for("auth.login", volver=request.full_path.rstrip("?")))


def exigir_sesion(vista):
    """Redirige a /login si no hay sesión activa."""
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "access_token" not in session:
            return _a_login()
        return vista(*args, **kwargs)
    return envoltura


def exigir_rol(*roles):
    """Exige sesión activa y que el tipo de cuenta esté entre los permitidos (RN-03)."""
    def decorador(vista):
        @wraps(vista)
        def envoltura(*args, **kwargs):
            if "access_token" not in session:
                return _a_login()
            if session.get("rol") not in roles:
                abort(403)
            return vista(*args, **kwargs)
        return envoltura
    return decorador


exigir_admin = exigir_rol("admin")
exigir_cliente = exigir_rol("cliente")
exigir_proveedor = exigir_rol(*ROLES_PROVEEDOR)


def cliente_sesion():
    """Cliente de Supabase autenticado como el usuario actual (para que apliquen las RLS)."""
    return sb_como_usuario(session["access_token"], session["refresh_token"])


# ---------- Consultas ----------

def primero(respuesta):
    """Primera fila de una respuesta de Supabase o None (evita maybe_single, que según
    la versión devuelve None en vez de una respuesta vacía)."""
    datos = respuesta.data
    return datos[0] if datos else None


def proveedor_propio(sb_usuario):
    """Fila de `profesionales` del usuario en sesión, o None si aún no registró su negocio."""
    return primero(
        sb_usuario.table("profesionales").select("*")
        .eq("perfil_id", session["user_id"]).limit(1).execute()
    )


# ---------- Validaciones (RN-04) ----------

def correo_valido(correo: str) -> bool:
    return bool(_RE_EMAIL.match(correo or "")) and len(correo) <= 254


def telefono_valido(telefono: str) -> bool:
    """El teléfono es opcional; si viene, debe tener un formato razonable."""
    return not telefono or bool(_RE_TELEFONO.match(telefono))


def destino_seguro(destino, por_defecto=None):
    """Solo acepta rutas internas ('/algo'); evita open redirect con //host, http://… o \\."""
    if not destino or not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return por_defecto
    partes = urlparse(destino)
    if partes.scheme or partes.netloc:
        return por_defecto
    return destino


# ---------- Errores ----------

def mensaje_error(error, generico="No se pudo completar la operación. Inténtalo de nuevo."):
    """Convierte una excepción de Supabase/Postgres en un mensaje entendible.
    El detalle técnico va al log, nunca a la pantalla del usuario."""
    log.warning("Error de Supabase: %r", error)
    texto = str(getattr(error, "message", "") or error)
    codigo = str(getattr(error, "code", "") or "")
    bajo = texto.lower()

    # Mensajes escritos a propósito en los triggers de schema.sql ("SB: ...")
    if "SB: " in texto:
        return texto.split("SB: ", 1)[1].strip().strip("'\"}").rstrip()
    if codigo == "23P01" or "sin_cruces_horario" in bajo:
        return "Ese horario acaba de ser ocupado por otra reserva. Elige otro."
    if codigo == "23505" or "already registered" in bajo or "already been registered" in bajo:
        return "Ya existe un registro con esos datos."
    if codigo == "42501" or "row-level security" in bajo:
        return "No tienes permiso para realizar esta acción."
    if "email not confirmed" in bajo:
        return "Debes confirmar tu correo antes de iniciar sesión."
    if "rate limit" in bajo or "too many" in bajo:
        return "Demasiados intentos. Espera unos minutos e inténtalo de nuevo."
    if "password" in bajo and "at least" in bajo:
        return "La contraseña es demasiado corta."
    return generico
