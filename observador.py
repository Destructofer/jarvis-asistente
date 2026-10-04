"""Ojos atentos de Jarvis durante la exposición.

En modo expositor mira por la cámara cada pocos segundos (config.json → observador.cada_seg) y
anota en JSON qué ve: la escena, si el expositor está platicando de frente con alguien y si la
persona (o el público) parece tener una DUDA. Con eso:

- Si una duda se sostiene (dos miradas seguidas, o una muy clara), espera a que el expositor
  haga una pausa y pregunta con amabilidad "¿te quedó alguna duda sobre...?" por las bocinas.
  Nunca describe la cara ni pone en evidencia a nadie, y no vuelve a intervenir hasta que pase
  observador.pausa_entre_intervenciones_seg.
- genesis.py usa contexto_marca() para razonar lo que oye sin su nombre: si la cámara ve que
  le estás explicando algo a alguien de frente, eso no son órdenes para Jarvis.
- La escena va al diario visual (expositor.lo_visto), para "¿qué te pareció el público?".

Solo funciona con la cámara configurada (camara.fuente) y cuesta una petición de visión por
mirada: ~600 tokens con la imagen a 640 px.
"""
import json
import re
import threading
import time
from collections import deque

import camara
import cerebro
import escuchar
import skills
import vision
from skills import Callado, skill

# Los pone genesis.py
puede_hablar = None   # fn() -> bool: Jarvis libre (sin orden en curso ni voz sonando)
intervenir = None     # fn(texto): lo dice al público, lo anota en la conversación y escucha la respuesta

_estado = {"hilo": None, "manual": None, "ultima_intervencion": 0.0}
_historial = deque(maxlen=6)   # (momento, observación)

PEDIDO = (
    "Responde SOLO con JSON, sin texto antes ni después:\n"
    '{"escena": "una frase neutral: el lugar, cuánta gente hay más o menos y qué hacen",\n'
    ' "personas": número aproximado,\n'
    ' "platicando_de_frente": true si quien lleva la cámara está frente a una o pocas personas, '
    "cerca, platicando con ellas,\n"
    ' "duda": true si la persona con la que platica (o varias del público) muestra gesto de duda '
    "o confusión: ceño fruncido, cabeza ladeada, gesto de no entender, mano levantada,\n"
    ' "confianza_duda": de 0 a 1, qué tan seguro estás de que SÍ hay duda (0 si no la hay),\n'
    ' "atencion": "alta", "media" o "baja"}\n'
    "Juzga solo la expresión y la actitud, nunca quién es la persona ni su apariencia.")
SISTEMA = "Eres los ojos de un asistente en una exposición. Respondes solo JSON válido."
PREGUNTAS = ["Perdón que interrumpa: ¿te quedó alguna duda? Con gusto lo explico de otra forma.",
             "¿Alguna duda hasta aquí? Con gusto lo aclaramos.",
             "Noto que quizá algo no quedó claro. ¿Quieren que lo repasemos?"]


def _cfg():
    return skills._CFG or {}


def _conf():
    return _cfg().get("observador", {}) or {}


def activo():
    if _estado["manual"] is not None:
        return _estado["manual"]
    return bool(_conf().get("activo", True))


