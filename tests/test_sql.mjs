// Prueba del esquema contra Postgres real (PGlite): triggers, RLS y restricción anti-cruce.
// Uso:  npm i @electric-sql/pglite  &&  node tests/test_sql.mjs
import { PGlite } from "@electric-sql/pglite";
import { btree_gist } from "@electric-sql/pglite/contrib/btree_gist";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const aqui = dirname(fileURLToPath(import.meta.url));
const leer = (r) => readFileSync(join(aqui, r), "utf8");
const SCHEMA = leer("../db/schema.sql");

// --- Entorno tipo Supabase: esquema auth, auth.uid(), roles anon/authenticated ---
const ENTORNO = `
  create schema if not exists auth;
  create table auth.users (id uuid primary key default gen_random_uuid(), email text, raw_user_meta_data jsonb default '{}');
  create or replace function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  do $$ begin
    if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
    if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  end $$;
  grant usage on schema public, auth to anon, authenticated;
  alter default privileges in schema public grant all on tables to anon, authenticated;
  alter default privileges in schema public grant execute on functions to anon, authenticated;
`;
const PERMISOS = `grant all on all tables in schema public to anon, authenticated;`;

let ok = 0;
const prueba = async (nombre, fn) => { try { await fn(); ok++; console.log("  ✔", nombre); }
  catch (e) { console.error("  ✘", nombre, "\n     ", e.message); process.exitCode = 1; } };

async function nuevaBD() {
  const db = new PGlite({ extensions: { btree_gist } });
  await db.exec(ENTORNO);
  return db;
}
// Ejecuta sql como usuario (uid) con RLS activa; uid=null → anon; "sql" → superusuario sin JWT (SQL Editor)
async function como(db, uid, sql, params = []) {
  if (uid === "sql") { await db.exec("reset role; select set_config('request.jwt.claim.sub','',false)"); return db.query(sql, params); }
  await db.exec(`set role ${uid ? "authenticated" : "anon"}; select set_config('request.jwt.claim.sub','${uid ?? ""}',false)`);
  try { return await db.query(sql, params); } finally { await db.exec("reset role"); }
}
const falla = async (promesa, fragmento) => {
  try { await promesa; } catch (e) { assert.match(e.message, new RegExp(fragmento, "i")); return; }
  assert.fail(`Debía fallar con /${fragmento}/`);
};

