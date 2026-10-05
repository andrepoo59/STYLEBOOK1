"""Pruebas de flujos de la app con Supabase simulada (tests/fake_supabase.py)."""
import pathlib
from datetime import date, datetime, timedelta

from disponibilidad import (
    a_time, ahora_local, error_de_fecha, generar_franjas_libres, hay_cruce, sumar_minutos,
)
from utilidades import destino_seguro, mensaje_error, telefono_valido

RAIZ = pathlib.Path(__file__).resolve().parent.parent


# ---------- Utilidades ----------

def proximo_dia(dia_semana, minimo=2):
    d = date.today() + timedelta(days=minimo)
    while d.weekday() != dia_semana:
        d += timedelta(days=1)
    return d


def token(c):
    c.get("/login")
    with c.session_transaction() as s:
        return s["csrf_token"]


def post(c, url, datos=None, **kw):
    return c.post(url, data={"csrf_token": token(c), **(datos or {})}, **kw)


def entrar(c, email, password="secreto1"):
    return post(c, "/login", {"email": email, "password": password})


def escenario(db):
    """Un proveedor aprobado con un servicio de 60 min y horario lunes 09:00-12:00."""
    uid = db.agregar_usuario("local@x.co", "secreto1", "Barbería Central", "establecimiento")
    pid = db.insertar("profesionales", perfil_id=uid, nombre_negocio="Barbería Central",
                      especialidad="Barbería", ubicacion="Cra 5 # 10-20", telefono="3001234567",
                      tipo_proveedor="establecimiento", estado="aprobado", anios_experiencia=3)
    sid = db.insertar("servicios", profesional_id=pid, nombre="Corte clásico VIP", categoria="barbería",
                      precio=30000, duracion_min=60, activo=True)
    db.insertar("horarios_profesional", profesional_id=pid, dia_semana=0,
                hora_inicio="09:00", hora_fin="12:00")
    db.agregar_usuario("ana@x.co", "secreto1", "Ana", "cliente")
    return uid, pid, sid


# ---------- Arranque y estructura (bloqueantes del informe) ----------

def test_app_arranca_con_endpoints_unicos(app):
    endpoints = [r.endpoint for r in app.url_map.iter_rules()]
    assert endpoints.count("citas.cancelar") == 1
    assert app.test_client().get("/").status_code == 200


def test_sin_marcadores_de_merge_ni_restos():
    for ruta in list(RAIZ.glob("templates/**/*.html")) + [RAIZ / "static/css/estilos.css"]:
        texto = ruta.read_text(encoding="utf-8")
        assert "<<<<<<<" not in texto and ">>>>>>>" not in texto, ruta
        assert "HOLA" not in texto, ruta
    assert "ManoLista" not in (RAIZ / "utilidades.py").read_text(encoding="utf-8")


# ---------- Seguridad ----------

def test_post_sin_csrf_es_rechazado(cliente_http):
    assert cliente_http.post("/login", data={"email": "a@b.co", "password": "x"}).status_code == 400


def test_open_redirect_bloqueado(cliente_http, db):
    db.agregar_usuario("ana@x.co", "secreto1", "Ana")
    for malo in ("//evil.com", "https://evil.com", "/\\evil.com"):
        r = post(cliente_http, "/login?volver=" + malo, {"email": "ana@x.co", "password": "secreto1"})
        assert r.status_code == 302 and "evil.com" not in r.headers["Location"], malo
        cliente_http.get("/logout")
    r = post(cliente_http, "/login?volver=/servicios", {"email": "ana@x.co", "password": "secreto1"})
    assert r.headers["Location"].endswith("/servicios")


def test_destino_seguro_unitario():
    assert destino_seguro("/mis-citas") == "/mis-citas"
    assert destino_seguro("//x.com") is None and destino_seguro("http://x.com") is None
    assert destino_seguro(None, "/inicio") == "/inicio"


# ---------- RF-01: tipos de cuenta ----------

