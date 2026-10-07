-- Esquema de Supabase para Jarvis (memoria por significado + respaldos).
-- Se corre UNA vez en el SQL Editor del proyecto (Jarvis lo copia al portapapeles al conectar).
-- Es idempotente: correrlo otra vez no borra nada.

create extension if not exists vector with schema extensions;

-- Lo que Jarvis recuerda (tus mensajes y los datos que sabe de ti), con su "significado"
-- (embedding de 768 números del modelo abierto paraphrase-multilingual, calculado en tu PC).
create table if not exists public.recuerdos (
  id         text primary key,          -- "<equipo>:<m|h><id local>"
  equipo     text not null,             -- de qué computadora vino
  tipo       text not null,             -- 'mensaje' | 'hecho'
  texto      text not null,
  creado     timestamptz not null,
  embedding  vector(768) not null
);

create index if not exists recuerdos_embedding_idx
  on public.recuerdos using hnsw (embedding vector_cosine_ops);

-- Búsqueda por significado: los más parecidos a la consulta (similitud de coseno, 1 = idéntico)
create or replace function public.buscar_recuerdos(
  consulta vector(768), cuantos int default 8, tipo_filtro text default null)
returns table (id text, equipo text, tipo text, texto text, creado timestamptz, similitud float)
language sql stable
set search_path = public, extensions
as $$
  select r.id, r.equipo, r.tipo, r.texto, r.creado, 1 - (r.embedding <=> consulta) as similitud
  from public.recuerdos r
  where tipo_filtro is null or r.tipo = tipo_filtro
  order by r.embedding <=> consulta
  limit cuantos;
$$;

-- Seguridad: con RLS activo y SIN políticas, nadie con la clave pública puede leer ni escribir.
-- Solo la clave secreta (la que guarda Jarvis, cifrada en tu PC) tiene acceso.
alter table public.recuerdos enable row level security;
revoke all on function public.buscar_recuerdos(vector, int, text) from anon, authenticated;
