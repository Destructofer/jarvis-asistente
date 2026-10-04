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
    # Conserva solo los últimos 500 mensajes
    _q("DELETE FROM mensajes WHERE id NOT IN "
       "(SELECT id FROM mensajes ORDER BY id DESC LIMIT 500)", escribir=True)


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
        if not hits:
            return "No tengo nada guardado sobre ese tema."
    else:
        hits = listar_hechos()
        if not hits:
            return "Todavía no tengo nada guardado."
    return "Recuerdos: " + " | ".join(h["texto"] for h in hits)


@skill("olvidar",
       "Borra de la memoria permanente los datos que coincidan con un tema. Úsala cuando el "
       "usuario pida olvidar o borrar algo que le recordaste. Pide la confirmación por sí misma.",
       {"tema": {"type": "string", "description": "Tema o dato a olvidar"}})
def olvidar(tema):
    if not _activa():
        return MSG_OFF
    hits = buscar(tema)
    if not hits:
        return "No encontré nada guardado sobre ese tema."
    if pedir_confirmacion is None:
        return "No puedo pedir confirmación ahora, así que no borré nada."
    if len(hits) <= 3:
        pregunta = "Voy a borrar: " + "; ".join(h["texto"] for h in hits) + ". ¿Confirmas?"
    else:
        pregunta = f"Encontré {len(hits)} recuerdos sobre eso. ¿Los borro todos?"
    if not pedir_confirmacion(pregunta):
        return "El usuario canceló. No se borró nada."
    borrar_hechos([h["id"] for h in hits])
    return f"Olvidado ({len(hits)})."


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