def test_registro_guarda_tipo_de_cuenta_y_admin_no_es_elegible(cliente_http, db):
    datos = {"nombre": "Luis", "email": "luis@x.co", "password": "secreto1", "telefono": "3001112222",
             "tipo_cuenta": "profesional"}
    db.exigir_confirmacion = True   # el caso que antes perdía el rol
    r = post(cliente_http, "/registro", datos)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    perfil = next(p for p in db.tablas["perfiles"] if p["nombre"] == "Luis")
    assert perfil["rol"] == "profesional" and perfil["telefono"] == "3001112222"

    db.exigir_confirmacion = False
    r = post(cliente_http, "/registro", {**datos, "email": "hack@x.co", "nombre": "Hack", "tipo_cuenta": "admin"})
    assert "Elige un tipo de cuenta válido" in r.get_data(as_text=True)
    assert not any(p["nombre"] == "Hack" for p in db.tablas["perfiles"])


def test_establecimiento_es_llevado_a_registrar_su_negocio(cliente_http, db):
    r = post(cliente_http, "/registro", {"nombre": "Salón Luna", "email": "luna@x.co",
             "password": "secreto1", "tipo_cuenta": "establecimiento"})
    assert r.status_code == 302 and "/mi-negocio/registro" in r.headers["Location"]
    r = post(cliente_http, "/mi-negocio/registro", {
        "nombre_negocio": "Salón Luna", "especialidad": "Peluquería", "ubicacion": "Calle 1 # 2-3",
        "telefono": "3001234567", "descripcion": "", "anios_experiencia": "2"})
    assert r.status_code == 302
    negocio = db.tablas["profesionales"][0]
    assert negocio["estado"] == "pendiente" and negocio["tipo_proveedor"] == "establecimiento"
    assert negocio["ubicacion"] == "Calle 1 # 2-3"


def test_registro_valida_datos(cliente_http):
    base = {"nombre": "X", "email": "x@x.co", "password": "secreto1", "tipo_cuenta": "cliente"}
    assert "correo electrónico válido" in post(cliente_http, "/registro", {**base, "email": "malo"}).get_data(as_text=True)
    assert "teléfono no es válido" in post(cliente_http, "/registro", {**base, "telefono": "abc"}).get_data(as_text=True)
    assert "al menos 6" in post(cliente_http, "/registro", {**base, "password": "123"}).get_data(as_text=True)


def test_control_de_acceso_por_rol(cliente_http, db):
    escenario(db)
    assert cliente_http.get("/mis-citas").status_code == 302          # sin sesión → login
    entrar(cliente_http, "ana@x.co")
    assert cliente_http.get("/mi-agenda").status_code == 403           # cliente no entra a agenda
    assert cliente_http.get("/mis-servicios").status_code == 403
    assert cliente_http.get("/admin/").status_code == 403
    cliente_http.get("/logout")
    entrar(cliente_http, "local@x.co")
    assert cliente_http.get("/mis-citas").status_code == 403           # proveedor no reserva


# ---------- Visibilidad de proveedores (RN-05, RF-07) ----------

def test_proveedor_pendiente_no_es_publico_ni_reservable(cliente_http, db):
    uid = db.agregar_usuario("p@x.co", "secreto1", "Pend", "profesional")
    pid = db.insertar("profesionales", perfil_id=uid, nombre_negocio="Pendiente SA", especialidad="Uñas",
                      estado="pendiente", tipo_proveedor="independiente")
    db.insertar("servicios", profesional_id=pid, nombre="Secreto", categoria="uñas", precio=10, duracion_min=30, activo=True)
    db.agregar_usuario("ana@x.co", "secreto1", "Ana", "cliente")

    assert "Secreto" not in cliente_http.get("/servicios").get_data(as_text=True)
    assert "Pendiente SA" not in cliente_http.get("/profesionales").get_data(as_text=True)
    assert cliente_http.get(f"/profesionales/{pid}").status_code == 404
    entrar(cliente_http, "ana@x.co")
    assert cliente_http.get(f"/profesionales/{pid}/reservar").status_code == 404
    assert cliente_http.get(f"/profesionales/{pid}/horarios-disponibles?fecha=2030-01-01&servicio_id=x").get_json()["horarios"] == []


