import datetime
import re
import sqlite3
from pathlib import Path

import skills
from skills import skill

DB_PATH = Path(__file__).parent / "datos" / "genesis.db"
_CFG = {}

# genesis.py instala aquí la función que pide confirmación al usuario
pedir_confirmacion = None

PROHIBIDO = re.compile(
    r"contrase[ñn]a|password|passcode|\bpin\b|\bcvv\b|\btoken\b|api[ _-]?key|clave (de|del|para)",
    re.I)
NUMERO_LARGO = re.compile(r"\d[\d\s-]{11,}\d")  # 13+ dígitos: parece tarjeta o cuenta

STOP = {"mis", "los", "las", "del", "que", "una", "uno", "por", "para", "con",
        "sobre", "acerca", "algo", "todo", "sus", "como"}

MSG_OFF = "La memoria está desactivada en la configuración."


# ---------- Base de datos ----------
def _conectar():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS hechos ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "texto TEXT NOT NULL, creado TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS mensajes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ts TEXT NOT NULL, rol TEXT NOT NULL, contenido TEXT NOT NULL)")
    return con


def _q(sql, params=(), escribir=False):
    con = _conectar()
    try:
        filas = con.execute(sql, params).fetchall()
        if escribir:
            con.commit()
        return filas
    finally:
        con.close()


def _ahora():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _mem():
    return _CFG.get("memoria", {})


def _activa():
    return _mem().get("activa", True)


def iniciar(cfg):
    global _CFG
    _CFG = cfg
    # Conserva los últimos N mensajes (memoria.max_mensajes): con 500 se borraba lo de hace un
    # par de días y "¿de qué hablamos la semana pasada?" no tenía respuesta
    _q("DELETE FROM mensajes WHERE id NOT IN "
       "(SELECT id FROM mensajes ORDER BY id DESC LIMIT ?)",
       (int(_mem().get("max_mensajes", 5000)),), escribir=True)


# ---------- Recuerdos ----------
def listar_hechos():
    return _q("SELECT id, texto, creado FROM hechos ORDER BY id")


def agregar_hecho(texto):
    texto = " ".join(texto.split())
    nuevo = skills._norm(texto)
    for h in listar_hechos():
        if skills._norm(h["texto"]) == nuevo:
            return False
    _q("INSERT INTO hechos (texto, creado) VALUES (?, ?)", (texto, _ahora()), escribir=True)
    return True


def _tokens(tema):
    return [t for t in skills._norm(tema).split() if len(t) >= 3 and t not in STOP]


def buscar(tema):
    """Recuerdos que contienen todas las palabras clave del tema."""
    tokens = _tokens(tema)
    if not tokens:
        return []
    return [h for h in listar_hechos()
            if all(t[:5] in skills._norm(h["texto"]) for t in tokens)]


def _por_significado(tema, tipo, k=8, minimo=0.5, desde=None, hasta=None):
    """refs (ids locales) de lo que se parece en SIGNIFICADO (semantica.py); [] si no está."""
    try:
        import semantica
        if not semantica.disponible():
            return []
        return [r["ref"] for r in semantica.buscar(tema, k=k, tipo=tipo, minimo=minimo,
                                                  desde=desde, hasta=hasta) if r["ref"] is not None]
    except Exception:
        return []


def borrar_hechos(ids):
    if ids:
        _q(f"DELETE FROM hechos WHERE id IN ({','.join('?' * len(ids))})",
           tuple(ids), escribir=True)


# ---------- Historial ----------
def guardar_mensaje(rol, texto):
    if _activa() and texto:
        _q("INSERT INTO mensajes (ts, rol, contenido) VALUES (?, ?, ?)",
           (_ahora(), rol, texto), escribir=True)


def cargar_mensajes(turnos):
    if not _activa() or turnos <= 0:
        return []
    filas = _q("SELECT rol, contenido FROM mensajes ORDER BY id DESC LIMIT ?",
               (turnos * 2,))
    msgs = [{"role": f["rol"], "content": f["contenido"]} for f in reversed(filas)]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


# ---------- Prompt de sistema ----------
def prompt_sistema(personalidad):
    """Personalidad + lo que Genesis sabe del usuario. Se regenera en cada turno."""
    if not _activa():
        return personalidad
    hechos = listar_hechos()[-_mem().get("max_hechos", 60):]
    texto = (personalidad +
             "\n\nMEMORIA PERMANENTE: usa la herramienta recordar solo cuando el usuario "
             "te pida recordar, anotar o guardar algo, y olvidar cuando pida olvidar. "
             "Nunca guardes contraseñas, claves ni datos bancarios.")
    if hechos:
        texto += ("\nLo que ya sabes del usuario (úsalo con naturalidad cuando sea "
                  "relevante, sin recitarlo):\n" +
                  "\n".join(f"- {h['texto']}" for h in hechos))
    else:
        texto += "\nAún no sabes nada personal del usuario."
    return texto


