"""Horario de atención del proveedor (RN-08, RF-04)."""
from flask import Blueprint, abort, render_template, request, redirect, url_for, flash

from disponibilidad import parsear_hora
from utilidades import exigir_proveedor, cliente_sesion, primero, proveedor_propio, mensaje_error

horarios_bp = Blueprint("horarios", __name__)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _negocio(sb_usuario):
    negocio = proveedor_propio(sb_usuario)
    if negocio is None:
        flash("Primero registra los datos de tu negocio.", "error")
    return negocio


@horarios_bp.route("/mis-horarios")
@exigir_proveedor
def gestionar():
    sb_usuario = cliente_sesion()
    negocio = _negocio(sb_usuario)
    if negocio is None:
        return redirect(url_for("profesionales.registro"))

    horarios = sb_usuario.table("horarios_profesional").select("*").eq(
        "profesional_id", negocio["id"]).order("dia_semana").order("hora_inicio").execute()
    return render_template("horarios/gestionar.html", horarios=horarios.data, dias=DIAS)


@horarios_bp.route("/mis-horarios/nuevo", methods=["POST"])
@exigir_proveedor
def nuevo():
    sb_usuario = cliente_sesion()
    negocio = _negocio(sb_usuario)
    if negocio is None:
        return redirect(url_for("profesionales.registro"))

    try:
        dia = int(request.form.get("dia_semana", ""))
    except ValueError:
        dia = -1
    inicio = parsear_hora(request.form.get("hora_inicio", "").strip())
    fin = parsear_hora(request.form.get("hora_fin", "").strip())

    if not 0 <= dia <= 6 or inicio is None or fin is None:
        flash("Completa día, hora de inicio y hora de fin.", "error")
        return redirect(url_for("horarios.gestionar"))
    if fin <= inicio:   # se comparan horas reales, no texto
        flash("La hora de fin debe ser posterior a la de inicio.", "error")
        return redirect(url_for("horarios.gestionar"))

    existentes = sb_usuario.table("horarios_profesional").select("hora_inicio, hora_fin").eq(
        "profesional_id", negocio["id"]).eq("dia_semana", dia).execute().data
    for e in existentes:
        e_ini, e_fin = parsear_hora(e["hora_inicio"][:5]), parsear_hora(e["hora_fin"][:5])
        if inicio < e_fin and fin > e_ini:
            flash("Esa franja se cruza con otra que ya tienes ese día.", "error")
            return redirect(url_for("horarios.gestionar"))

    try:
        sb_usuario.table("horarios_profesional").insert({
            "profesional_id": negocio["id"],
            "dia_semana": dia,
            "hora_inicio": inicio.strftime("%H:%M"),
            "hora_fin": fin.strftime("%H:%M"),
        }).execute()
    except Exception as error:
        flash(mensaje_error(error, "No se pudo guardar la franja horaria."), "error")
        return redirect(url_for("horarios.gestionar"))

    flash("Franja horaria agregada.", "success")
    return redirect(url_for("horarios.gestionar"))


@horarios_bp.route("/mis-horarios/<horario_id>/eliminar", methods=["POST"])
@exigir_proveedor
def eliminar(horario_id):
    sb_usuario = cliente_sesion()
    negocio = _negocio(sb_usuario)
    if negocio is None:
        return redirect(url_for("profesionales.registro"))

    fila = primero(sb_usuario.table("horarios_profesional").select("id").eq(
        "id", horario_id).eq("profesional_id", negocio["id"]).limit(1).execute())
    if fila is None:
        abort(404)

    try:
        sb_usuario.table("horarios_profesional").delete().eq("id", horario_id).eq(
            "profesional_id", negocio["id"]).execute()
    except Exception as error:
        flash(mensaje_error(error), "error")
        return redirect(url_for("horarios.gestionar"))

    flash("Franja horaria eliminada.", "success")
    return redirect(url_for("horarios.gestionar"))
