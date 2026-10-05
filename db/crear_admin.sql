-- StyleBook · Crear la cuenta de ADMINISTRADOR
-- ---------------------------------------------------------------------------
-- El administrador NO se registra desde la app: se crea aquí, directamente en
-- la base de datos, y luego inicia sesión en /login como cualquier usuario.
--
-- Cómo usarlo:
--   1. Cambia v_email y v_pass (contraseña de mínimo 6 caracteres).
--   2. Supabase -> SQL Editor -> pega este archivo -> Run.
--   3. Entra en /login: te lleva directo al Panel admin (/admin).
--
-- IMPORTANTE: no subas este archivo a un repositorio con tu contraseña real
-- escrita. Escríbela, ejecútalo y vuelve a dejar el texto de ejemplo.
--
-- Si este script da error (Supabase cambia sus tablas internas a veces), usa la
-- alternativa segura:
--   a) Authentication -> Users -> Add user -> "Create new user"
--      (marca "Auto Confirm User").
--   b) Ejecuta solo el UPDATE del final de este archivo.
-- ---------------------------------------------------------------------------

do $$
declare
  v_id    uuid := gen_random_uuid();
  v_email text := 'TU_CORREO';          -- <-- cámbialo
  v_pass  text := 'TU_CONTRASEÑA';      -- <-- cámbiala
begin
  -- Usuario de Supabase Auth, ya confirmado
  insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at,
    confirmation_token, recovery_token, email_change_token_new, email_change
  ) values (
    '00000000-0000-0000-0000-000000000000', v_id, 'authenticated', 'authenticated',
    lower(v_email), crypt(v_pass, gen_salt('bf')),
    now(), '{"provider":"email","providers":["email"]}',
    '{"nombre":"Administrador"}',
    now(), now(),
    '', '', '', ''
  );

  insert into auth.identities (
    id, user_id, provider_id, identity_data, provider,
    last_sign_in_at, created_at, updated_at
  ) values (
    gen_random_uuid(), v_id, v_id::text,
    jsonb_build_object('sub', v_id::text, 'email', lower(v_email), 'email_verified', true),
    'email', now(), now(), now()
  );

  -- El trigger crear_perfil() ya creó su perfil como 'cliente'; lo pasamos a admin.
  -- (Desde el SQL Editor, proteger_perfil() sí permite cambiar el rol.)
  update public.perfiles set rol = 'admin' where id = v_id;
end $$;

-- ---------------------------------------------------------------------------
-- ALTERNATIVA: si ya tienes el usuario (creado desde Authentication -> Users
-- o registrado antes), solo conviértelo en admin:
--
-- update public.perfiles
-- set rol = 'admin'
-- where id = (select id from auth.users where email = 'TU_CORREO');
-- ---------------------------------------------------------------------------