# ---------- Skills ----------
@skill("recordar",
       "Guarda un dato o preferencia del usuario en la memoria permanente. Úsala cuando el "
       "usuario diga 'recuerda que', 'anota', 'guarda' o similar. Escribe el dato como frase "
       "completa en tercera persona, por ejemplo 'El usuario prefiere el café sin azúcar'. "
       "Nunca para contraseñas ni datos bancarios.",
       {"dato": {"type": "string", "description": "El dato a recordar, como frase completa"}},
       # sensible: lo recordado entra al prompt de sistema en CADA turno como si lo hubiera
       # dicho el usuario; un mensaje de Teams no debe poder "sembrar" una instrucción ahí
       sensible=True)
def recordar(dato):
    if not _activa():
        return MSG_OFF
    dato = " ".join(str(dato).split())
    if len(dato) < 3 or len(dato) > 300:
        return "El dato debe tener entre 3 y 300 caracteres."
    if PROHIBIDO.search(dato) or NUMERO_LARGO.search(dato):
        return "Por seguridad no guardo contraseñas, claves ni datos bancarios."
    return "Guardado." if agregar_hecho(dato) else "Ya lo tenía guardado."


@skill("consultar_memoria",
       "Consulta lo que hay guardado en la memoria permanente. Con un tema, busca solo lo "
       "relacionado; sin tema, lista todo lo que sabes del usuario.",
       {"tema": {"type": "string", "description": "Tema a buscar (opcional)"}},
       requeridos=[], terminal=False)
def consultar_memoria(tema=""):
    if not _activa():
        return MSG_OFF
    if _tokens(tema):
        hits = buscar(tema)
        ids = {h["id"] for h in hits}
        # también lo que significa lo mismo con otras palabras ("mascota" -> "mi perro Rocky")
        refs = [r for r in _por_significado(tema, "hecho", k=5) if r not in ids]
        if refs:
            por_id = {h["id"]: h for h in listar_hechos()}
            hits += [por_id[r] for r in refs if r in por_id]
        if not hits:
            return "No tengo nada guardado sobre ese tema."
    else:
        hits = listar_hechos()
        if not hits:
            return "Todavía no tengo nada guardado."
    texto = "Recuerdos: " + " | ".join(h["texto"] for h in hits)
    if not _tokens(tema):  # "¿qué sabes de mí?": también lo que te pidió que hicieras siempre
        try:
            import preferencias
            extra = preferencias.ver_preferencias()
            if not extra.startswith("Todavía"):
                texto += " " + extra
        except Exception:
            pass
    return texto


# ---------- Conversaciones pasadas ----------
def _rango(cuando):
    """'hoy', 'ayer', 'la semana pasada', '2026-10-02' -> (desde, hasta) en ISO, o (None, None)."""
    t = skills._norm(cuando or "")
    hoy = datetime.date.today()
    dias = {"hoy": (0, 1), "ayer": (1, 1), "antier": (2, 1), "anteayer": (2, 1),
            "esta semana": (hoy.weekday(), hoy.weekday() + 1),
            "la semana pasada": (hoy.weekday() + 7, 7), "semana pasada": (hoy.weekday() + 7, 7),
            "este mes": (hoy.day - 1, hoy.day)}
    if t in ("hace rato", "hace un rato", "hace un momento", "hace poco", "recien", "esta manana",
             "esta tarde", "esta noche", "hoy temprano", "en la manana", "al rato"):
        t = "hoy"
    if t in dias:
        atras, n = dias[t]
        desde = hoy - datetime.timedelta(days=atras)
        return desde.isoformat(), (desde + datetime.timedelta(days=n)).isoformat()
    m = re.match(r"(\d{4})\s(\d{1,2})\s(\d{1,2})$", t)
    if m:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.isoformat(), (d + datetime.timedelta(days=1)).isoformat()
    return None, None


# Las preguntas del tipo "¿de qué hablamos de X?" no son la conversación sobre X: se saltan
PREGUNTA_RECUERDO = re.compile(r"\b(de que hablamos|que hablamos|que te dije|que te conte|que te pedi|"
                               r"que te habia|recuerdas|te acuerdas|que me dijiste)\b")


@skill("recordar_conversacion",
       "Busca en las conversaciones pasadas con el usuario (de otros días también): qué se dijo "
       "sobre un tema o qué se habló en una fecha. Úsala para '¿de qué hablamos ayer?', '¿qué me "
       "dijiste del examen?', '¿qué te había pedido la semana pasada?', 'como te comenté antes...'.",
       {"tema": {"type": "string", "description": "Palabras del tema (opcional)"},
        "cuando": {"type": "string", "description": "hoy, ayer, antier, esta semana, la semana pasada, "
                                                    "este mes o AAAA-MM-DD (opcional)"}},
       requeridos=[], terminal=False)
