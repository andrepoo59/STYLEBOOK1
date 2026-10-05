# StyleBook

Aplicación web en Flask + Supabase para reservar citas en salones de belleza, barberías y con profesionales independientes.

## Tipos de cuenta (RF-01, RN-02)

| Tipo | Qué hace |
|---|---|
| **Cliente** | Consulta servicios y proveedores, reserva, cancela y reprograma sus citas. |
| **Establecimiento** | Salón o barbería: registra su negocio (nombre, ubicación, contacto), servicios, horario y atiende su agenda. |
| **Profesional independiente** | Igual que el establecimiento, pero a título personal. |
| *Administrador* | Interno (no se elige al registrarse): aprueba o rechaza proveedores. Se asigna desde el SQL Editor de Supabase. |

> **Modelo actual:** cada establecimiento o profesional independiente es *un proveedor con una agenda propia*
> (tabla `profesionales`, columna `tipo_proveedor`). Un establecimiento con varios profesionales, cada uno con su
> propia agenda, queda para un sprint posterior.

## Puesta en marcha

1. **Entorno:** `python -m venv venv`, activarlo, y `pip install -r requirements.txt`.
2. **Variables:** copia `.env.example` a `.env` y completa:
   ```env
   SUPABASE_URL=https://TU-PROYECTO.supabase.co
   SUPABASE_ANON_KEY=tu_clave_anon_publica
   FLASK_SECRET_KEY=una_cadena_larga_y_aleatoria
   SESSION_COOKIE_SECURE=0   # 1 cuando publiques con HTTPS
   ```
3. **Base de datos:** pega **`db/schema.sql`** completo en Supabase → SQL Editor → Run.
   Es un único script, ordenado e idempotente: sirve para un proyecto nuevo y también migra uno que ya corrió
   los scripts anteriores (`schema.sql` viejo + `schema_citas.sql`, que ya no existe).
4. **Primer administrador:** regístrate desde la app y luego, en el SQL Editor:
   `update public.perfiles set rol = 'admin' where id = (select id from auth.users where email = 'tu@correo');`
5. **Ejecutar:** `python app.py` (desarrollo) o `gunicorn -w 4 -b 0.0.0.0:5000 app:app` (producción).

## Pruebas

```bash
pip install pytest && python -m pytest tests -q          # flujos de la app (Supabase simulada)
npm i @electric-sql/pglite && node tests/test_sql.mjs    # esquema SQL contra Postgres real: triggers, RLS, anti-cruce
```

Las pruebas de la app usan una Supabase simulada (no ejercitan RLS); las de SQL usan Postgres real pero no el servicio
de Auth de Supabase. Falta una pasada manual contra tu proyecto real de Supabase.

## Estructura

```
app.py                  arranque, CSRF y páginas de error
supabase_client.py      cliente Supabase (anónimo y como usuario)
utilidades.py           roles, validaciones, redirección segura, mensajes de error
disponibilidad.py       franjas libres, fechas válidas, zona horaria America/Bogota
blueprints/             auth · perfil · profesionales · servicios · horarios · citas · admin
templates/  static/     vistas Jinja y estilos
db/schema.sql           esquema completo (tablas, funciones, triggers, RLS)
tests/                  pruebas (pytest y node)
```

## Trazabilidad: requisitos → código

| Requisito | Dónde se cumple |
|---|---|
| RF-01, RN-02 tipos de cuenta | `blueprints/auth.py` (registro), `perfiles.rol` + `crear_perfil()` en `schema.sql` |
| RN-01 correo único | Supabase Auth (el mensaje "ya registrado" se traduce en `utilidades.mensaje_error`) |
| RF-02 inicio de sesión | `blueprints/auth.py` (`login`, `_destino_inicial`) |
| RN-03 acceso según rol | `utilidades.exigir_rol` (app) + RLS y `proteger_perfil()` (base) |
| RF-03 gestión del perfil | `blueprints/perfil.py`, `profesionales.editar_negocio` |
| RF-04, RN-19 datos del proveedor | `blueprints/profesionales.py` (`registro`, `editar_negocio`), `blueprints/horarios.py` |
| RF-05, RN-05/06/07 servicios | `blueprints/servicios.py` (se desactivan, no se borran) |
| RF-06 consultar servicios | `servicios.catalogo` (solo activos de proveedores aprobados) |
| RF-07 consultar proveedor | `profesionales.detalle` (servicios, precios, horario, ubicación, disponibilidad) |
| RN-04 datos obligatorios y válidos | validaciones en cada formulario + `utilidades.py` |
| RN-08 horario de atención | `disponibilidad.generar_franjas_libres` + `validar_cita()` |
| RN-09 solo horas disponibles | `disponibilidad.franjas_por_dia` + función SQL `rangos_ocupados()` |
| RN-10 no duplicidad | restricción `sin_cruces_horario` en `schema.sql` |
| RN-11, RN-12 profesional–servicio–cliente | `validar_cita()` y RLS de `citas` |
| RN-13 estados de la cita | `citas_estado_check` + transiciones en `validar_cita()`; `citas.agenda_estado` |

## Pendiente para próximos sprints

RN-14 (aceptar/rechazar solicitudes; hoy toda cita nace *confirmada*), RN-15 (anticipación mínima para cancelar),
RN-16 (calificaciones), RN-17 (festivos y días de cierre), RN-18 (política de datos personales) y el modelo de
establecimiento con varios profesionales.