def parsear(texto):
    """El JSON del modelo de visión, normalizado (None si no vino algo usable)."""
    m = re.search(r"\{.*\}", texto or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None

    def booleano(v):
        return v is True or str(v).strip().lower() in ("true", "si", "sí", "1")
    try:
        confianza = max(0.0, min(1.0, float(d.get("confianza_duda", 0) or 0)))
    except (TypeError, ValueError):
        confianza = 0.0
    try:
        personas = int(float(d.get("personas", 0) or 0))
    except (TypeError, ValueError):
        personas = 0
    return {"escena": str(d.get("escena", "")).strip(), "personas": personas,
            "platicando_de_frente": booleano(d.get("platicando_de_frente")),
            "duda": booleano(d.get("duda")), "confianza_duda": confianza,
            "atencion": str(d.get("atencion", "")).strip().lower()}


def debe_intervenir(historial, ahora, ultima_intervencion, conf):
    """¿Hay una duda sostenida y ya pasó suficiente tiempo desde la última intervención? Una sola
    mirada con duda no basta (un gesto pasajero): hacen falta dos seguidas, o una muy clara."""
    if ahora - ultima_intervencion < float(conf.get("pausa_entre_intervenciones_seg", 60)):
        return False
    recientes = [o for t, o in historial if ahora - t <= float(conf.get("ventana_seg", 25))]
    if not recientes or not recientes[-1]["duda"]:
        return False
    umbral = float(conf.get("umbral_duda", 0.7))
    ultima = recientes[-1]["confianza_duda"]
    if ultima >= max(umbral, 0.85):
        return True
    previa = recientes[-2] if len(recientes) >= 2 else None
    return bool(ultima >= umbral and previa and previa["duda"] and previa["confianza_duda"] >= umbral * 0.8)


def contexto_marca(maximo_seg=15):
    """Para genesis.py: qué ve la cámara AHORA, como pista para razonar lo que se oyó."""
    if not _historial:
        return ""
    t, o = _historial[-1]
    if time.time() - t > maximo_seg:
        return ""
    if o["platicando_de_frente"]:
        return "(la cámara ve que el expositor le está explicando algo de frente a alguien) "
    return ""


def _pregunta(o):
    """Una pregunta amable sobre lo que se estaba explicando (el modelo la redacta con lo que se
    dijo en los últimos segundos; si no se puede, una de las de respaldo)."""
    try:
        dicho = escuchar.texto_reciente(30, hasta=time.time())
    except Exception:
        dicho = ""
    a_quien = "a esa persona, de tú" if o["platicando_de_frente"] else "al público, en plural"
    try:
        r = cerebro.chat(_cfg(), [
            {"role": "system", "content": "Eres Jarvis, la inteligencia artificial del equipo que expone. "
                                          "Hablas español de México, cálido y breve."},
            {"role": "user", "content":
                f"Lo que el expositor explicaba hace un momento: «{dicho or 'no se alcanzó a escuchar'}». "
                f"Parece que alguien se quedó con una duda. Escribe UNA sola frase corta para decir en "
                f"voz alta, dirigida {a_quien}, ofreciendo aclarar ese tema en concreto. No menciones "
                "su cara, su gesto ni su apariencia y no lo pongas en evidencia. Solo la frase."}],
            [], 0.6)
        texto = (r.get("content") or "").strip().strip('"')
        if texto and len(texto) < 220:
            return texto
    except Exception as e:
        print(f"[Observador: no pude redactar la pregunta: {type(e).__name__}]")
    return PREGUNTAS[int(time.time()) % len(PREGUNTAS)]


def _intervenir_si_toca(o):
    conf = _conf()
    ahora = time.time()
    if not debe_intervenir(list(_historial), ahora, _estado["ultima_intervencion"], conf):
        return
    if intervenir is None:
        return
    # No se le interrumpe a media frase: se espera una pausa (y que Jarvis esté libre)
    limite = ahora + float(conf.get("espera_pausa_seg", 8))
    while time.time() < limite:
        libre = puede_hablar is None or puede_hablar()
        if libre and escuchar.en_pausa(float(conf.get("pausa_seg", 1.3))):
            _estado["ultima_intervencion"] = time.time()
            pregunta = _pregunta(o)
            print(f"[Observador: parece haber una duda (confianza {o['confianza_duda']:.2f}) → {pregunta}]")
            intervenir(pregunta)
            _historial.clear()
            return
        time.sleep(0.25)


def _ciclo():
    import expositor
    time.sleep(2)  # que la cámara arranque
    while expositor.ACTIVO and activo():
        cada = float(_conf().get("cada_seg", 8))
        inicio = time.time()
        try:
            img = camara.capturar(_cfg())
            o = parsear(vision.ver(_cfg(), img, PEDIDO, SISTEMA, max_tokens=180,
                                   lado=int(_conf().get("lado", 640))))
            if o:
                _historial.append((time.time(), o))
                if o["escena"]:
                    expositor._diario.append((time.time(), o["escena"]))
                _intervenir_si_toca(o)
        except (camara.CamaraError, RuntimeError) as e:
            print(f"[Observador: {str(e)[:100]}]")
            time.sleep(10)  # sin cámara no tiene caso insistir cada pocos segundos
        except Exception as e:
            print(f"[Observador falló: {type(e).__name__}: {str(e)[:100]}]")
        time.sleep(max(1.0, cada - (time.time() - inicio)))


def iniciar():
    """Lo llama expositor.cambiar_modo(True). Un solo hilo; se detiene solo al salir del modo."""
    hilo = _estado["hilo"]
    if not activo() or (hilo is not None and hilo.is_alive()):
        return False
    _estado["hilo"] = threading.Thread(target=_ciclo, daemon=True, name="observador")
    _estado["hilo"].start()
    return True


@skill("observar_publico",
       "Activa o desactiva que Jarvis observe al público por la cámara durante la exposición para "
       "notar si alguien tiene dudas y preguntarle: 'observa al público', 'deja de observar'.",
       {"activar": {"type": "boolean", "description": "true para observar, false para dejar de hacerlo"}})
def observar_publico(activar=True):
    if isinstance(activar, str):
        activar = activar.strip().lower() in ("true", "1", "si", "sí", "activar")
    _estado["manual"] = bool(activar)
    if activar:
        import expositor
        if not expositor.ACTIVO:
            return Callado("Observaré al público cuando estemos en modo expositor.")
        iniciar()
        return Callado("Observando al público.")
    return Callado("Dejo de observar al público.")