def recordar_conversacion(tema="", cuando=""):
    if not _activa():
        return MSG_OFF
    desde, hasta = _rango(cuando)  # una fecha que no entienda no impide buscar el tema
    sql, params = "SELECT id, ts, rol, contenido FROM mensajes", []
    if desde:
        sql += " WHERE ts >= ? AND ts < ?"
        params += [desde, hasta]
    filas = _q(sql + " ORDER BY id", tuple(params))
    tokens = _tokens(tema)
    if tokens:  # lo que DIJO el usuario sobre el tema, con la respuesta que siguió (si se buscara
        # también en las respuestas, un "no recuerdo haber hablado de eso" tapaba lo importante)
        idx = [i for i, f in enumerate(filas) if f["rol"] == "user"
               and not PREGUNTA_RECUERDO.search(skills._norm(f["contenido"]))
               and sum(t[:5] in skills._norm(f["contenido"]) for t in tokens) >= max(1, len(tokens) // 2)]
        # + por significado: "lo que te dije del viaje" encuentra "me voy a Oaxaca en diciembre"
        refs = set(_por_significado(tema, "mensaje", k=6, desde=desde, hasta=hasta))
        if refs:
            idx += [i for i, f in enumerate(filas) if f["id"] in refs and i not in idx
                    and not PREGUNTA_RECUERDO.search(skills._norm(f["contenido"]))]
        elegidos = sorted({j for i in idx for j in (i, i + 1) if j < len(filas)})
        filas = [filas[j] for j in elegidos]
    if not filas:
        return "No encontré conversaciones sobre eso" + (f" ({cuando})." if cuando else ".")
    lineas, total = [], 0
    for f in reversed(filas):  # lo más reciente primero, hasta ~1800 caracteres
        quien = "Usuario" if f["rol"] == "user" else "Jarvis"
        linea = f"[{f['ts'][:16].replace('T', ' ')}] {quien}: {f['contenido'][:220]}"
        total += len(linea)
        if total > 1800:
            break
        lineas.append(linea)
    return "Conversaciones encontradas (más recientes primero):\n" + "\n".join(lineas)


@skill("olvidar",
       "Borra de la memoria permanente los datos que coincidan con un tema. Úsala cuando el "
       "usuario pida olvidar o borrar algo que le recordaste. Pide la confirmación por sí misma.",
       {"tema": {"type": "string", "description": "Tema o dato a olvidar"}})
def olvidar(tema):
    if not _activa():
        return MSG_OFF
    hits = buscar(tema)
    # Antes solo se borraban los datos guardados y las CONVERSACIONES donde lo dijiste seguían
    # ahí (y se podían volver a recordar). Ahora también: tu mensaje y la respuesta que siguió.
    mensajes = mensajes_sobre(tema)
    if not hits and not mensajes:
        return "No encontré nada guardado sobre ese tema."
    if pedir_confirmacion is None:
        return "No puedo pedir confirmación ahora, así que no borré nada."
    if hits and len(hits) <= 3:
        pregunta = "Voy a borrar: " + "; ".join(h["texto"] for h in hits)
    else:
        pregunta = f"Encontré {len(hits)} recuerdos sobre eso" if hits else "Voy a borrar"
    if mensajes:
        pregunta += f" y {len(mensajes)} mensaje{'s' if len(mensajes) != 1 else ''} de conversaciones donde lo mencionaste"
    if not pedir_confirmacion(pregunta + ". ¿Confirmas?"):
        return "El usuario canceló. No se borró nada."
    borrar_hechos([h["id"] for h in hits])
    borrar_mensajes(mensajes)
    return f"Olvidado ({len(hits) + len(mensajes)})."


def mensajes_sobre(tema):
    """ids de tus mensajes que hablan del tema (todas sus palabras clave) y de la respuesta de
    Jarvis que siguió a cada uno."""
    tokens = _tokens(tema)
    if not tokens:
        return []
    filas = _q("SELECT id, rol, contenido FROM mensajes ORDER BY id")
    ids = []
    for i, f in enumerate(filas):
        if f["rol"] == "user" and all(t[:5] in skills._norm(f["contenido"]) for t in tokens):
            ids.append(f["id"])
            if i + 1 < len(filas) and filas[i + 1]["rol"] != "user":
                ids.append(filas[i + 1]["id"])
    return ids


def borrar_mensajes(ids):
    if ids:
        _q(f"DELETE FROM mensajes WHERE id IN ({','.join('?' * len(ids))})", tuple(ids), escribir=True)


@skill("olvidar_todo", "Borra TODA la memoria permanente de recuerdos. Pide confirmación.")
def olvidar_todo():
    if not _activa():
        return MSG_OFF
    hechos = listar_hechos()
    if not hechos:
        return "No había nada guardado."
    if pedir_confirmacion is None:
        return "No puedo pedir confirmación ahora, así que no borré nada."
    if not pedir_confirmacion(f"Voy a borrar los {len(hechos)} recuerdos que tengo. ¿Seguro?"):
        return "El usuario canceló. No se borró nada."
    borrar_hechos([h["id"] for h in hechos])
    return "Toda la memoria fue borrada."


if __name__ == "__main__":
    hs = listar_hechos()
    n = _q("SELECT COUNT(*) AS n FROM mensajes")[0]["n"]
    print(f"{len(hs)} recuerdos y {n} mensajes en {DB_PATH}")
    for h in hs:
        print(f"  [{h['id']}] {h['texto']}  ({h['creado']})")