import datetime
import re
import sqlite3
import threading
import time

import pyautogui

import memoria
from skills import skill

_notificar = None
_hilo = None

HORA = re.compile(r"(\d{1,2})(?:\s*[:.h]\s*(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?", re.I)


# ---------- Base de datos ----------
def _q(sql, params=(), escribir=False):
    memoria.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(memoria.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.execute("CREATE TABLE IF NOT EXISTS avisos ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "cuando TEXT NOT NULL, texto TEXT NOT NULL)")
        filas = con.execute(sql, params).fetchall()
        if escribir:
            con.commit()
        return filas
    finally:
        con.close()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _agregar(cuando, texto):
    _q("INSERT INTO avisos (cuando, texto) VALUES (?, ?)", (_iso(cuando), texto), escribir=True)


def _cuando_legible(dt):
    ahora = datetime.datetime.now()
    if dt.date() == ahora.date():
        return f"hoy a las {dt:%H:%M}"
    if dt.date() == ahora.date() + datetime.timedelta(days=1):
        return f"mañana a las {dt:%H:%M}"
    return f"el {dt:%d/%m} a las {dt:%H:%M}"


# ---------- Hilo que vigila los avisos ----------
def _vigilar():
    try:  # necesario para que pyttsx3 (voz de Windows) funcione fuera del hilo principal
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass
    while True:
        try:
            vencidos = _q("SELECT id, cuando, texto FROM avisos WHERE cuando <= ? ORDER BY cuando",
                          (_iso(datetime.datetime.now()),))
            for a in vencidos:
                _q("DELETE FROM avisos WHERE id = ?", (a["id"],), escribir=True)
                retraso = datetime.datetime.now() - datetime.datetime.fromisoformat(a["cuando"])
                prefijo = "Tenías pendiente: " if retraso.total_seconds() > 120 else ""
                if _notificar:
                    _notificar(prefijo + a["texto"])
        except Exception as e:
            print(f"[Error en avisos: {type(e).__name__}: {str(e)[:100]}]")
        time.sleep(1)


def iniciar(notificar):
    """notificar(texto) lo instala genesis.py; se llama cuando vence un aviso."""
    global _notificar, _hilo
    _notificar = notificar
    if _hilo is None:
        _hilo = threading.Thread(target=_vigilar, daemon=True, name="avisos")
        _hilo.start()


# ---------- Skills ----------
@skill("temporizador",
       "Pone un temporizador o cuenta atrás. Convierte la duración a segundos "
       "(5 minutos = 300, 1 hora = 3600). Avisa en voz alta cuando termina.",
       {"segundos": {"type": "integer", "description": "Duración total en segundos"},
        "texto": {"type": "string", "description": "Qué avisar al terminar (opcional)"}},
       requeridos=["segundos"])
def temporizador(segundos, texto=""):
    segundos = int(segundos)
    if not 1 <= segundos <= 7 * 24 * 3600:
        return "La duración debe estar entre 1 segundo y 7 días."
    fin = datetime.datetime.now() + datetime.timedelta(seconds=segundos)
    _agregar(fin, texto.strip() or "Tu temporizador ha terminado.")
    m, s = divmod(segundos, 60)
    h, m = divmod(m, 60)
    partes = [f"{h} h" if h else "", f"{m} min" if m else "", f"{s} s" if s else ""]
    return f"Temporizador de {' '.join(p for p in partes if p)} en marcha."


@skill("recordatorio",
       "Programa un recordatorio o alarma para una hora concreta. La hora va en formato 24 h "
       "HH:MM (las 7 de la tarde = 19:00). Sin fecha, es la próxima vez que llegue esa hora.",
       {"hora": {"type": "string", "description": "Hora en formato HH:MM de 24 h"},
        "texto": {"type": "string", "description": "Qué recordar"},
        "fecha": {"type": "string", "description": "Fecha YYYY-MM-DD (opcional)"}},
       requeridos=["hora", "texto"])
def recordatorio(hora, texto, fecha=""):
    m = HORA.fullmatch(str(hora).strip())
    if not m:
        return f"No entendí la hora '{hora}'. Usa el formato HH:MM."
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    sufijo = (m.group(3) or "").lower().replace(".", "").replace(" ", "")
    if sufijo == "pm" and h < 12:
        h += 12
    elif sufijo == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return f"La hora '{hora}' no es válida."

    ahora = datetime.datetime.now()
    try:
        if fecha:
            dia = datetime.date.fromisoformat(fecha.strip())
            cuando = datetime.datetime.combine(dia, datetime.time(h, mi))
        else:
            cuando = ahora.replace(hour=h, minute=mi, second=0, microsecond=0)
            if cuando <= ahora:
                cuando += datetime.timedelta(days=1)
    except ValueError:
        return f"La fecha '{fecha}' no es válida. Usa YYYY-MM-DD."
    if cuando <= ahora:
        return "Esa fecha y hora ya pasaron."
    _agregar(cuando, texto.strip())
    return f"Recordatorio programado para {_cuando_legible(cuando)}: {texto.strip()}."


@skill("listar_avisos", "Lista los temporizadores y recordatorios pendientes.", terminal=False)
def listar_avisos():
    filas = _q("SELECT cuando, texto FROM avisos ORDER BY cuando")
    if not filas:
        return "No tienes avisos pendientes."
    return "Pendientes: " + " | ".join(
        f"{_cuando_legible(datetime.datetime.fromisoformat(f['cuando']))}: {f['texto']}"
        for f in filas)


@skill("cancelar_avisos",
       "Cancela temporizadores o recordatorios. Con un tema cancela solo los que lo mencionen; "
       "sin tema cancela todos.",
       {"tema": {"type": "string", "description": "Palabra a buscar en el aviso (opcional)"}},
       requeridos=[])
def cancelar_avisos(tema=""):
    filas = _q("SELECT id, texto FROM avisos")
    tema = tema.strip().lower()
    ids = [f["id"] for f in filas if not tema or tema in f["texto"].lower()]
    if not ids:
        return "No encontré avisos que cancelar."
    _q(f"DELETE FROM avisos WHERE id IN ({','.join('?' * len(ids))})", tuple(ids), escribir=True)
    return f"Cancelados {len(ids)} avisos."


@skill("musica",
       "Controla la reproducción de música o vídeo: pausar o reanudar, siguiente, anterior, detener.",
       {"accion": {"type": "string",
                   "enum": ["pausar_reanudar", "siguiente", "anterior", "detener"],
                   "description": "Acción a realizar"}})
def musica(accion):
    teclas = {"pausar_reanudar": "playpause", "siguiente": "nexttrack",
              "anterior": "prevtrack", "detener": "stop"}
    if accion not in teclas:
        return "Acción de música no válida."
    pyautogui.press(teclas[accion])
    return {"pausar_reanudar": "Listo, alterné pausa y reproducción.",
            "siguiente": "Siguiente pista.", "anterior": "Pista anterior.",
            "detener": "Reproducción detenida."}[accion]


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    memoria.DB_PATH = Path(tempfile.mkdtemp()) / "prueba.db"
    iniciar(lambda t: print("AVISO:", t))
    print(temporizador(2, "Prueba de temporizador"))
    print(recordatorio("7:30 pm", "Cena"))
    print(listar_avisos())
    time.sleep(4)
    print(listar_avisos())
    print(cancelar_avisos())
