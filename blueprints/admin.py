"""
Panel de administrador: aprobar o rechazar el registro de proveedores
antes de que aparezcan públicamente.
"""
from flask import Blueprint, abort, render_template, redirect, url_for, flash

from utilidades import exigir_admin, cliente_sesion, mensaje_error

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@exigir_admin
def panel():
    sb_admin = cliente_sesion()

    pendientes = sb_admin.table("profesionales").select("*").eq(
        "estado", "pendiente").order("creado_en").execute()
    aprobados = sb_admin.table("profesionales").select("id", count="exact").eq(
        "estado", "aprobado").execute()
    servicios = sb_admin.table("servicios").select("id", count="exact").eq("activo", True).execute()

    return render_template(
        "admin/panel.html",
        pendientes=pendientes.data,
        total_aprobados=aprobados.count or 0,
        total_servicios=servicios.count or 0,
    )


def _cambiar_estado(profesional_id, estado, mensaje_ok):
    sb_admin = cliente_sesion()
    try:
        resultado = sb_admin.table("profesionales").update({"estado": estado}).eq(
            "id", profesional_id).execute()
    except Exception as error:
        flash(mensaje_error(error), "error")
        return redirect(url_for("admin.panel"))
    if not resultado.data:
        abort(404)
    flash(mensaje_ok, "success")
    return redirect(url_for("admin.panel"))


@admin_bp.route("/profesionales/<profesional_id>/aprobar", methods=["POST"])
@exigir_admin
def aprobar(profesional_id):
    return _cambiar_estado(profesional_id, "aprobado",
                           "Proveedor aprobado. Ya aparece en el listado público.")


@admin_bp.route("/profesionales/<profesional_id>/rechazar", methods=["POST"])
@exigir_admin
def rechazar(profesional_id):
    return _cambiar_estado(profesional_id, "rechazado", "Registro de proveedor rechazado.")
