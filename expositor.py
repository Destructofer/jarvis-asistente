"""Modo expositor: Jarvis como "copresentador" que habla con el público desde la computadora
mientras tú expones con los lentes puestos.

- Lo que responde se escucha por las bocinas de la PC / del proyector (salida_publico), no en
  tus lentes.
- Puede "mirar" por la cámara de los lentes (ver camara.py) y comentar lo que tienes enfrente:
  saludar al público, describir un objeto que les muestras, leer un letrero o pizarrón.
- Conoce el contenido de tu presentación (presentacion.py), así que puede explicar una
  diapositiva o contestar una pregunta del público sobre el tema.
"""
import re
import threading
import time

import camara
import cerebro
import hud
import presentacion
import skills
import vision
from skills import Callado, skill

ACTIVO = False
hablar_publico = None   # fn(texto): lo pone genesis.py; habla por la salida del público
_cambios = []           # funciones a avisar cuando se activa/desactiva (icono de la bandeja)

ESTILO = (
    "Hablas como un mayordomo digital al estilo de J.A.R.V.I.S.: elegante, seguro, preciso y "
    "con un humor fino y breve. En español de México neutro. Frases cortas que suenen bien en "
    "voz alta; nada de listas, emojis, markdown ni símbolos.")


def _cfg():
    return skills._CFG


def _nombre():
    return _cfg().get("name", "Jarvis")


def _presentador():
    return (_cfg().get("expositor", {}) or {}).get("presentador", "") or "el expositor"


def al_cambiar(fn):
    _cambios.append(fn)


def cambiar_modo(valor=True):
    global ACTIVO
    ACTIVO = bool(valor)
    if ACTIVO:
        threading.Thread(target=camara.calentar, args=(_cfg(),), daemon=True).start()
    hud.modo_expositor(ACTIVO)
    for fn in _cambios:
        try:
            fn(ACTIVO)
        except Exception:
            pass


def prompt_extra():
    if not ACTIVO:
        return ""
    estilo = (_cfg().get("expositor", {}) or {}).get("estilo") or ESTILO
    return (
        f"\n\nMODO EXPOSITOR ACTIVO: estás en una exposición en vivo junto a {_presentador()}. "
        "Todo lo que respondes se escucha por las bocinas frente al público, así que habla para "
        f"todos. {estilo} Normalmente responde en 1 a 3 frases; si te piden explicar algo, "
        "máximo unas 5 frases. Si te piden ver, mirar, saludar al público o describir lo que "
        f"{_presentador()} tiene enfrente, usa la herramienta mirar (o presentarse_al_publico "
        "para presentarte). No confirmes en voz alta cada cambio de diapositiva.")


def _sistema():
    estilo = (_cfg().get("expositor", {}) or {}).get("estilo") or ESTILO
    s = f"Eres {_nombre()}, el asistente de inteligencia artificial de {_presentador()}. {estilo}"
    if ACTIVO:
        s += (" Estás en una exposición en vivo: lo que digas se escucha por las bocinas frente "
              "al público. La imagen viene de la cámara de los lentes inteligentes que "
              f"{_presentador()} lleva puestos, así que es exactamente lo que él está viendo.")
    else:
        s += (" La imagen viene de la cámara de los lentes inteligentes del usuario: es lo que "
              "él está viendo.")
    try:
        ctx = presentacion.contexto(2500)
    except Exception:
        ctx = ""
    return s + ctx


def _decir_texto_sin_imagen(instruccion):
    """Plan B si la cámara no responde: el mismo mensaje, sin describir la imagen."""
    r = cerebro.chat(_cfg(), [{"role": "system", "content": _sistema()},
                              {"role": "user", "content": instruccion}], [], 0.7)
    return (r.get("content") or "").strip()


# ---------- Skills ----------
@skill("modo_expositor",
       "Activa o desactiva el modo expositor: Jarvis habla con el público por las bocinas de la "
       "computadora durante una exposición y puede ver por la cámara de los lentes. Úsala con "
       "'modo presentación', 'modo expositor', 'ya terminé de exponer'.",
       {"activar": {"type": "boolean", "description": "true para activar, false para desactivar"}})