def test_detalle_muestra_ubicacion_horario_y_disponibilidad(cliente_http, db):
    _, pid, _ = escenario(db)
    html = cliente_http.get(f"/profesionales/{pid}").get_data(as_text=True)
    assert "Cra 5 # 10-20" in html            # ubicación (RF-07)
    assert "Lunes" in html and "09:00 – 12:00" in html   # horario
    assert "Próxima disponibilidad" in html and "09:00" in html


# ---------- Disponibilidad y no duplicidad (RN-08/09/10) ----------

def reservar(c, pid, sid, fecha, hora):
    return post(c, f"/profesionales/{pid}/reservar",
                {"servicio_id": sid, "fecha": fecha.isoformat(), "hora_inicio": hora})


def test_reserva_completa_y_no_se_ofrece_lo_ocupado(cliente_http, db):
    _, pid, sid = escenario(db)
    lunes = proximo_dia(0)
    entrar(cliente_http, "ana@x.co")

    r = reservar(cliente_http, pid, sid, lunes, "09:00")
    assert r.status_code == 302 and "/mis-citas" in r.headers["Location"]
    cita = db.tablas["citas"][0]
    assert cita["hora_inicio"] == "09:00:00" and cita["hora_fin"] == "10:00:00" and cita["cliente_nombre"] == "Ana"

    # El servicio dura 60 min: con 09:00-10:00 ocupado, 09:00/09:15/09:30/09:45 dejan de ofrecerse…
    url = f"/profesionales/{pid}/horarios-disponibles?servicio_id={sid}&fecha={lunes}"
    horas = cliente_http.get(url).get_json()["horarios"]
    assert horas == ["10:00", "10:15", "10:30", "10:45", "11:00"]   # …y 10:00 sí (empieza justo al terminar)

    # Reservar a mano un horario ocupado es rechazado por la app
    r = reservar(cliente_http, pid, sid, lunes, "09:30")
    assert "ya no está disponible" in r.get_data(as_text=True)
    assert len(db.tablas["citas"]) == 1

    # Cita pegada a la anterior: debe aceptarse (antes el chequeo con texto la marcaba como choque)
    assert reservar(cliente_http, pid, sid, lunes, "10:00").status_code == 302
    assert len(db.tablas["citas"]) == 2


def test_carrera_de_dos_clientes_muestra_mensaje_amable(cliente_http, db, monkeypatch):
    _, pid, sid = escenario(db)
    lunes = proximo_dia(0)
    entrar(cliente_http, "ana@x.co")
    import blueprints.citas as citas
    # Simula que otro cliente reserva entre el cálculo de horas libres y el insert
    original = citas._libres_del_dia
    def desactualizado(*a, **k):
        db.insertar("citas", cliente_id="otro", profesional_id=pid, servicio_id=sid,
                    fecha=lunes.isoformat(), hora_inicio="09:00", hora_fin="10:00", estado="confirmada")
        monkeypatch.setattr(citas, "_libres_del_dia", original)
        return ["09:00"]
    monkeypatch.setattr(citas, "_libres_del_dia", desactualizado)
    html = reservar(cliente_http, pid, sid, lunes, "09:00").get_data(as_text=True)
    assert "acaba de ser ocupado" in html and "conflicting key" not in html and "23P01" not in html


