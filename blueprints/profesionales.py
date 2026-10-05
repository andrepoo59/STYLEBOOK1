"""
RF-04 Registro de proveedor (establecimiento o profesional independiente)
RF-07 Consultar proveedor (perfil público: servicios, precios, horarios, ubicación, disponibilidad)
"""
from datetime import timedelta

from flask import Blueprint, abort, render_template, request, redirect, url_for, session, flash

from supabase_client import sb
from disponibilidad import ahora_local, franjas_por_dia
from utilidades import (
    exigir_proveedor, cliente_sesion, primero, proveedor_propio,
    telefono_valido, mensaje_error,
)

profesionales_bp = Blueprint("profesionales", __name__)

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_VISTA_PREVIA = 7   # días de disponibilidad que se muestran en el perfil


def proveedor_aprobado(profesional_id):
    """Fila del proveedor si existe y está aprobado; si no, None."""
    return primero(sb.table("profesionales").select("*")
                   .eq("id", profesional_id).eq("estado", "aprobado").limit(1).execute())


@profesionales_bp.route("/profesionales")
def listar():
    """RF-07: cualquiera puede consultar la lista de proveedores aprobados."""
    respuesta = sb.table("profesionales").select("*").eq(
        "estado", "aprobado").order("creado_en").execute()
    return render_template("profesionales/listar.html", profesionales=respuesta.data)


@profesionales_bp.route("/profesionales/<profesional_id>")
def detalle(profesional_id):
    """RF-07: perfil público con servicios, precios, horarios, ubicación y disponibilidad."""
    profesional = proveedor_aprobado(profesional_id)
    if profesional is None:
        abort(404)

    servicios = sb.table("servicios").select("*").eq(
        "profesional_id", profesional_id).eq("activo", True).order("nombre").execute().data
    horarios = sb.table("horarios_profesional").select("*").eq(
        "profesional_id", profesional_id).order("dia_semana").order("hora_inicio").execute().data

    disponibilidad, duracion_ref = [], None
    if servicios and horarios:
        duracion_ref = min(s["duracion_min"] for s in servicios)
        ahora = ahora_local()
        try:
            por_dia = franjas_por_dia(
                sb, profesional_id, ahora.date(),
                ahora.date() + timedelta(days=DIAS_VISTA_PREVIA - 1), duracion_ref, ahora=ahora)
            disponibilidad = [
                {"fecha": f, "dia": DIAS[f.weekday()], "horas": h}
                for f, h in por_dia.items() if h
            ]
        except Exception as error:
            mensaje_error(error)   # queda en el log; la página se muestra sin vista previa

    return render_template(
        "profesionales/detalle.html", profesional=profesional, servicios=servicios,
        horarios=horarios, dias=DIAS, disponibilidad=disponibilidad, duracion_ref=duracion_ref,
    )


def _leer_negocio():
    """Lee y valida el formulario del negocio. Devuelve (datos, error)."""
    nombre_negocio = request.form.get("nombre_negocio", "").strip()
    especialidad = request.form.get("especialidad", "").strip()
    descripcion = request.form.get("descripcion", "").strip()
    ubicacion = request.form.get("ubicacion", "").strip()
    telefono = request.form.get("telefono", "").strip()

    if not nombre_negocio or not especialidad or not ubicacion or not telefono:
        return None, "Nombre, especialidad, ubicación y teléfono de contacto son obligatorios."
    if len(nombre_negocio) > 100 or len(especialidad) > 100 or len(ubicacion) > 200:
        return None, "Alguno de los textos es demasiado largo."
    if len(descripcion) > 1000:
        return None, "La descripción no puede superar 1000 caracteres."
    if not telefono_valido(telefono):
        return None, "El teléfono no es válido (solo números, espacios, + y guiones)."
    try:
        anios = int(request.form.get("anios_experiencia") or 0)
    except ValueError:
        return None, "Los años de experiencia deben ser un número."
    if not 0 <= anios <= 80:
        return None, "Los años de experiencia deben estar entre 0 y 80."

    return {
        "nombre_negocio": nombre_negocio, "especialidad": especialidad,
        "descripcion": descripcion, "ubicacion": ubicacion,
        "telefono": telefono, "anios_experiencia": anios,
    }, None


@profesionales_bp.route("/mi-negocio/registro", methods=["GET", "POST"])
@exigir_proveedor
def registro():
    """RF-04: el establecimiento o profesional independiente registra su negocio."""
    sb_usuario = cliente_sesion()
    if proveedor_propio(sb_usuario) is not None:
        return redirect(url_for("profesionales.editar_negocio"))

    if request.method == "GET":
        return render_template("profesionales/registro.html", negocio=None)

    datos, error = _leer_negocio()
    if error:
        flash(error, "error")
        return render_template("profesionales/registro.html", negocio=request.form)

    datos["perfil_id"] = session["user_id"]
    datos["tipo_proveedor"] = "establecimiento" if session["rol"] == "establecimiento" else "independiente"
    try:
        sb_usuario.table("profesionales").insert(datos).execute()
    except Exception as e:
        flash(mensaje_error(e, "No se pudo registrar tu negocio."), "error")
        return render_template("profesionales/registro.html", negocio=request.form)

    flash("Negocio registrado. Un administrador lo revisará antes de publicarlo. "
          "Mientras tanto ya puedes cargar servicios y horarios.", "success")
    return redirect(url_for("servicios.gestionar"))


@profesionales_bp.route("/mi-negocio", methods=["GET", "POST"])
@exigir_proveedor
def editar_negocio():
    """RF-03/RF-19: el proveedor mantiene actualizados los datos de su negocio."""
    sb_usuario = cliente_sesion()
    negocio = proveedor_propio(sb_usuario)
    if negocio is None:
        return redirect(url_for("profesionales.registro"))

    if request.method == "GET":
        return render_template("profesionales/registro.html", negocio=negocio)

    datos, error = _leer_negocio()
    if error:
        flash(error, "error")
        return render_template("profesionales/registro.html", negocio=request.form)

    try:
        sb_usuario.table("profesionales").update(datos).eq("id", negocio["id"]).execute()
    except Exception as e:
        flash(mensaje_error(e), "error")
        return render_template("profesionales/registro.html", negocio=request.form)

    flash("Datos del negocio actualizados.", "success")
    return redirect(url_for("perfil.ver"))
