"""
Disponibilidad real de un proveedor (RN-08, RN-09, RN-10) y validación de fechas.

Todas las comparaciones se hacen con objetos `time`/`date`, nunca con texto:
así "10:00" y "10:00:00" son iguales y una cita que empieza justo cuando termina
otra NO se considera cruce.
"""
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Bogota")
MAX_DIAS_ADELANTE = 90   # no se reserva más allá de ~3 meses
PASO_MIN = 15            # las horas ofrecidas van de 15 en 15 minutos


def ahora_local() -> datetime:
    """Hora actual de Colombia, sin zona (para comparar con fecha+hora de la BD)."""
    return datetime.now(ZONA).replace(tzinfo=None)


def a_time(valor) -> time:
    """'HH:MM' / 'HH:MM:SS' (formato de Supabase) → datetime.time."""
    if isinstance(valor, time):
        return valor.replace(second=0, microsecond=0)
    partes = str(valor).split(":")
    return time(int(partes[0]), int(partes[1]))


_a_time = a_time  # alias por compatibilidad


def parsear_fecha(texto):
    try:
        return datetime.strptime(texto or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def parsear_hora(texto):
    try:
        return datetime.strptime(texto or "", "%H:%M").time()
    except ValueError:
        return None


def sumar_minutos(hora: time, minutos: int) -> time:
    return (datetime.combine(date.today(), hora) + timedelta(minutes=minutos)).time()


def error_de_fecha(fecha: date, ahora: datetime = None):
    """Devuelve un mensaje si la fecha no es reservable (pasada o demasiado lejana); si no, None."""
    ahora = ahora or ahora_local()
    if fecha < ahora.date():
        return "No puedes agendar en una fecha pasada."
    if fecha > ahora.date() + timedelta(days=MAX_DIAS_ADELANTE):
        return f"Solo se puede reservar con hasta {MAX_DIAS_ADELANTE} días de anticipación."
    return None


def generar_franjas_libres(fecha: date, horario_laboral: list, ocupados: list,
                           duracion_min: int, paso_min: int = PASO_MIN, ahora: datetime = None):
    """
    Horas de inicio ('HH:MM') realmente libres ese día: dentro del horario de atención,
    sin cruzar con citas activas y sin ser una hora que ya pasó (si la fecha es hoy).
    """
    ocupados_t = [(a_time(c["hora_inicio"]), a_time(c["hora_fin"])) for c in ocupados]
    duracion = timedelta(minutes=duracion_min)
    paso = timedelta(minutes=paso_min)
    libres = set()

    for bloque in horario_laboral:
        cursor = datetime.combine(fecha, a_time(bloque["hora_inicio"]))
        fin_bloque = datetime.combine(fecha, a_time(bloque["hora_fin"]))
        while cursor + duracion <= fin_bloque:
            inicio_t, fin_t = cursor.time(), (cursor + duracion).time()
            en_el_pasado = ahora is not None and cursor <= ahora
            se_cruza = any(inicio_t < fin_o and fin_t > inicio_o for inicio_o, fin_o in ocupados_t)
            if not en_el_pasado and not se_cruza:
                libres.add(cursor.strftime("%H:%M"))
            cursor += paso
    return sorted(libres)


def hay_cruce(inicio: time, fin: time, ocupados: list) -> bool:
    """Dos rangos se cruzan si cada uno empieza antes de que el otro termine."""
    return any(inicio < a_time(c["hora_fin"]) and fin > a_time(c["hora_inicio"]) for c in ocupados)


def franjas_por_dia(sb, profesional_id, desde: date, hasta: date, duracion_min: int,
                    excluir_cita=None, ahora: datetime = None):
    """
    {fecha: [horas libres]} para el rango [desde, hasta].
    Los rangos ocupados vienen de la función SQL `rangos_ocupados` (security definer),
    que no expone datos de clientes y no depende de la RLS de `citas`.
    """
    horarios = sb.table("horarios_profesional").select(
        "dia_semana, hora_inicio, hora_fin"
    ).eq("profesional_id", profesional_id).execute().data or []

    params = {"p_profesional": profesional_id, "p_desde": desde.isoformat(),
              "p_hasta": hasta.isoformat()}
    if excluir_cita:
        params["p_excluir"] = excluir_cita
    ocupados = sb.rpc("rangos_ocupados", params).execute().data or []

    por_fecha = {}
    for o in ocupados:
        por_fecha.setdefault(o["fecha"], []).append(o)

    resultado, dia = {}, desde
    while dia <= hasta:
        bloques = [h for h in horarios if h["dia_semana"] == dia.weekday()]
        resultado[dia] = generar_franjas_libres(
            dia, bloques, por_fecha.get(dia.isoformat(), []), duracion_min, ahora=ahora
        )
        dia += timedelta(days=1)
    return resultado
