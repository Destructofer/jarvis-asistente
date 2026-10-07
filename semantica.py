"""Memoria por SIGNIFICADO: Jarvis encuentra "lo que te dije del viaje" aunque hayas dicho
"vacaciones en Oaxaca".

Cada mensaje tuyo y cada dato que sabe de ti se convierte en un "embedding" (768 números que
representan su significado) con un modelo abierto que corre en tu PC, en el procesador
(paraphrase-multilingual vía Ollama, ~30 ms por frase, sin internet y sin ocupar la tarjeta de
video que usan Whisper y qwen). La búsqueda compara significados (similitud de coseno).

Dónde vive:
- En tu PC (datos/genesis.db, tabla vectores): de ahí se busca siempre, rápido y sin internet.
- En Supabase (tabla recuerdos, con pgvector): una copia que se sube en segundo plano (nube.py),
  para no perderla y para otras computadoras. Si en esta PC aún no hay índice, se busca allá.

Privacidad: lo que olvidas ("olvida lo de...", "olvida todo", o los mensajes viejos que se
descartan) se borra también del índice y de Supabase en la siguiente pasada (≤ 1 min).
"""
import threading
import time

import numpy as np

import memoria
import nube

MODELO = "paraphrase-multilingual"
DIM = 768
MIN_CARACTERES = 12      # "sí", "ok", "gracias": no vale la pena indexarlos
SIMILITUD_MINIMA = 0.45  # por debajo, no se parece lo suficiente para mostrarlo
LOTE = 32

_estado = {"hilo": None, "matriz": None, "filas": None, "version": -1, "cambios": 0,
           "error": "", "ultima_subida": 0.0}
_lock = threading.Lock()


# ---------- Almacén local ----------
def _q(sql, params=(), escribir=False, muchos=False):
    con = memoria._conectar()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS vectores (id TEXT PRIMARY KEY, tipo TEXT NOT NULL, "
                    "ref INTEGER NOT NULL, texto TEXT NOT NULL, creado TEXT NOT NULL, "
                    "vector BLOB NOT NULL, subido INTEGER DEFAULT 0)")
        # Lo olvidado que ya estaba en Supabase: se reintenta borrarlo allá hasta que se pueda
        con.execute("CREATE TABLE IF NOT EXISTS borrar_en_nube (id TEXT PRIMARY KEY)")
        if muchos:
            con.executemany(sql, params)
            filas = []
        else:
            filas = con.execute(sql, params).fetchall()
        if escribir:
            con.commit()
        return filas
    finally:
        con.close()


def embeddings(textos):
    """Matriz (n, 768) normalizada. En el procesador (num_gpu 0): la tarjeta de video ya va
    llena con Whisper y el modelo de lenguaje, y cargar otro ahí los sacaría de memoria."""
    import ollama
    r = ollama.embed(model=MODELO, input=list(textos), options={"num_gpu": 0}, keep_alive="30m")
    m = np.asarray(r["embeddings"], dtype=np.float32)
    m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    return m


def _fuentes():
    """Lo que debería estar indexado: {id: (tipo, ref, texto, creado)}."""
    res = {}
    for f in memoria._q("SELECT id, ts, contenido FROM mensajes WHERE rol = 'user'"):
        if len((f["contenido"] or "").strip()) >= MIN_CARACTERES:
            res[f"m{f['id']}"] = ("mensaje", f["id"], f["contenido"].strip(), f["ts"])
    for f in memoria._q("SELECT id, texto, creado FROM hechos"):
        res[f"h{f['id']}"] = ("hecho", f["id"], f["texto"].strip(), f["creado"])
    return res


def _id_nube(local_id):
    return f"{nube.EQUIPO}:{local_id}"