// Fechas: próximo lunes (≥2 días) y "ayer", en hora de Colombia
const bogota = (await new PGlite().query("select (now() at time zone 'America/Bogota')::date as d")).rows[0].d;
const sumar = (d, n) => { const x = new Date(d); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
let lunes = sumar(bogota, 2); while (new Date(lunes + "T00:00:00Z").getUTCDay() !== 1) lunes = sumar(lunes, 1);
const ayer = sumar(bogota, -1);

async function poblar(db) {
  const ids = {};
  const mk = async (k, email, meta) => { ids[k] = (await db.query(
    "insert into auth.users(email, raw_user_meta_data) values ($1, $2::jsonb) returning id", [email, JSON.stringify(meta)])).rows[0].id; };
  await mk("ana", "ana@x.co", { nombre: "Ana" });
  await mk("beto", "beto@x.co", { nombre: "Beto", tipo_cuenta: "cliente" });
  await mk("local", "local@x.co", { nombre: "Barbería", telefono: "300", tipo_cuenta: "establecimiento" });
  await mk("otro", "otro@x.co", { nombre: "Otro local", tipo_cuenta: "profesional" });
  await mk("hack", "hack@x.co", { nombre: "Hack", tipo_cuenta: "admin" });
  await db.query("insert into auth.users(id,email,raw_user_meta_data) values (gen_random_uuid(),'root@x.co','{\"nombre\":\"Root\"}')");
  ids.root = (await db.query("select id from auth.users where email='root@x.co'")).rows[0].id;
  await db.query("update perfiles set rol='admin' where id=$1", [ids.root]);   // como SQL Editor
  return ids;
}

// ===================== INSTALACIÓN LIMPIA =====================
console.log("\nInstalación limpia");
{
  const db = await nuevaBD();
  await db.exec(SCHEMA); await db.exec(PERMISOS);
  await prueba("el script es idempotente (se puede correr 2 veces)", async () => { await db.exec(SCHEMA); await db.exec(PERMISOS); });
  const u = await poblar(db);

  await prueba("perfil nace con nombre, teléfono y tipo de cuenta; 'admin' no es elegible", async () => {
    const r = await db.query("select nombre, telefono, rol from perfiles where id=$1", [u.local]);
    assert.deepEqual(r.rows[0], { nombre: "Barbería", telefono: "300", rol: "establecimiento" });
    assert.equal((await db.query("select rol from perfiles where id=$1", [u.otro])).rows[0].rol, "profesional");
    assert.equal((await db.query("select rol from perfiles where id=$1", [u.hack])).rows[0].rol, "cliente");
  });

  await prueba("nadie cambia su propio rol (escalada a admin bloqueada)", async () => {
    await falla(como(db, u.ana, "update perfiles set rol='admin' where id=$1", [u.ana]), "tipo de cuenta");
    await falla(como(db, u.local, "update perfiles set rol='cliente' where id=$1", [u.local]), "tipo de cuenta");
    await como(db, u.ana, "update perfiles set nombre='Ana María' where id=$1", [u.ana]);
    assert.equal((await db.query("select nombre from perfiles where id=$1", [u.ana])).rows[0].nombre, "Ana María");
  });

  await prueba("un usuario solo ve su propio perfil", async () => {
    assert.equal((await como(db, u.ana, "select id from perfiles")).rows.length, 1);
    assert.equal((await como(db, null, "select id from perfiles")).rows.length, 0);
  });

  let pid, pid2, sid;
  await prueba("proveedor se registra 'pendiente' aunque intente 'aprobado'; un cliente no puede registrarse", async () => {
    pid = (await como(db, u.local, `insert into profesionales(perfil_id,nombre_negocio,especialidad,ubicacion,tipo_proveedor,estado)
      values ($1,'Barbería Central','Barbería','Cra 5','establecimiento','aprobado') returning id`, [u.local])).rows[0].id;
    assert.equal((await db.query("select estado from profesionales where id=$1", [pid])).rows[0].estado, "pendiente");
    await falla(como(db, u.ana, `insert into profesionales(perfil_id,nombre_negocio,especialidad) values ($1,'X','Y')`, [u.ana]), "row-level security");
    pid2 = (await como(db, u.otro, `insert into profesionales(perfil_id,nombre_negocio,especialidad,tipo_proveedor)
      values ($1,'Otro','Uñas','independiente') returning id`, [u.otro])).rows[0].id;
  });

  await prueba("el proveedor NO puede aprobarse a sí mismo; el admin sí", async () => {
    await falla(como(db, u.local, "update profesionales set estado='aprobado' where id=$1", [pid]), "administrador");
    const ajeno = await como(db, u.ana, "update profesionales set estado='aprobado' where id=$1", [pid]);
    assert.equal(ajeno.affectedRows, 0);   // otra persona ni siquiera ve la fila
    assert.equal((await db.query("select estado from profesionales where id=$1", [pid])).rows[0].estado, "pendiente");
    await como(db, u.root, "update profesionales set estado='aprobado' where id=$1", [pid]);
    assert.equal((await db.query("select estado from profesionales where id=$1", [pid])).rows[0].estado, "aprobado");
  });

  await prueba("el público solo ve proveedores aprobados (el dueño ve el suyo)", async () => {
    assert.deepEqual((await como(db, null, "select id from profesionales")).rows.map(r => r.id), [pid]);
    assert.equal((await como(db, u.otro, "select id from profesionales")).rows.length, 2);   // aprobado + el suyo pendiente
    assert.equal((await como(db, u.root, "select id from profesionales")).rows.length, 2);
  });

  await prueba("servicios: visibles solo si activos y de proveedor aprobado; sin borrado", async () => {
    sid = (await como(db, u.local, `insert into servicios(profesional_id,nombre,precio,duracion_min) values ($1,'Corte',30000,60) returning id`, [pid])).rows[0].id;
    await como(db, u.otro, `insert into servicios(profesional_id,nombre,precio,duracion_min) values ($1,'Manicure',20000,30)`, [pid2]);
    await falla(como(db, u.otro, `insert into servicios(profesional_id,nombre,precio,duracion_min) values ($1,'Colado',1,30)`, [pid]), "row-level security");
    assert.deepEqual((await como(db, null, "select nombre from servicios")).rows.map(r => r.nombre), ["Corte"]);   // Manicure: proveedor pendiente
    await como(db, u.local, "delete from servicios where id=$1", [sid]);                                          // sin política DELETE: no borra
    assert.equal((await db.query("select count(*)::int n from servicios where id=$1", [sid])).rows[0].n, 1);
  });

  await prueba("horarios: lectura pública, gestión solo del dueño", async () => {
    await como(db, u.local, `insert into horarios_profesional(profesional_id,dia_semana,hora_inicio,hora_fin) values ($1,0,'09:00','12:00')`, [pid]);
    assert.equal((await como(db, null, "select id from horarios_profesional")).rows.length, 1);
    await falla(como(db, u.ana, `insert into horarios_profesional(profesional_id,dia_semana,hora_inicio,hora_fin) values ($1,1,'09:00','12:00')`, [pid]), "row-level security");
  });

  const cita = (uid, f, ini, fin, extra = "") => como(db, uid, `insert into citas(cliente_id,profesional_id,servicio_id,fecha,hora_inicio,hora_fin,cliente_nombre${extra ? "," + extra.split("=")[0] : ""})
      values ($1,$2,$3,$4,$5,$6,'x'${extra ? "," + extra.split("=")[1] : ""}) returning id`, [uid, pid, sid, f, ini, fin]);
  let c1, c2;
  await prueba("reserva válida; aparece en 'mis citas' de la cliente y en la agenda del proveedor", async () => {
    c1 = (await cita(u.ana, lunes, "09:00", "10:00")).rows[0].id;
    assert.equal((await como(db, u.ana, "select id from citas")).rows.length, 1);
    assert.equal((await como(db, u.local, "select id from citas")).rows.length, 1);
    assert.equal((await como(db, u.beto, "select id from citas")).rows.length, 0);
  });

  await prueba("RN-10: dos citas que se cruzan → restricción sin_cruces_horario", async () => {
    await falla(cita(u.beto, lunes, "09:30", "10:30"), "sin_cruces_horario");
    await falla(cita(u.beto, lunes, "09:00", "10:00"), "sin_cruces_horario");
  });

  await prueba("cita pegada (empieza justo cuando termina otra) SÍ se permite", async () => {
    c2 = (await cita(u.beto, lunes, "10:00", "11:00")).rows[0].id;
  });

  await prueba("RN-08: fuera del horario de atención o día sin atención → rechazada", async () => {
    await falla(cita(u.beto, lunes, "14:00", "15:00"), "fuera del horario");
    await falla(cita(u.beto, sumar(lunes, 1), "09:00", "10:00"), "fuera del horario");
  });

  await prueba("no se agenda en el pasado ni con duración distinta a la del servicio", async () => {
    await falla(cita(u.beto, sumar(lunes, -7), "09:00", "10:00"), "pasadas");
    await falla(cita(u.beto, lunes, "11:00", "11:30"), "duración");
  });

  await prueba("RN-13: una cita no nace 'completada'; un proveedor no puede reservar como cliente", async () => {
    await falla(cita(u.beto, lunes, "11:00", "12:00", "estado='completada'"), "pendiente o confirmada");
    await falla(cita(u.local, lunes, "11:00", "12:00"), "row-level security");
  });

  await prueba("RN-05/11: no se reserva servicio inactivo ni de otro proveedor", async () => {
    await como(db, u.local, "update servicios set activo=false where id=$1", [sid]);
    await falla(cita(u.beto, lunes, "11:00", "12:00"), "inactivo");
    await como(db, u.local, "update servicios set activo=true where id=$1", [sid]);
  });

  await prueba("el cliente solo puede cancelar (no completar) y no cambia proveedor/servicio", async () => {
    await falla(como(db, u.ana, "update citas set estado='completada' where id=$1", [c1]), "solo puedes cancelar|aún no ha empezado|no permitido");
    await falla(como(db, u.ana, "update citas set profesional_id=$2 where id=$1", [c1, pid2]), "No se puede cambiar");
    await como(db, u.beto, "update citas set estado='cancelada' where id=$1", [c2]);
    assert.equal((await db.query("select estado from citas where id=$1", [c2])).rows[0].estado, "cancelada");
    await falla(como(db, u.beto, "update citas set estado='confirmada' where id=$1", [c2]), "no permitido");   // cancelada es final
  });

  await prueba("cancelar libera el horario para otro cliente", async () => {
    await cita(u.beto, lunes, "10:00", "11:00");
  });

  await prueba("reprogramar: dentro de horario y sin cruce OK; fuera de horario o cruzando → error", async () => {
    await como(db, u.ana, "update citas set hora_inicio='11:00', hora_fin='12:00' where id=$1", [c1]);       // pegada al final: OK
    await falla(como(db, u.ana, "update citas set hora_inicio='10:30', hora_fin='11:30' where id=$1", [c1]), "sin_cruces_horario");
    await falla(como(db, u.ana, "update citas set hora_inicio='12:00', hora_fin='13:00' where id=$1", [c1]), "fuera del horario");
    await falla(como(db, u.ana, "update citas set fecha=$2 where id=$1", [c1, sumar(lunes, -7)]), "pasadas");
  });

  await prueba("completar: el proveedor solo puede si la cita ya empezó", async () => {
    await falla(como(db, u.local, "update citas set estado='completada' where id=$1", [c1]), "aún no ha empezado");
    // cita pasada creada como SQL Editor (sin JWT) para poder completarla
    await db.exec("reset role");
    await db.query(`alter table citas disable trigger validar_cita_trg`);
    const p = (await db.query(`insert into citas(cliente_id,profesional_id,servicio_id,fecha,hora_inicio,hora_fin,estado)
      values ($1,$2,$3,$4,'09:00','10:00','confirmada') returning id`, [u.ana, pid, sid, ayer])).rows[0].id;
    await db.query(`alter table citas enable trigger validar_cita_trg`);
    await como(db, u.local, "update citas set estado='completada' where id=$1", [p]);
    assert.equal((await db.query("select estado from citas where id=$1", [p])).rows[0].estado, "completada");
  });

  await prueba("RN-09: rangos_ocupados funciona para visitantes sin exponer datos de clientes", async () => {
    const r = await como(db, null, "select * from rangos_ocupados($1,$2,$2)", [pid, lunes]);
    assert.ok(r.rows.length >= 2);
    assert.deepEqual(Object.keys(r.rows[0]).sort(), ["fecha", "hora_fin", "hora_inicio"]);
    const sin = await como(db, null, "select * from rangos_ocupados($1,$2,$2,$3)", [pid, lunes, c1]);
    assert.equal(sin.rows.length, r.rows.length - 1);                                                         // p_excluir
    assert.equal((await como(db, null, "select id from citas")).rows.length, 0);                              // y la RLS de citas sigue cerrada
  });

  await prueba("un servicio con citas no se puede borrar ni siquiera como administrador de BD", async () => {
    await falla(como(db, "sql", "delete from servicios where id=$1", [sid]), "foreign key|violates");
  });
}

// ===================== MIGRACIÓN DESDE LOS SCRIPTS VIEJOS =====================
console.log("\nMigración desde schema.sql + schema_citas.sql anteriores");
{
  const db = await nuevaBD();
  await db.exec(leer("legacy/schema_v0.sql")); await db.exec(leer("legacy/schema_citas_v0.sql")); await db.exec(PERMISOS);
  const uid = (await db.query("insert into auth.users(email,raw_user_meta_data) values ('viejo@x.co','{\"nombre\":\"Viejo Pro\"}') returning id")).rows[0].id;
  await db.query("update perfiles set rol='profesional' where id=$1", [uid]);
  const pid = (await db.query("insert into profesionales(perfil_id,especialidad,estado) values ($1,'Barbería','aprobado') returning id", [uid])).rows[0].id;
  const sid = (await db.query("insert into servicios(profesional_id,nombre,precio,duracion_min) values ($1,'Corte',10000,30) returning id", [pid])).rows[0].id;
  await db.query("insert into citas(cliente_id,profesional_id,servicio_id,fecha,hora_inicio,hora_fin) values ($1,$2,$3,$4,'09:00','09:30')", [uid, pid, sid, lunes]);

  await prueba("el script nuevo migra una base existente sin perder datos", async () => {
    await db.exec(SCHEMA); await db.exec(PERMISOS);
    const p = (await db.query("select nombre_negocio, tipo_proveedor, estado from profesionales")).rows[0];
    assert.deepEqual(p, { nombre_negocio: "Viejo Pro", tipo_proveedor: "independiente", estado: "aprobado" });
    assert.equal((await db.query("select count(*)::int n from citas")).rows[0].n, 1);
  });
  await prueba("tras migrar rigen los 4 estados, el anti-cruce activo y la protección de rol", async () => {
    const est = (await db.query("select pg_get_constraintdef(oid) d from pg_constraint where conname='citas_estado_check'")).rows[0].d;
    assert.match(est, /completada/);
    const ex = (await db.query("select pg_get_constraintdef(oid) d from pg_constraint where conname='sin_cruces_horario'")).rows[0].d;
    assert.match(ex, /pendiente/);
    await falla(como(db, uid, "update perfiles set rol='admin' where id=$1", [uid]), "tipo de cuenta");
    assert.equal((await db.query("select count(*)::int n from pg_policies where tablename='citas'")).rows[0].n, 3);
    await falla(db.query("delete from servicios where id=$1", [sid]), "foreign key|violates");   // ya no hay cascade sobre citas
  });
}

console.log(`\n${ok} pruebas de SQL correctas${process.exitCode ? " — HAY FALLOS" : ""}`);