def test_no_se_reserva_en_el_pasado_ni_fuera_de_horario(cliente_http, db):
    _, pid, sid = escenario(db)
    entrar(cliente_http, "ana@x.co")
    ayer = date.today() - timedelta(days=1)
    assert "fecha pasada" in reservar(cliente_http, pid, sid, ayer, "09:00").get_data(as_text=True)
    lunes = proximo_dia(0)
    assert "ya no está disponible" in reservar(cliente_http, pid, sid, lunes, "15:00").get_data(as_text=True)   # fuera de horario
    martes = proximo_dia(1)
    assert "ya no está disponible" in reservar(cliente_http, pid, sid, martes, "09:00").get_data(as_text=True)  # día sin atención
    lejos = date.today() + timedelta(days=400)
    assert "días de anticipación" in reservar(cliente_http, pid, sid, lejos, "09:00").get_data(as_text=True)
    assert db.tablas["citas"] == []


# ---------- Reprogramación (RN-08/09/10) ----------

def crear_cita(db, pid, sid, fecha, ini, fin, estado="confirmada", cliente="ana@x.co"):
    uid = next(u["id"] for u in db.usuarios if u["email"] == cliente)
    return db.insertar("citas", cliente_id=uid, profesional_id=pid, servicio_id=sid, fecha=fecha.isoformat(),
                       hora_inicio=ini, hora_fin=fin, estado=estado, cliente_nombre="Ana")


def test_reprogramar_valida_horario_cruce_y_permite_pegado(cliente_http, db):
    _, pid, sid = escenario(db)
    lunes = proximo_dia(0)
    mia = crear_cita(db, pid, sid, lunes, "09:00", "10:00")
    otra = crear_cita(db, pid, sid, lunes, "10:00", "11:00", cliente="ana@x.co")
    entrar(cliente_http, "ana@x.co")

    url = f"/mis-citas/{mia}/reprogramar"
    def mover(fecha, hora):
        return post(cliente_http, url, {"fecha": fecha.isoformat(), "hora_inicio": hora})

    assert "no está disponible" in mover(lunes, "10:30").get_data(as_text=True)          # cruza con la otra
    assert "no está disponible" in mover(lunes, "15:00").get_data(as_text=True)          # fuera de horario del proveedor
    assert "fecha pasada" in mover(date.today() - timedelta(days=2), "09:00").get_data(as_text=True)
    r = mover(lunes, "11:00")                                                             # pegada al final de la otra: OK
    assert r.status_code == 302
    c = next(c for c in db.tablas["citas"] if c["id"] == mia)
    assert c["hora_inicio"] == "11:00:00" and c["hora_fin"] == "12:00:00"

    # Su propia cita no bloquea su horario actual: "moverla" a donde ya está es válido
    assert mover(lunes, "11:00").status_code == 302


def test_cancelar_libera_el_horario_y_no_toca_citas_ajenas(cliente_http, db):
    _, pid, sid = escenario(db)
    lunes = proximo_dia(0)
    cid = crear_cita(db, pid, sid, lunes, "09:00", "10:00")
    ajena = db.insertar("citas", cliente_id="otro", profesional_id=pid, servicio_id=sid, fecha=lunes.isoformat(),
                        hora_inicio="10:00", hora_fin="11:00", estado="confirmada")
    entrar(cliente_http, "ana@x.co")
    assert post(cliente_http, f"/mis-citas/{ajena}/cancelar").status_code == 404
    assert post(cliente_http, f"/mis-citas/{cid}/cancelar").status_code == 302
    estados = {c["id"]: c["estado"] for c in db.tablas["citas"]}
    assert estados[cid] == "cancelada" and estados[ajena] == "confirmada"
    horas = cliente_http.get(f"/profesionales/{pid}/horarios-disponibles?servicio_id={sid}&fecha={lunes}").get_json()["horarios"]
    assert "09:00" in horas


# ---------- Estados (RN-13) y agenda del proveedor ----------