def indexar(limite_lotes=20):
    """Una pasada: indexa lo nuevo, borra lo olvidado (local y en la nube). Devuelve cuántos
    agregó y cuántos borró."""
    fuentes = _fuentes()
    actuales = {f["id"] for f in _q("SELECT id FROM vectores")}
    nuevos = [i for i in fuentes if i not in actuales]
    viejos = [i for i in actuales if i not in fuentes]
    if viejos:
        en_nube = [f["id"] for f in _q("SELECT id FROM vectores WHERE subido = 1")
                   if f["id"] in set(viejos)]
        _q("INSERT OR IGNORE INTO borrar_en_nube (id) VALUES (?)", [(i,) for i in en_nube],
           escribir=True, muchos=True)
        _q("DELETE FROM vectores WHERE id = ?", [(i,) for i in viejos], escribir=True, muchos=True)
    agregados = 0
    for k in range(0, min(len(nuevos), LOTE * limite_lotes), LOTE):
        lote = nuevos[k:k + LOTE]
        m = embeddings([fuentes[i][2][:1000] for i in lote])
        _q("INSERT OR REPLACE INTO vectores (id, tipo, ref, texto, creado, vector, subido) "
           "VALUES (?, ?, ?, ?, ?, ?, 0)",
           [(i, fuentes[i][0], fuentes[i][1], fuentes[i][2], fuentes[i][3], m[j].tobytes())
            for j, i in enumerate(lote)], escribir=True, muchos=True)
        agregados += len(lote)
    if agregados or viejos:
        _estado["cambios"] += 1
    return agregados, len(viejos)


def borrar_pendientes_en_nube():
    """Borra en Supabase lo que olvidaste (si falla, queda pendiente y se reintenta)."""
    ids = [f["id"] for f in _q("SELECT id FROM borrar_en_nube")]
    if not ids or not nube.lista():
        return 0
    for k in range(0, len(ids), 50):
        lote = ids[k:k + 50]
        lista = ",".join(f'"{_id_nube(i)}"' for i in lote)
        nube.peticion("DELETE", f"/rest/v1/recuerdos?id=in.({lista})")
        _q("DELETE FROM borrar_en_nube WHERE id = ?", [(i,) for i in lote], escribir=True, muchos=True)
    return len(ids)


def subir(maximo=500):
    """Sube a Supabase lo que aún no está allá (en lotes de 100). Devuelve cuántos subió."""
    if not nube.lista():
        return 0
    borrar_pendientes_en_nube()   # primero lo olvidado: que no quede nada que no quieres allá
    filas = [dict(f) for f in _q("SELECT id, tipo, texto, creado, vector FROM vectores WHERE subido = 0 "
                                 "LIMIT ?", (maximo,))]
    subidos = 0
    for k in range(0, len(filas), 100):
        lote = filas[k:k + 100]
        cuerpo = [{"id": _id_nube(f["id"]), "equipo": nube.EQUIPO, "tipo": f["tipo"], "texto": f["texto"],
                   "creado": f["creado"],
                   "embedding": "[" + ",".join(f"{x:.6f}" for x in np.frombuffer(f["vector"], dtype=np.float32)) + "]"}
                  for f in lote]
        nube.peticion("POST", "/rest/v1/recuerdos", json=cuerpo,
                      encabezados={"Prefer": "resolution=merge-duplicates,return=minimal"})
        _q("UPDATE vectores SET subido = 1 WHERE id = ?", [(f["id"],) for f in lote],
           escribir=True, muchos=True)
        subidos += len(lote)
    if subidos:
        _estado["ultima_subida"] = time.time()
    return subidos


# ---------- Buscar ----------
def _matriz():
    """Todos los vectores locales en memoria (se recargan solo si cambiaron)."""
    with _lock:
        if _estado["version"] != _estado["cambios"] or _estado["matriz"] is None:
            filas = [dict(f) for f in _q("SELECT id, tipo, ref, texto, creado, vector FROM vectores")]
            if filas:
                _estado["matriz"] = np.vstack([np.frombuffer(f["vector"], dtype=np.float32) for f in filas])
            else:
                _estado["matriz"] = np.zeros((0, DIM), dtype=np.float32)
            _estado["filas"] = filas
            _estado["version"] = _estado["cambios"]
        return _estado["matriz"], _estado["filas"]


