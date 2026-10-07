-- Control remoto desde el teléfono (remoto.py). Se puede correr varias veces sin romper nada.
--
-- Seguridad: las tablas no tienen políticas (RLS sin reglas = nadie entra con la llave pública).
-- El teléfono solo puede usar las dos funciones de abajo, y ambas exigen la llave secreta del
-- teléfono (256 bits, del código QR). En la base solo se guarda su huella SHA-256, así que ni
-- quien vea la base puede hacerse pasar por tu teléfono. Jarvis usa la clave secreta del proyecto.

create table if not exists public.telefonos (
  id bigint generated always as identity primary key,
  equipo text not null,
  nombre text not null default 'Mi teléfono',
  huella text not null unique,
  creado timestamptz not null default now(),
  usado timestamptz
);

create table if not exists public.ordenes_remotas (
  id bigint generated always as identity primary key,
  equipo text not null,
  telefono bigint references public.telefonos (id) on delete cascade,
  texto text not null check (char_length(texto) between 1 and 500),
  estado text not null default 'pendiente'
    check (estado in ('pendiente', 'procesando', 'hecha', 'error')),
  respuesta text,
  creado timestamptz not null default now(),
  respondido timestamptz
);
create index if not exists ordenes_remotas_pendientes on public.ordenes_remotas (equipo, estado, id);

-- Jarvis dice "aquí estoy" cada pocos segundos: el teléfono muestra si la PC está en línea
create table if not exists public.equipos_vivos (
  equipo text primary key,
  visto timestamptz not null default now()
);

alter table public.telefonos enable row level security;
alter table public.ordenes_remotas enable row level security;
alter table public.equipos_vivos enable row level security;
revoke all on public.telefonos, public.ordenes_remotas, public.equipos_vivos from anon, authenticated;

-- El teléfono manda una orden. Máximo 20 por minuto.
create or replace function public.remoto_enviar(llave text, texto text)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  t public.telefonos;
  nuevo bigint;
begin
  select * into t from public.telefonos
   where huella = encode(sha256(convert_to(llave, 'UTF8')), 'hex');
  if not found then
    raise exception 'llave inválida' using errcode = '28000';
  end if;
  if (select count(*) from public.ordenes_remotas
       where telefono = t.id and creado > now() - interval '1 minute') >= 20 then
    raise exception 'demasiadas órdenes; espera un minuto' using errcode = '54000';
  end if;
  insert into public.ordenes_remotas (equipo, telefono, texto)
  values (t.equipo, t.id, left(btrim(texto), 500))
  returning id into nuevo;
  update public.telefonos set usado = now() where id = t.id;
  return nuevo;
end;
$$;

-- El teléfono ve si la PC está en línea y sus últimas órdenes con sus respuestas
drop function if exists public.remoto_historial(text);
create or replace function public.remoto_historial(llave text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  t public.telefonos;
begin
  select * into t from public.telefonos
   where huella = encode(sha256(convert_to(llave, 'UTF8')), 'hex');
  if not found then
    raise exception 'llave inválida' using errcode = '28000';
  end if;
  return json_build_object(
    'equipo', t.equipo,
    'en_linea', coalesce((select v.visto > now() - interval '45 seconds'
                            from public.equipos_vivos v where v.equipo = t.equipo), false),
    'ordenes', coalesce((select json_agg(o order by o.id desc) from (
        select id, texto, estado, respuesta, creado, respondido
          from public.ordenes_remotas where telefono = t.id
         order by id desc limit 30) o), '[]'::json));
end;
$$;

revoke all on function public.remoto_enviar(text, text) from public;
revoke all on function public.remoto_historial(text) from public;
grant execute on function public.remoto_enviar(text, text) to anon, authenticated;
grant execute on function public.remoto_historial(text) to anon, authenticated;
grant execute on function public.remoto_enviar(text, text), public.remoto_historial(text) to service_role;

-- que la API de Supabase vea ya las funciones nuevas
notify pgrst, 'reload schema';