def test_proveedor_completa_solo_citas_que_ya_empezaron(cliente_http, db):
    _, pid, sid = escenario(db)
    futura = crear_cita(db, pid, sid, proximo_dia(0), "09:00", "10:00")
    ayer = date.today() - timedelta(days=1)
    pasada = crear_cita(db, pid, sid, ayer, "09:00", "10:00")
    entrar(cliente_http, "local@x.co")

    assert "Ana" in cliente_http.get("/mi-agenda").get_data(as_text=True)
    post(cliente_http, f"/mi-agenda/{futura}/estado", {"estado": "completada"})
    assert next(c for c in db.tablas["citas"] if c["id"] == futura)["estado"] == "confirmada"
    post(cliente_http, f"/mi-agenda/{pasada}/estado", {"estado": "completada"})
    assert next(c for c in db.tablas["citas"] if c["id"] == pasada)["estado"] == "completada"
    # una completada ya no cambia
    post(cliente_http, f"/mi-agenda/{pasada}/estado", {"estado": "cancelada"})
    assert next(c for c in db.tablas["citas"] if c["id"] == pasada)["estado"] == "completada"


# ---------- Servicios (RN-05) ----------

def test_servicio_se_desactiva_sin_borrar_el_historial(cliente_http, db):
    _, pid, sid = escenario(db)
    crear_cita(db, pid, sid, proximo_dia(0), "09:00", "10:00")
    entrar(cliente_http, "local@x.co")
    assert post(cliente_http, f"/mis-servicios/{sid}/estado").status_code == 302
    servicio = db.tablas["servicios"][0]
    assert servicio["activo"] is False and len(db.tablas["citas"]) == 1     # la cita sigue existiendo
    assert "Corte clásico VIP" not in cliente_http.get("/servicios").get_data(as_text=True)
    post(cliente_http, f"/mis-servicios/{sid}/estado")
    assert db.tablas["servicios"][0]["activo"] is True
    # no existe la ruta de borrado
    assert post(cliente_http, f"/mis-servicios/{sid}/eliminar").status_code == 404


def test_servicio_valida_precio_y_duracion(cliente_http, db):
    escenario(db)
    entrar(cliente_http, "local@x.co")
    base = {"nombre": "Barba", "categoria": "barbería", "descripcion": "", "precio": "15000", "duracion_min": "30"}
    assert "mayor que cero" in post(cliente_http, "/mis-servicios/nuevo", {**base, "precio": "0"}).get_data(as_text=True)
    assert "entre 10 y 480" in post(cliente_http, "/mis-servicios/nuevo", {**base, "duracion_min": "5"}).get_data(as_text=True)
    assert len(db.tablas["servicios"]) == 1
    assert post(cliente_http, "/mis-servicios/nuevo", base).status_code == 302
    assert len(db.tablas["servicios"]) == 2


# ---------- Horarios del proveedor ----------

def test_horarios_comparan_horas_reales_y_no_se_solapan(cliente_http, db):
    escenario(db)
    entrar(cliente_http, "local@x.co")
    def agregar(dia, ini, fin):
        return post(cliente_http, "/mis-horarios/nuevo", {"dia_semana": dia, "hora_inicio": ini, "hora_fin": fin},
                    follow_redirects=True).get_data(as_text=True)
    assert "posterior" in agregar("1", "10:00", "09:00")
    assert "se cruza" in agregar("0", "11:00", "13:00")          # lunes ya tiene 09:00-12:00
    assert "Franja horaria agregada" in agregar("0", "14:00", "18:00")
    assert len(db.tablas["horarios_profesional"]) == 2


# ---------- Módulo de disponibilidad (unitario) ----------

def test_hora_texto_vs_time_y_bordes():
    assert a_time("10:00") == a_time("10:00:00")
    ocupada = [{"hora_inicio": "10:00:00", "hora_fin": "11:00:00"}]
    assert not hay_cruce(a_time("09:00"), a_time("10:00"), ocupada)     # termina justo cuando empieza otra
    assert not hay_cruce(a_time("11:00"), a_time("12:00"), ocupada)     # empieza justo cuando termina otra
    assert hay_cruce(a_time("10:30"), a_time("11:30"), ocupada)


