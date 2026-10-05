-- StyleBook · Diagnóstico y reparación de perfiles
-- Ejecuta cada bloque por separado en Supabase -> SQL Editor.

-- 1) DIAGNÓSTICO: ¿cada usuario tiene su perfil? (rol = null significa que NO tiene perfil)
select u.email, p.rol, p.nombre
from auth.users u
left join public.perfiles p on p.id = u.id
order by u.created_at;

-- 2) ¿Existe el trigger que crea el perfil al registrarse? Debe devolver 1 fila.
select tgname from pg_trigger where tgname = 'al_crear_usuario';

-- 3) REPARACIÓN: crea el perfil a los usuarios que no lo tengan
insert into public.perfiles (id, nombre, telefono, rol)
select u.id,
       coalesce(nullif(trim(u.raw_user_meta_data->>'nombre'), ''), 'Usuario'),
       nullif(trim(u.raw_user_meta_data->>'telefono'), ''),
       case when u.raw_user_meta_data->>'tipo_cuenta' in ('establecimiento', 'profesional')
            then u.raw_user_meta_data->>'tipo_cuenta' else 'cliente' end
from auth.users u
where not exists (select 1 from public.perfiles p where p.id = u.id);

-- 4) Convertir tu cuenta en administradora (cambia el correo)
update public.perfiles
set rol = 'admin'
where id = (select id from auth.users where email = 'TU_CORREO');
