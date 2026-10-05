-- ============================================================
-- StyleBook · HU-03 (disponibilidad) y HU-04 (reservas)
-- ============================================================

create table if not exists public.horarios_profesional (
  id             uuid primary key default gen_random_uuid(),
  profesional_id uuid not null references public.profesionales(id) on delete cascade,
  dia_semana     smallint not null check (dia_semana between 0 and 6), -- 0=Lunes ... 6=Domingo
  hora_inicio    time not null,
  hora_fin       time not null check (hora_fin > hora_inicio),
  creado_en      timestamptz not null default now()
);

create index if not exists idx_horarios_profesional
  on public.horarios_profesional(profesional_id, dia_semana);

create table if not exists public.citas (
  id             uuid primary key default gen_random_uuid(),
  cliente_id     uuid not null references public.perfiles(id) on delete cascade,
  profesional_id uuid not null references public.profesionales(id) on delete cascade,
  servicio_id    uuid not null references public.servicios(id) on delete cascade,
  fecha          date not null,
  hora_inicio    time not null,
  hora_fin       time not null check (hora_fin > hora_inicio),
  estado         text not null default 'confirmada'
                 check (estado in ('confirmada', 'cancelada')),
  creado_en      timestamptz not null default now()
);

create index if not exists idx_citas_profesional_fecha on public.citas(profesional_id, fecha);
create index if not exists idx_citas_cliente on public.citas(cliente_id);

create extension if not exists btree_gist;

alter table public.citas
  add constraint sin_cruces_horario
  exclude using gist (
    profesional_id with =,
    tsrange((fecha + hora_inicio)::timestamp, (fecha + hora_fin)::timestamp, '[)') with &&
  )
  where (estado = 'confirmada');

alter table public.horarios_profesional enable row level security;
alter table public.citas enable row level security;

create policy "horarios lectura publica" on public.horarios_profesional
  for select using (true);

create policy "horarios gestion propia" on public.horarios_profesional
  for all using (
    exists (select 1 from public.profesionales p
            where p.id = profesional_id and p.perfil_id = auth.uid())
  ) with check (
    exists (select 1 from public.profesionales p
            where p.id = profesional_id and p.perfil_id = auth.uid())
  );

create policy "citas lectura propia" on public.citas
  for select using (
    cliente_id = auth.uid()
    or exists (select 1 from public.profesionales p
               where p.id = profesional_id and p.perfil_id = auth.uid())
  );

create policy "citas alta propia" on public.citas
  for insert with check (cliente_id = auth.uid());

create policy "citas cancelacion propia" on public.citas
  for update using (
    cliente_id = auth.uid()
    or exists (select 1 from public.profesionales p
               where p.id = profesional_id and p.perfil_id = auth.uid())
  );