def test_franjas_excluyen_horas_pasadas_de_hoy():
    hoy = date(2030, 5, 6)
    bloque = [{"hora_inicio": "09:00:00", "hora_fin": "12:00:00"}]
    ahora = datetime(2030, 5, 6, 10, 20)
    assert generar_franjas_libres(hoy, bloque, [], 30, ahora=ahora)[0] == "10:30"
    assert generar_franjas_libres(hoy, bloque, [], 30)[0] == "09:00"
    assert sumar_minutos(a_time("11:30"), 45).strftime("%H:%M") == "12:15"


def test_error_de_fecha():
    ahora = datetime(2030, 5, 6, 10, 0)
    assert error_de_fecha(date(2030, 5, 5), ahora) and error_de_fecha(date(2031, 5, 5), ahora)
    assert error_de_fecha(date(2030, 5, 6), ahora) is None


# ---------- Mensajes de error ----------

def test_mensaje_error_no_expone_detalles_tecnicos():
    from postgrest.exceptions import APIError
    e = APIError({"message": 'violates exclusion constraint "sin_cruces_horario"', "code": "23P01", "details": None, "hint": None})
    assert mensaje_error(e) == "Ese horario acaba de ser ocupado por otra reserva. Elige otro."
    e = APIError({"message": "SB: No se pueden agendar citas en fechas u horas pasadas.", "code": "P0001", "details": None, "hint": None})
    assert mensaje_error(e) == "No se pueden agendar citas en fechas u horas pasadas."
    assert "relation" not in mensaje_error(Exception('relation "x" does not exist'))
    assert telefono_valido("") and telefono_valido("+57 300 123 4567") and not telefono_valido("abc")


# ---------- Todas las pantallas renderizan ----------

def test_todas_las_pantallas_renderizan_por_rol(cliente_http, db):
    uid, pid, sid = escenario(db)
    lunes = proximo_dia(0)
    cid = crear_cita(db, pid, sid, lunes, "09:00", "10:00")
    crear_cita(db, pid, sid, date.today() - timedelta(days=3), "09:00", "10:00", estado="completada")
    db.agregar_usuario("root@x.co", "secreto1", "Admin", "admin")
    db.insertar("profesionales", perfil_id="z", nombre_negocio="Por aprobar", especialidad="Uñas",
                tipo_proveedor="independiente", estado="pendiente", ubicacion="Calle 9", telefono="300")

    for url in ("/", "/servicios", "/servicios?categoria=barbería", "/profesionales", f"/profesionales/{pid}",
                "/login", "/registro"):
        assert cliente_http.get(url).status_code == 200, url

    entrar(cliente_http, "ana@x.co")
    for url in ("/perfil", "/perfil/editar", f"/profesionales/{pid}/reservar",
                f"/profesionales/{pid}/reservar?servicio_id={sid}", "/mis-citas", f"/mis-citas/{cid}/reprogramar"):
        assert cliente_http.get(url).status_code == 200, url
    assert "Historial" in cliente_http.get("/mis-citas").get_data(as_text=True)
    cliente_http.get("/logout")

    entrar(cliente_http, "local@x.co")
    for url in ("/perfil", "/mi-negocio", "/mis-servicios", "/mis-servicios/nuevo", f"/mis-servicios/{sid}/editar",
                "/mis-horarios", "/mi-agenda"):
        assert cliente_http.get(url).status_code == 200, url
    assert cliente_http.get("/mi-negocio/registro").status_code == 302     # ya registrado → editar
    assert cliente_http.get("/mis-servicios/no-existe/editar").status_code == 404
    cliente_http.get("/logout")

    entrar(cliente_http, "root@x.co")
    html = cliente_http.get("/admin/").get_data(as_text=True)
    assert "Por aprobar" in html and "Calle 9" in html
    nuevo = next(p for p in db.tablas["profesionales"] if p["nombre_negocio"] == "Por aprobar")
    assert post(cliente_http, f"/admin/profesionales/{nuevo['id']}/aprobar").status_code == 302
    assert nuevo["estado"] == "aprobado"
    assert post(cliente_http, "/admin/profesionales/no-existe/aprobar").status_code == 404