def buscar(consulta, k=8, tipo=None, minimo=SIMILITUD_MINIMA, desde=None, hasta=None):
    """[{tipo, ref, texto, creado, similitud}] de lo más parecido en significado, de más a menos.
    desde/hasta: fechas ISO para acotar (como "la semana pasada")."""
    consulta = (consulta or "").strip()
    if not consulta:
        return []
    v = embeddings([consulta])[0]
    m, filas = _matriz()
    if len(filas) == 0:
        return _buscar_en_nube(v, k, tipo, minimo)
    s = m @ v
    orden = np.argsort(-s)
    res = []
    for i in orden:
        if s[i] < minimo or len(res) >= k:
            break
        f = filas[i]
        if tipo and f["tipo"] != tipo:
            continue
        if (desde and f["creado"] < desde) or (hasta and f["creado"] >= hasta):
            continue
        res.append({"tipo": f["tipo"], "ref": f["ref"], "texto": f["texto"], "creado": f["creado"],
                    "similitud": float(s[i])})
    return res


def _buscar_en_nube(v, k, tipo, minimo):
    """Sin índice local (otra PC, o recién instalado): se le pregunta a Supabase."""
    if not nube.lista():
        return []
    try:
        r = nube.peticion("POST", "/rest/v1/rpc/buscar_recuerdos", json={
            "consulta": "[" + ",".join(f"{x:.6f}" for x in v) + "]", "cuantos": k,
            "tipo_filtro": tipo})
    except nube.NubeError:
        return []
    return [{"tipo": f["tipo"], "ref": None, "texto": f["texto"], "creado": f["creado"],
             "similitud": float(f["similitud"])} for f in r.json() if f["similitud"] >= minimo]


def contexto(texto, k=3, minimo=0.55):
    """Para el prompt: lo de conversaciones PASADAS que tiene que ver con lo que acabas de decir
    (no lo de la última hora, que ya está en la conversación). '' si no hay nada relevante."""
    if not disponible() or len((texto or "").strip()) < MIN_CARACTERES:
        return ""
    try:
        import datetime
        hace_una_hora = (datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat(timespec="seconds")
        res = [r for r in buscar(texto, k=k * 3, minimo=minimo) if r["creado"] < hace_una_hora
               and r["texto"].strip().lower() != texto.strip().lower()][:k]
    except Exception:
        return ""
    if not res:
        return ""
    return ("\n\nDE CONVERSACIONES PASADAS (por si viene al caso; no lo recites): "
            + " | ".join(f"[{r['creado'][:10]}] {'dijo' if r['tipo'] == 'mensaje' else 'sabes'}: "
                         f"{r['texto'][:160]}" for r in res))


def disponible():
    """¿Hay índice y modelo? (sin Ollama, Jarvis sigue con la búsqueda por palabras)."""
    return not _estado["error"] and _estado["hilo"] is not None


# ---------- En segundo plano ----------
def _bucle():
    import ollama
    try:
        ollama.show(MODELO)
    except Exception:
        try:
            print(f"[Memoria: descargando el modelo de significado {MODELO} (~560 MB)...]")
            ollama.pull(MODELO)
        except Exception as e:
            _estado["error"] = f"sin modelo de embeddings ({type(e).__name__})"
            print(f"[Memoria por significado desactivada: {_estado['error']}]")
            return
    while True:
        try:
            agregados, borrados = indexar()
            if agregados or borrados:
                print(f"[Memoria: {agregados} recuerdos indexados, {borrados} olvidados]")
            subidos = subir()
            if subidos:
                print(f"[Memoria: {subidos} recuerdos copiados a Supabase]")
        except nube.NubeError as e:
            print(f"[Memoria: Supabase: {e}]")
        except Exception as e:
            print(f"[Memoria: {type(e).__name__}: {str(e)[:100]}]")
        time.sleep(60)


def iniciar():
    if _estado["hilo"] is None:
        _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="memoria-semantica")
        _estado["hilo"].start()
