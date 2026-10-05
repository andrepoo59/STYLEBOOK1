-- ============================================================
-- StyleBook · Sprint 1 (HU-01 a HU-07)
-- Pegar completo en Supabase → SQL Editor → Run
-- ============================================================

-- ---------- 1. PERFILES (HU-01, HU-03) ----------
-- Todo usuario que se registra (cliente o profesional) tiene un perfil.
create table if not exists public.perfiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  nombre     text not null,
  telefono   text,
  rol        text not null default 'cliente'
             check (rol in ('cliente', 'profesional', 'admin')),
  creado_en  timestamptz not null default now()
);

-- El perfil se crea solo al registrarse (HU-01)
create or replace function public.crear_perfil()   
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.perfiles (id, nombre)
  values (new.id, coalesce(new.raw_user_meta_data->>'nombre', 'Usuario'));
  return new;
end;
$$;

drop trigger if exists al_crear_usuario on auth.users;
create trigger al_crear_usuario
  after insert on auth.users
  for each row execute function public.crear_perfil();

-- ---------- 2. PROFESIONALES (HU-04, HU-07) ----------
-- Estilistas / barberos del establecimiento.
create table if not exists public.profesionales (
  id                uuid primary key default gen_random_uuid(),
  perfil_id         uuid not null unique references public.perfiles(id) on delete cascade,
  especialidad      text not null,      -- ej. 'Barbería', 'Colorimetría', 'Uñas'
  descripcion       text,
  telefono          text,
  anios_experiencia int default 0,
  foto_url          text,
  -- El administrador del negocio aprueba o rechaza a cada profesional
  -- antes de que aparezca públicamente (rol 'admin' en perfiles).
  estado            text not null default 'pendiente'
                    check (estado in ('pendiente', 'aprobado', 'rechazado')),
  creado_en         timestamptz not null default now()
);

-- ---------- 3. SERVICIOS (HU-05, HU-06) ----------
-- Catálogo de servicios que ofrece cada profesional.
create table if not exists public.servicios (
  id             uuid primary key default gen_random_uuid(),
  profesional_id uuid not null references public.profesionales(id) on delete cascade,
  nombre         text not null,          -- ej. 'Corte clásico', 'Manicure spa'
  categoria      text not null default 'general',
  descripcion    text,
  precio         numeric(12,2) not null check (precio >= 0),
  duracion_min   int not null default 30 check (duracion_min between 10 and 480),
  activo         boolean not null default true,
  creado_en      timestamptz not null default now()
);

create index if not exists idx_servicios_profesional on public.servicios(profesional_id);
create index if not exists idx_profesionales_especialidad on public.profesionales(especialidad);

-- ============================================================
-- 4. SEGURIDAD (Row Level Security)
-- ============================================================
alter table public.perfiles      enable row level security;
alter table public.profesionales enable row level security;
alter table public.servicios     enable row level security;

-- Perfiles: cada quien ve y edita el suyo
drop policy if exists "perfil propio lectura" on public.perfiles;
create policy "perfil propio lectura" on public.perfiles
  for select using (auth.uid() = id);

drop policy if exists "perfil propio edicion" on public.perfiles;
create policy "perfil propio edicion" on public.perfiles
  for update using (auth.uid() = id) with check (auth.uid() = id);

-- Profesionales: cualquiera consulta (HU-07), solo el dueño escribe (HU-04)
drop policy if exists "profesionales lectura" on public.profesionales;
create policy "profesionales lectura" on public.profesionales
  for select using (true);

drop policy if exists "profesional alta propia" on public.profesionales;
create policy "profesional alta propia" on public.profesionales
  for insert with check (auth.uid() = perfil_id);

drop policy if exists "profesional edicion propia" on public.profesionales;
create policy "profesional edicion propia" on public.profesionales
  for update using (auth.uid() = perfil_id) with check (auth.uid() = perfil_id);

-- Administrador: aprueba o rechaza el registro de cualquier profesional
drop policy if exists "admin gestiona profesionales" on public.profesionales;
create policy "admin gestiona profesionales" on public.profesionales
  for update using (
    exists (select 1 from public.perfiles p where p.id = auth.uid() and p.rol = 'admin')
  ) with check (
    exists (select 1 from public.perfiles p where p.id = auth.uid() and p.rol = 'admin')
  );

-- Servicios: cualquiera consulta (HU-06), solo el profesional dueño gestiona (HU-05)
drop policy if exists "servicios lectura" on public.servicios;
create policy "servicios lectura" on public.servicios
  for select using (true);

drop policy if exists "servicios alta propia" on public.servicios;
create policy "servicios alta propia" on public.servicios
  for insert with check (
    exists (select 1 from public.profesionales p
            where p.id = profesional_id and p.perfil_id = auth.uid())
  );

drop policy if exists "servicios edicion propia" on public.servicios;
create policy "servicios edicion propia" on public.servicios
  for update using (
    exists (select 1 from public.profesionales p
            where p.id = profesional_id and p.perfil_id = auth.uid())
  );

drop policy if exists "servicios baja propia" on public.servicios;
create policy "servicios baja propia" on public.servicios
  for delete using (
    exists (select 1 from public.profesionales p
            where p.id = profesional_id and p.perfil_id = auth.uid())
  );

-- ---------- 5. CITAS (HU-06: cancelar / reprogramar) ----------
create table if not exists public.citas (
  id             uuid primary key default gen_random_uuid(),
  cliente_id     uuid not null references public.perfiles(id) on delete cascade,
  profesional_id uuid not null references public.profesionales(id) on delete cascade,
  servicio_id    uuid not null references public.servicios(id) on delete cascade,
  fecha          date not null,
  hora_inicio    time not null,
  hora_fin       time not null,
  estado         text not null default 'confirmada'
                 check (estado in ('pendiente', 'confirmada', 'cancelada', 'completada')),
  creado_en      timestamptz not null default now()
);

create index if not exists idx_citas_cliente on public.citas(cliente_id);
create index if not exists idx_citas_profesional_fecha on public.citas(profesional_id, fecha);

alter table public.citas enable row level security;

-- El cliente ve solo sus propias citas
drop policy if exists "citas propias lectura cliente" on public.citas;
create policy "citas propias lectura cliente" on public.citas
  for select using (auth.uid() = cliente_id);

-- El profesional ve las citas que le corresponden
drop policy if exists "citas propias lectura profesional" on public.citas;
create policy "citas propias lectura profesional" on public.citas
  for select using (
    exists (select 1 from public.profesionales p
            where p.id = profesional_id and p.perfil_id = auth.uid())
  );

-- El cliente solo puede modificar (cancelar/reprogramar) sus propias citas
drop policy if exists "citas edicion propia cliente" on public.citas;
create policy "citas edicion propia cliente" on public.citas
  for update using (auth.uid() = cliente_id) with check (auth.uid() = cliente_id);