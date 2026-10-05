"""RF-05 Gestión de servicios (crear, editar, activar/desactivar los propios)
RF-06 Consultar servicios (catálogo público)"""
from flask import Blueprint, abort, render_template, request, redirect, url_for, flash

from supabase_client import sb
from utilidades import exigir_proveedor, cliente_sesion, primero, proveedor_propio, mensaje_error

servicios_bp = Blueprint("servicios", __name__)

CATEGORIAS = ["cortes", "color", "barbería", "uñas", "spa", "maquillaje", "general"]


@servicios_bp.route("/servicios")
def catalogo():
    """RF-06: catálogo público (solo servicios activos de proveedores aprobados)."""
    categoria = request.args.get("categoria")
    consulta = sb.table("servicios").select(
        "*, profesionales!inner(id, nombre_negocio, especialidad, ubicacion, estado)"
    ).eq("activo", True).eq("profesionales.estado", "aprobado")

    if categoria in CATEGORIAS:
        consulta = consulta.eq("categoria", categoria)
    else:
        categoria = None

    respuesta = consulta.order("creado_en", desc=True).execute()
    return render_template("servicios/listar.html", servicios=respuesta.data,
                           categoria=categoria, categorias=CATEGORIAS)


def _negocio_o_redirigir(sb_usuario):
    negocio = proveedor_propio(sb_usuario)
    if negocio is None:
        flash("Primero registra los datos de tu negocio.", "error")
        return None, redirect(url_for("profesionales.registro"))
    return negocio, None


def _servicio_propio(sb_usuario, negocio, servicio_id):
    return primero(sb_usuario.table("servicios").select("*")
                   .eq("id", servicio_id).eq("profesional_id", negocio["id"]).limit(1).execute())


@servicios_bp.route("/mis-servicios")
@exigir_proveedor
def gestionar():
    """RF-05: el proveedor ve y administra sus servicios (activos e inactivos)."""
    sb_usuario = cliente_sesion()
    negocio, redireccion = _negocio_o_redirigir(sb_usuario)
    if redireccion:
        return redireccion

    servicios = sb_usuario.table("servicios").select("*").eq(
        "profesional_id", negocio["id"]).order("creado_en", desc=True).execute()
    return render_template("servicios/gestionar.html", servicios=servicios.data, negocio=negocio)


@servicios_bp.route("/mis-servicios/nuevo", methods=["GET", "POST"])
@exigir_proveedor
def nuevo():
    sb_usuario = cliente_sesion()
    negocio, redireccion = _negocio_o_redirigir(sb_usuario)
    if redireccion:
        return redireccion

    if request.method == "GET":
        return render_template("servicios/form.html", servicio=None, categorias=CATEGORIAS)

    datos = _leer_formulario()
    if datos is None:
        return render_template("servicios/form.html", servicio=request.form, categorias=CATEGORIAS)

    datos["profesional_id"] = negocio["id"]
    try:
        sb_usuario.table("servicios").insert(datos).execute()
    except Exception as error:
        flash(mensaje_error(error, "No se pudo guardar el servicio."), "error")
        return render_template("servicios/form.html", servicio=request.form, categorias=CATEGORIAS)

    flash("Servicio agregado al catálogo.", "success")
    return redirect(url_for("servicios.gestionar"))


@servicios_bp.route("/mis-servicios/<servicio_id>/editar", methods=["GET", "POST"])
@exigir_proveedor
def editar(servicio_id):
    sb_usuario = cliente_sesion()
    negocio, redireccion = _negocio_o_redirigir(sb_usuario)
    if redireccion:
        return redireccion

    servicio = _servicio_propio(sb_usuario, negocio, servicio_id)
    if servicio is None:
        abort(404)

    if request.method == "GET":
        return render_template("servicios/form.html", servicio=servicio, categorias=CATEGORIAS)

    datos = _leer_formulario()
    if datos is None:
        return render_template("servicios/form.html", servicio=request.form, categorias=CATEGORIAS)

    try:
        sb_usuario.table("servicios").update(datos).eq("id", servicio_id).eq(
            "profesional_id", negocio["id"]).execute()
    except Exception as error:
        flash(mensaje_error(error, "No se pudo actualizar el servicio."), "error")
        return render_template("servicios/form.html", servicio=request.form, categorias=CATEGORIAS)

    flash("Servicio actualizado.", "success")
    return redirect(url_for("servicios.gestionar"))


@servicios_bp.route("/mis-servicios/<servicio_id>/estado", methods=["POST"])
@exigir_proveedor
def cambiar_estado(servicio_id):
    """RN-05: en vez de borrar (y perder el historial de citas) el servicio se desactiva/reactiva."""
    sb_usuario = cliente_sesion()
    negocio, redireccion = _negocio_o_redirigir(sb_usuario)
    if redireccion:
        return redireccion

    servicio = _servicio_propio(sb_usuario, negocio, servicio_id)
    if servicio is None:
        abort(404)

    nuevo_estado = not servicio["activo"]
    try:
        sb_usuario.table("servicios").update({"activo": nuevo_estado}).eq(
            "id", servicio_id).eq("profesional_id", negocio["id"]).execute()
    except Exception as error:
        flash(mensaje_error(error), "error")
        return redirect(url_for("servicios.gestionar"))

    flash("Servicio reactivado: ya se puede reservar." if nuevo_estado
          else "Servicio desactivado: ya no aparece para reservas (el historial se conserva).", "success")
    return redirect(url_for("servicios.gestionar"))


def _leer_formulario():
    """RN-04/06/07: nombre, precio y duración obligatorios y válidos. None si hay error."""
    nombre = request.form.get("nombre", "").strip()
    categoria = request.form.get("categoria", "general").strip().lower()
    descripcion = request.form.get("descripcion", "").strip()
    precio = request.form.get("precio", "").strip()
    duracion_min = request.form.get("duracion_min", "").strip()

    if not nombre or not precio or not duracion_min:
        flash("Nombre, precio y duración son obligatorios.", "error")
        return None
    if len(nombre) > 100 or len(descripcion) > 500:
        flash("El nombre (máx. 100) o la descripción (máx. 500) es demasiado largo.", "error")
        return None
    if categoria not in CATEGORIAS:
        flash("Elige una categoría de la lista.", "error")
        return None

    try:
        precio = round(float(precio), 2)
        duracion_min = int(duracion_min)
    except ValueError:
        flash("Precio y duración deben ser numéricos.", "error")
        return None
    if precio <= 0 or precio > 99999999:
        flash("El precio debe ser mayor que cero.", "error")
        return None
    if not 10 <= duracion_min <= 480:
        flash("La duración debe estar entre 10 y 480 minutos.", "error")
        return None

    return {"nombre": nombre, "categoria": categoria, "descripcion": descripcion,
            "precio": precio, "duracion_min": duracion_min}
