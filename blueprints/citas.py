"""
Reservas: disponibilidad real (RN-08/09), reserva (RN-10/11/12), estados (RN-13),
cancelar y reprogramar (cliente) y agenda del proveedor (confirmar, completar, cancelar).
"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, render_template, request, redirect, url_for, session, flash

from supabase_client import sb
from blueprints.profesionales import proveedor_aprobado, DIAS
from disponibilidad import (
    MAX_DIAS_ADELANTE, a_time, ahora_local, error_de_fecha, franjas_por_dia,
    parsear_fecha, parsear_hora, sumar_minutos,
)
from utilidades import (
    exigir_cliente, exigir_proveedor, cliente_sesion, mensaje_error, primero, proveedor_propio,
)

citas_bp = Blueprint("citas", __name__)

ESTADOS_ACTIVOS = ("pendiente", "confirmada")
# RN-13: transiciones válidas (la base de datos también las hace cumplir).
TRANSICIONES = {"pendiente": {"confirmada", "cancelada"}, "confirmada": {"completada", "cancelada"}}


# ---------- Ayudas ----------

def _libres_del_dia(profesional_id, fecha, duracion_min, excluir=None):
    """Horas realmente libres ese día para un servicio de `duracion_min` minutos."""
    return franjas_por_dia(sb, profesional_id, fecha, fecha, duracion_min,
                           excluir_cita=excluir, ahora=ahora_local())[fecha]


def _preparar(cita, ahora):
    """Agrega campos listos para mostrar (evita cortar textos en las plantillas)."""
    fecha = parsear_fecha(cita["fecha"])
    inicio, fin = a_time(cita["hora_inicio"]), a_time(cita["hora_fin"])
    cita["inicio_dt"] = datetime.combine(fecha, inicio)
    cita["fecha_txt"] = f"{DIAS[fecha.weekday()]} {fecha:%d/%m/%Y}"
    cita["hora_txt"] = f"{inicio:%H:%M} – {fin:%H:%M}"
    cita["duracion"] = int((datetime.combine(fecha, fin) - cita["inicio_dt"]).total_seconds() // 60)
    cita["ya_empezo"] = cita["inicio_dt"] <= ahora
    cita["activa"] = cita["estado"] in ESTADOS_ACTIVOS
    return cita


def _separar(citas):
    """(próximas activas por fecha ascendente, historial por fecha descendente)."""
    proximas = sorted((c for c in citas if c["activa"] and not c["ya_empezo"]),
                      key=lambda c: c["inicio_dt"])
    historial = sorted((c for c in citas if not (c["activa"] and not c["ya_empezo"])),
                       key=lambda c: c["inicio_dt"], reverse=True)
    return proximas, historial


def _rango_fechas():
    hoy = ahora_local().date()
    return hoy.isoformat(), (hoy + timedelta(days=MAX_DIAS_ADELANTE)).isoformat()


# ---------- Reservar (cliente) ----------

def _vista_reservar(profesional, servicios, fecha="", servicio_id="", libres=None):
    minimo, maximo = _rango_fechas()
    return render_template(
        "citas/reservar.html", profesional=profesional, servicios=servicios, fecha=fecha,
        servicio_id=servicio_id, horarios_libres=libres or [], fecha_min=minimo, fecha_max=maximo,
    )


@citas_bp.route("/profesionales/<profesional_id>/reservar", methods=["GET", "POST"])
@exigir_cliente
def reservar(profesional_id):
    profesional = proveedor_aprobado(profesional_id)   # RN: solo proveedores aprobados
    if profesional is None:
        abort(404)

    servicios = sb.table("servicios").select("*").eq("profesional_id", profesional_id).eq(
        "activo", True).order("nombre").execute().data

    if request.method == "GET":
        return _vista_reservar(profesional, servicios, servicio_id=request.args.get("servicio_id", ""))

    servicio_id = request.form.get("servicio_id", "")
    fecha_str = request.form.get("fecha", "")
    hora_str = request.form.get("hora_inicio", "")

    def volver(mensaje, libres=None):
        flash(mensaje, "error")
        return _vista_reservar(profesional, servicios, fecha_str, servicio_id, libres)

    servicio = next((s for s in servicios if s["id"] == servicio_id), None)
    fecha, hora = parsear_fecha(fecha_str), parsear_hora(hora_str)
    if not servicio or not fecha or not hora:
        return volver("Selecciona un servicio, una fecha y un horario válidos.")

    problema = error_de_fecha(fecha)
    if problema:
        return volver(problema)

    try:
        libres = _libres_del_dia(profesional_id, fecha, servicio["duracion_min"])
    except Exception as error:
        return volver(mensaje_error(error, "No se pudo consultar la disponibilidad."))

    if hora_str not in libres:   # RN-08/09: solo horas realmente libres
        return volver("Ese horario ya no está disponible. Elige otro.", libres)

    try:
        sb_usuario = cliente_sesion()
        sb_usuario.table("citas").insert({
            "cliente_id": session["user_id"],
            "cliente_nombre": session.get("nombre"),
            "profesional_id": profesional_id,
            "servicio_id": servicio_id,
            "fecha": fecha.isoformat(),
            "hora_inicio": hora.strftime("%H:%M"),
            "hora_fin": sumar_minutos(hora, servicio["duracion_min"]).strftime("%H:%M"),
        }).execute()
    except Exception as error:
        # RN-10: si dos personas reservan a la vez, la restricción de la base rechaza a la segunda.
        try:
            libres = _libres_del_dia(profesional_id, fecha, servicio["duracion_min"])
        except Exception:
            libres = []
        return volver(mensaje_error(error, "No se pudo reservar la cita."), libres)

    flash("¡Cita reservada con éxito!", "success")
    return redirect(url_for("citas.mis_citas"))


@citas_bp.route("/profesionales/<profesional_id>/horarios-disponibles")
def horarios_disponibles(profesional_id):
    """Endpoint AJAX: horas libres al cambiar fecha o servicio, sin recargar la página."""
    fecha = parsear_fecha(request.args.get("fecha", ""))
    servicio_id = request.args.get("servicio_id", "")
    if fecha is None or not servicio_id:
        return jsonify({"horarios": []})

    problema = error_de_fecha(fecha)
    if problema:
        return jsonify({"horarios": [], "aviso": problema})

    servicio = primero(sb.table("servicios").select("duracion_min").eq("id", servicio_id).eq(
        "profesional_id", profesional_id).eq("activo", True).limit(1).execute())
    if servicio is None or proveedor_aprobado(profesional_id) is None:
        return jsonify({"horarios": []})

    try:
        libres = _libres_del_dia(profesional_id, fecha, servicio["duracion_min"],
                                 excluir=request.args.get("excluir") or None)
    except Exception as error:
        return jsonify({"horarios": [], "aviso": mensaje_error(error)}), 200
    return jsonify({"horarios": libres})


# ---------- Mis citas (cliente): listar, cancelar, reprogramar ----------

@citas_bp.route("/mis-citas")
@exigir_cliente
def mis_citas():
    sb_usuario = cliente_sesion()
    filas = sb_usuario.table("citas").select(
        "*, profesionales(id, nombre_negocio, especialidad, ubicacion), servicios(nombre, precio)"
    ).eq("cliente_id", session["user_id"]).execute().data
    ahora = ahora_local()
    proximas, historial = _separar([_preparar(c, ahora) for c in filas])
    return render_template("citas/listar.html", proximas=proximas, historial=historial)


def _cita_propia(sb_usuario, cita_id):
    return primero(sb_usuario.table("citas").select(
        "*, profesionales(id, nombre_negocio), servicios(nombre)"
    ).eq("id", cita_id).eq("cliente_id", session["user_id"]).limit(1).execute())


@citas_bp.route("/mis-citas/<cita_id>/cancelar", methods=["POST"])
@exigir_cliente
def cancelar(cita_id):
    sb_usuario = cliente_sesion()
    cita = _cita_propia(sb_usuario, cita_id)
    if cita is None:
        abort(404)
    _preparar(cita, ahora_local())

    if not cita["activa"]:
        flash("Esta cita ya no se puede cancelar.", "error")
    elif cita["ya_empezo"]:
        flash("No puedes cancelar una cita que ya empezó.", "error")
    else:
        try:
            sb_usuario.table("citas").update({"estado": "cancelada"}).eq("id", cita_id).eq(
                "cliente_id", session["user_id"]).execute()
            flash("Cita cancelada. El horario quedó disponible nuevamente.", "success")
        except Exception as error:
            flash(mensaje_error(error), "error")
    return redirect(url_for("citas.mis_citas"))


@citas_bp.route("/mis-citas/<cita_id>/reprogramar", methods=["GET", "POST"])
@exigir_cliente
def reprogramar(cita_id):
    """RN-08/09/10: la nueva hora debe estar dentro del horario del proveedor y realmente libre."""
    sb_usuario = cliente_sesion()
    cita = _cita_propia(sb_usuario, cita_id)
    if cita is None:
        abort(404)
    _preparar(cita, ahora_local())

    if not cita["activa"] or cita["ya_empezo"]:
        flash("Esta cita ya no se puede reprogramar.", "error")
        return redirect(url_for("citas.mis_citas"))

    minimo, maximo = _rango_fechas()

    def vista(fecha=""):
        return render_template("citas/reprogramar.html", cita=cita, fecha=fecha,
                               fecha_min=minimo, fecha_max=maximo)

    if request.method == "GET":
        return vista()

    fecha_str = request.form.get("fecha", "")
    hora_str = request.form.get("hora_inicio", "")
    fecha, hora = parsear_fecha(fecha_str), parsear_hora(hora_str)
    if not fecha or not hora:
        flash("Elige una fecha y un horario disponible.", "error")
        return vista(fecha_str)

    problema = error_de_fecha(fecha)
    if problema:
        flash(problema, "error")
        return vista(fecha_str)

    try:
        # excluir=cita_id: la propia cita no bloquea su nuevo horario
        libres = _libres_del_dia(cita["profesional_id"], fecha, cita["duracion"], excluir=cita_id)
    except Exception as error:
        flash(mensaje_error(error, "No se pudo consultar la disponibilidad."), "error")
        return vista(fecha_str)

    if hora_str not in libres:
        flash("Ese horario no está disponible. Elige otro.", "error")
        return vista(fecha_str)

    try:
        sb_usuario.table("citas").update({
            "fecha": fecha.isoformat(),
            "hora_inicio": hora.strftime("%H:%M"),
            "hora_fin": sumar_minutos(hora, cita["duracion"]).strftime("%H:%M"),
        }).eq("id", cita_id).eq("cliente_id", session["user_id"]).execute()
    except Exception as error:
        flash(mensaje_error(error, "No se pudo reprogramar la cita."), "error")
        return vista(fecha_str)

    flash("Cita reprogramada correctamente.", "success")
    return redirect(url_for("citas.mis_citas"))


# ---------- Agenda del proveedor: confirmar, completar, cancelar (RN-13) ----------

@citas_bp.route("/mi-agenda")
@exigir_proveedor
def agenda():
    sb_usuario = cliente_sesion()
    negocio = proveedor_propio(sb_usuario)
    if negocio is None:
        flash("Primero registra los datos de tu negocio.", "error")
        return redirect(url_for("profesionales.registro"))

    filas = sb_usuario.table("citas").select("*, servicios(nombre, precio)").eq(
        "profesional_id", negocio["id"]).execute().data
    ahora = ahora_local()
    citas = [_preparar(c, ahora) for c in filas]
    # En la agenda "pendientes de atender" incluye citas activas que ya empezaron (para completarlas).
    por_atender = sorted((c for c in citas if c["activa"]), key=lambda c: c["inicio_dt"])
    historial = sorted((c for c in citas if not c["activa"]), key=lambda c: c["inicio_dt"], reverse=True)
    return render_template("citas/agenda.html", por_atender=por_atender, historial=historial)


@citas_bp.route("/mi-agenda/<cita_id>/estado", methods=["POST"])
@exigir_proveedor
def agenda_estado(cita_id):
    sb_usuario = cliente_sesion()
    negocio = proveedor_propio(sb_usuario)
    if negocio is None:
        return redirect(url_for("profesionales.registro"))

    cita = primero(sb_usuario.table("citas").select("*").eq("id", cita_id).eq(
        "profesional_id", negocio["id"]).limit(1).execute())
    if cita is None:
        abort(404)
    _preparar(cita, ahora_local())

    nuevo = request.form.get("estado", "")
    if nuevo not in TRANSICIONES.get(cita["estado"], set()):
        flash("Ese cambio de estado no es válido para esta cita.", "error")
    elif nuevo == "completada" and not cita["ya_empezo"]:
        flash("Solo puedes completar una cita que ya empezó.", "error")
    else:
        try:
            sb_usuario.table("citas").update({"estado": nuevo}).eq("id", cita_id).eq(
                "profesional_id", negocio["id"]).execute()
            flash(f"Cita marcada como {nuevo}.", "success")
        except Exception as error:
            flash(mensaje_error(error), "error")
    return redirect(url_for("citas.agenda"))
