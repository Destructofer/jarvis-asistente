"""Bitácora de lo que Jarvis HIZO de verdad (cada herramienta que ejecutó), para que nunca
afirme algo que no hizo.

Pasó: con Mortal Kombat abierto le preguntaron "¿estás jugando por mí?" y el modelo contestó
"claro, aquí ando jugando por ti" sin haber mandado una sola tecla (le dio la razón al usuario).
Ahora:
- genesis.ejecutar_herramienta anota aquí cada herramienta (anotar),
- "¿estás jugando por mí?", "¿fuiste tú?", "¿tú moviste el mouse?" se contestan sin el modelo,
  con lo que dice la bitácora (skill que_hice; atajo en genesis.atajo_que_hice),
- y si la pregunta es de otra forma, el prompt lleva el resumen de la bitácora (contexto).
Solo vive en memoria (no se guarda): es lo de esta sesión.
"""
import re
import threading
import time
import unicodedata
from collections import deque

from skills import skill

_registro = deque(maxlen=300)   # (ts, nombre, args, ok)
_lock = threading.Lock()

# Herramientas que mueven algo en la computadora por ti (teclas, mouse, control, ventanas)
ENTRADA = re.compile(r"tecla|teclado|escribir|escribe|clic|click|mouse|raton|mover|arrastr|desplaz|presion|"
                     r"pulsa|control|gamepad|jugar|juego|atajo_|realidad|enfocar|maximiz|minimiz|cerrar_ventana|"
                     r"elegir_en_pantalla|saltar_anuncios|controlar_reproduccion")


def _norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def anotar(nombre, args=None, ok=True):
    with _lock:
        _registro.append((time.time(), nombre, dict(args or {}), bool(ok)))


def recientes(segundos=600, solo_entrada=False, ahora=None):
    ahora = time.time() if ahora is None else ahora
    with _lock:
        filas = [r for r in _registro if ahora - r[0] <= segundos]
    if solo_entrada:
        filas = [r for r in filas if ENTRADA.search(r[1])]
    return filas


def _hace(segundos):
    s = int(segundos)
    if s < 60:
        return "hace unos segundos"
    m = s // 60
    return "hace 1 minuto" if m == 1 else f"hace {m} minutos"


def resumen(segundos=600, ahora=None):
    """Una línea para el prompt: lo que de verdad hizo en los últimos minutos."""
    ahora = time.time() if ahora is None else ahora
    filas = recientes(segundos, ahora=ahora)
    if not filas:
        return (f"En los últimos {segundos // 60} minutos NO ejecutaste ninguna herramienta: no "
                "mandaste teclas, clics ni control de juego, no abriste ni moviste nada.")
    partes = [f"{n} ({_hace(ahora - ts)}{'' if ok else ', falló'})" for ts, n, _, ok in filas[-8:]]
    return f"Herramientas que SÍ ejecutaste en los últimos {segundos // 60} minutos: " + "; ".join(partes) + "."


REGLA = ("\n\nHONESTIDAD SOBRE TUS ACCIONES: solo puedes decir que hiciste, estás haciendo o "
         "controlas algo (jugar, mover el mouse, apretar teclas o botones, escribir, abrir o cerrar "
         "algo) si lo hiciste con una herramienta; la lista de abajo es la verdad. Si te preguntan "
         "si tú estás haciendo algo que no aparece ahí, di claramente que no fuiste tú. No le des la "
         "razón al usuario solo porque lo afirma o insiste.\n")

# "¿estás jugando por mí?", "¿tú tomaste el control?", "¿fuiste tú el que movió el mouse?"
PREGUNTA = re.compile(
    r"\b(estas|tu estas|andas|eres tu|fuiste tu|tu fuiste|tu tomaste|tomaste)\b.{0,40}?\b("
    r"jugando|juegas|jugaste|moviendo|moviste|controlando|controlas|el control|manejando|escribiendo|"
    r"escribiste|picando|apretando|presionando|tecleando|haciendo clic|clics?|el mouse|el raton|"
    r"el que (juega|jugo|mueve|movio|escribe|escribio))\b")


def es_pregunta(texto):
    t = _norm(texto)
    return bool(PREGUNTA.search(t)) and ("?" in (texto or "") or re.match(
        r"\s*(jarvis\W*)?(oye\W*)?(estas|tu|eres|fuiste|tomaste|andas)\b", t) is not None)


@skill("que_hice",
       "Dice con la verdad si Jarvis está haciendo o hizo algo en la computadora (jugar, mover el "
       "mouse, apretar teclas, escribir) según lo que de verdad ejecutó: '¿estás jugando por mí?', "
       "'¿fuiste tú?', '¿tú moviste el mouse?', '¿qué hiciste?'.", terminal=True)
def que_hice():
    entrada = recientes(600, solo_entrada=True)
    if not entrada:
        return ("No, no fui yo. En los últimos diez minutos no he mandado ninguna tecla, clic ni "
                "control. Si el juego se mueve solo, puede ser su demostración, un modo de la "
                "computadora contra la computadora o un video.")
    ts, nombre, _, ok = entrada[-1]
    return (f"Sí: lo último que hice fue {nombre.replace('_', ' ')} {_hace(time.time() - ts)}"
            + ("." if ok else ", aunque falló."))