def modo_expositor(activar=True):
    if isinstance(activar, str):  # algunos modelos mandan "true"/"false" como texto
        activar = activar.strip().lower() in ("true", "1", "si", "sí", "activar")
    cambiar_modo(activar)
    if ACTIVO:
        return "Modo expositor activado. A partir de ahora hablo para el público."
    return "Modo expositor desactivado. Vuelvo a hablar solo contigo."


@skill("mirar",
       "Mira por la cámara de los lentes del usuario (lo que él está viendo: el público, un "
       "objeto que muestra, un pizarrón, un letrero) o por la pantalla de la computadora, y "
       "responde. Úsala para '¿qué ves?', 'saluda al público', 'describe lo que tengo en la "
       "mano', 'lee ese letrero', 'cuántas personas hay', 'explica lo que se ve en pantalla'.",
       {"pregunta": {"type": "string", "description": "Qué quiere saber o que diga el usuario, con sus palabras"},
        "fuente": {"type": "string", "enum": ["lentes", "pantalla"],
                   "description": "lentes (por defecto) o pantalla de la computadora"}},
       requeridos=["pregunta"], externo=True)
def mirar(pregunta, fuente="lentes"):
    cfg = _cfg()
    hud.estado("mirando")
    try:
        img = camara.capturar(cfg, "pantalla" if fuente == "pantalla" else None)
    except camara.CamaraError as e:
        return str(e)
    hud.mostrar_imagen(camara.ULTIMA_PATH)
    instruccion = (f"{pregunta}\n\nResponde en voz alta como {_nombre()}, en español, en 1 a 3 "
                   "frases naturales (máximo 5 si te piden describir con detalle). No digas "
                   "'en la imagen'; habla como si lo estuvieras viendo en persona.")
    try:
        return vision.ver(cfg, img, instruccion, _sistema())
    except RuntimeError as e:
        return str(e)


@skill("presentarse_al_publico",
       "Jarvis se presenta ante el público: saluda, mira al público por la cámara de los lentes "
       "y comenta brevemente lo que ve, y presenta el tema de la exposición. Úsala con "
       "'preséntate', 'saluda a todos', 'di hola al público'.",
       {"tema": {"type": "string", "description": "Opcional: tema de la exposición si el usuario lo dijo"}},
       requeridos=[])
def presentarse_al_publico(tema=""):
    cfg = _cfg()
    extra = (cfg.get("expositor", {}) or {}).get("presentacion_extra", "")
    if not tema:
        try:
            pres = presentacion._presentacion_activa()
            tema = pres.Name.rsplit(".", 1)[0] if pres is not None else ""
        except Exception:
            tema = ""
    instruccion = (
        f"Preséntate ante el público: eres {_nombre()}, la inteligencia artificial que acompaña "
        f"a {_presentador()} en esta exposición" + (f" sobre '{tema}'" if tema else "") + ". "
        "Saluda al público, comenta en una frase algo amable de lo que ves (cuánta gente hay, el "
        "ambiente) y cede la palabra con elegancia. Máximo 4 frases. " + extra)
    hud.estado("mirando")
    try:
        img = camara.capturar(cfg)
        hud.mostrar_imagen(camara.ULTIMA_PATH)
        return vision.ver(cfg, img, instruccion, _sistema())
    except (camara.CamaraError, RuntimeError) as e:
        print(f"[Presentación sin cámara: {e}]")
        try:
            return _decir_texto_sin_imagen(instruccion.replace(
                "comenta en una frase algo amable de lo que ves (cuánta gente hay, el ambiente) y ", ""))
        except Exception:
            return (f"Buenas tardes a todos. Soy {_nombre()}, la inteligencia artificial que "
                    f"acompaña a {_presentador()} hoy. Adelante.")


@skill("hablar_al_publico",
       "Dice un mensaje en voz alta por las bocinas de la computadora para el público, aunque "
       "el modo expositor esté apagado. Úsala con 'dile al público que...', 'anuncia que...'. "
       "En 'mensaje' pon exactamente lo que se va a decir, ya redactado con tu estilo.",
       {"mensaje": {"type": "string", "description": "Texto final a decir en voz alta"}})
def hablar_al_publico(mensaje):
    if hablar_publico is None:
        return "No puedo hablar por las bocinas ahora."
    hablar_publico(mensaje)
    return Callado(mensaje)


def _separar(texto, n):
    """El modelo responde con las partes separadas por '---'; si no respeta el formato, se
    reparte el texto por frases en n partes parecidas."""
    partes = [p.strip() for p in re.split(r"\n\s*-{3,}\s*\n|^\s*-{3,}\s*$", texto, flags=re.M) if p.strip()]
    partes = [re.sub(r"^\s*(parte|secci[oó]n)\s*\d+\s*[:.-]\s*", "", p, flags=re.I) for p in partes]
    if len(partes) == n:
        return partes
    frases = [f for f in re.split(r"(?<=[.!?])\s+", " ".join(partes)) if f]
    tam = max(1, -(-len(frases) // n))  # división hacia arriba: no se pierde texto
    trozos = [" ".join(frases[j:j + tam]) for j in range(0, len(frases), tam)]
    return (trozos + [""] * n)[:n]


@skill("recorrer_y_explicar",
       "Recorre la página o documento de la ventana que está al frente y lo explica en voz alta "
       "parte por parte, bajando la pantalla solo mientras habla, como si lo estuviera "
       "exponiendo. Úsala con 'explica la página mientras bajas', 'recórrela y explícala', "
       "'preséntale al público esta página', 'dales un tour por el sistema'.",
       {"partes": {"type": "integer", "description": "Cuántas pantallas recorrer (2 o 3; 3 por defecto)"},
        "enfoque": {"type": "string", "description": "Opcional: en qué fijarse o a quién va dirigido"}},
       requeridos=[])
def recorrer_y_explicar(partes=3, enfoque=""):
    import control
    from PIL import ImageChops, ImageGrab

    cfg = _cfg()
    n = max(1, min(3, int(partes or 3)))  # Groq acepta máximo 3 imágenes por petición
    h = control.ventana_activa()
    if not h:
        return "No hay ninguna ventana al frente que pueda recorrer."
    import win32gui
    control.traer_al_frente(h)
    caja = win32gui.GetWindowRect(h)
    hud.estado("mirando")

    # 1) Escaneo rápido: arriba del todo y una foto por pantalla (se ve como "Jarvis analizando")
    import pyautogui
    pyautogui.press("home")
    time.sleep(0.6)
    fotos = []
    for i in range(n):
        foto = ImageGrab.grab(bbox=caja, all_screens=True)
        if fotos and not ImageChops.difference(foto.convert("L"), fotos[-1].convert("L")).getbbox():
            break  # la página ya no bajó más: se llegó al final
        fotos.append(foto)
        if i < n - 1:
            pyautogui.press("pagedown")
            time.sleep(0.7)
    pyautogui.press("home")

    # 2) Una sola petición con todas las fotos: la explicación sale con hilo, sin repetir
    k = len(fotos)
    instruccion = (
        f"Estas {k} imágenes son la misma página vista de arriba hacia abajo. Explícala en voz "
        f"alta como si la estuvieras exponiendo, en exactamente {k} partes, una por imagen y en "
        "orden, separadas por una línea que diga solo ---. Cada parte: 2 o 3 frases naturales "
        "sobre lo que se ve en ESA imagen, conectadas con la anterior; no digas 'en la imagen' "
        "ni 'en la primera parte'; la primera parte presenta qué es la página."
        + (f" Enfoque: {enfoque}." if enfoque else ""))
    try:
        texto = vision.ver_varias(cfg, fotos, instruccion, _sistema())
    except RuntimeError as e:
        return str(e)
    textos = _separar(texto, k)

    # 3) Exposición: habla cada parte y baja la página entre una y otra
    for i, t in enumerate(textos):
        if not t:
            continue
        if hablar_publico is not None and ACTIVO:
            hablar_publico(t)
        elif _hablar_normal is not None:
            _hablar_normal(t)
        if i < k - 1:
            control.traer_al_frente(h)
            pyautogui.press("pagedown")
            time.sleep(0.4)
    return Callado("Recorrido terminado.")


_hablar_normal = None   # lo pone genesis.py: habla por la salida normal (fuera de modo expositor)
