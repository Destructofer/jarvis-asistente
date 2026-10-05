"""Jarvis te ve: por la cámara de la computadora (camara.usuario_indice) nota si estás frente a
la PC y, como lo haría un compañero que está a tu lado:

- Te saluda cuando llegas (al encender Jarvis o al volver tras un rato fuera), con algo que note
  si viene al caso ("¿ya con café?"), y se queda escuchando tu respuesta sin que digas "Jarvis".
- Mientras platican, cada cierto tiempo echa un vistazo (presencia.describir_cada_seg) y lo
  tiene presente al contestar: si te ve cansado o mostrando algo, lo puede tomar en cuenta.
- Con la skill mirar_usuario te mira cuando se lo pides ("¿cómo me veo?", "¿qué tengo aquí?").

Si estás o no frente a la PC se sabe EN LA COMPUTADORA (MediaPipe, gratis y sin internet); solo
los vistazos y los saludos mandan una foto pequeña (640 px) al modelo de visión. Nunca intenta
identificar a nadie. "Deja de verme" lo apaga todo (cámara incluida).
"""
import threading
import time

import camara
import skills
import vision
from skills import Callado, skill

# Los pone genesis.py
puede_hablar = None   # fn() -> bool: Jarvis libre (sin orden en curso ni voz sonando)
intervenir = None     # fn(texto): lo dice, lo anota en la conversación y escucha la respuesta
actividad = None      # fn() -> momento de la última orden (para saber si están platicando)

_estado = {"hilo": None, "manual": None, "error": "", "ultimo_saludo": 0.0, "vista": None,
           "ultimo_vistazo": 0.0}
_presencia = None     # la instancia de Presencia del hilo (para contexto())


def _cfg():
    return skills._CFG or {}


def _conf():
    return _cfg().get("presencia", {}) or {}


def activo():
    if _estado["manual"] is not None:
        return _estado["manual"]
    return bool(_conf().get("activo", True))


def _nombre_usuario():
    return ((_cfg().get("expositor") or {}).get("presentador") or "").strip()


class Presencia:
    """¿Estás frente a la PC? Con margen para no "irte" por voltear un momento: llegas tras
    llegar_seg viéndote seguido y te vas tras ausencia_seg sin verte. Separada de la cámara
    para poder probarla con datos inventados."""

    def __init__(self, conf=None, ahora=0.0):
        conf = conf or {}
        self.llegar = float(conf.get("llegar_seg", 1.0))
        self.irse = float(conf.get("ausencia_seg", 15))
        self.presente = False
        self.visto_desde = None
        self.ultimo_visto = None
        self.ausente_desde = None   # None = aún no te ha visto desde que arrancó

    def actualizar(self, t, cara):
        """Devuelve ('llego', segundos_fuera o None si es la primera vez), ('se_fue', None) o None."""
        if cara:
            self.ultimo_visto = t
            if self.visto_desde is None:
                self.visto_desde = t
            if not self.presente and t - self.visto_desde >= self.llegar:
                self.presente = True
                fuera = None if self.ausente_desde is None else t - self.ausente_desde
                return ("llego", fuera)
            return None
        self.visto_desde = None
        if self.presente and self.ultimo_visto is not None and t - self.ultimo_visto >= self.irse:
            self.presente = False
            self.ausente_desde = self.ultimo_visto
            return ("se_fue", None)
        return None


def debe_saludar(fuera, ahora, ultimo_saludo, conf, ocupado=False):
    """¿Toca saludar al verte llegar? La primera vez (si presencia.saludar_al_iniciar) o tras
    presencia.saludar_tras_min fuera, sin repetir antes de presencia.pausa_saludos_min."""
    if ocupado or not conf.get("saludar", True):
        return False
    if ahora - ultimo_saludo < float(conf.get("pausa_saludos_min", 20)) * 60:
        return False
    if fuera is None:
        return bool(conf.get("saludar_al_iniciar", True))
    return fuera >= float(conf.get("saludar_tras_min", 5)) * 60


# ---------- Cámara ----------
def _detector():
    import mediapipe as mp  # noqa: F401
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    import gestos
    opciones = mp_vision.FaceDetectorOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(gestos.modelo("blaze_face_short_range.tflite"))),
        running_mode=mp_vision.RunningMode.IMAGE, min_detection_confidence=0.6)
    with gestos.silencio_nativo():
        return mp_vision.FaceDetector.create_from_options(opciones)


def hay_cara(detector, cuadro_bgr, tamano_minimo=0.08):
    """¿Hay una cara cerca de la cámara (alguien frente a la PC, no al fondo del cuarto)?"""
    import cv2
    import mediapipe as mp
    alto, ancho = cuadro_bgr.shape[:2]
    rgb = cv2.cvtColor(cuadro_bgr, cv2.COLOR_BGR2RGB)
    r = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    return any(d.bounding_box.width / ancho >= tamano_minimo for d in r.detections)


def _foto():
    return camara.foto_usuario(_cfg())


def _sistema():
    nombre = _nombre_usuario() or "tu compañero"
    return (f"Eres {_cfg().get('name', 'Jarvis')}, la inteligencia artificial que trabaja con "
            f"{nombre} como un compañero más, con personalidad propia. Hablas español de México, "
            "natural, cálido y breve, como una persona que está ahí con él; nada de frases de asistente.")


def _saludo(fuera):
    import datetime
    nombre = _nombre_usuario()
    hora = datetime.datetime.now().strftime("%H:%M")
    situacion = ("Es la primera vez que lo ves desde que te encendieron" if fuera is None else
                 f"Acaba de volver a la computadora después de unos {int(fuera // 60)} minutos fuera")
    instruccion = (f"{situacion} (son las {hora}). Salúdalo como un compañero que lo ve llegar: UNA "
                   "sola frase corta y natural. Si notas algo que valga la pena comentar (trae algo "
                   "en las manos, se ve muy contento o desvelado), puedes mencionarlo con tacto; si no, "
                   f"no describas nada. {'Llámalo ' + nombre + '.' if nombre else ''} Solo la frase.")
    try:
        texto = vision.ver(_cfg(), _foto(), instruccion, _sistema(), max_tokens=120,
                           lado=int(_conf().get("lado", 640)), reglas=vision.REGLAS_USUARIO)
        texto = texto.strip().strip('"')
        if texto and len(texto) < 240:
            return texto
    except Exception as e:
        print(f"[Presencia: no pude armar el saludo con la cámara: {type(e).__name__}]")
    return f"¡Qué onda{', ' + nombre if nombre else ''}! Aquí ando."


def _saludar(fuera):
    """Espera un momento libre (sin orden en curso ni voz sonando) y saluda."""
    import expositor
    if expositor.ACTIVO:
        return  # frente al público no se saluda a uno solo
    limite = time.time() + 10
    while time.time() < limite:
        if puede_hablar is None or puede_hablar():
            _estado["ultimo_saludo"] = time.time()
            texto = _saludo(fuera)
            print(f"[Presencia: te vi llegar → {texto}]")
            if intervenir is not None:
                intervenir(texto)
            return
        time.sleep(0.5)


def _platicando():
    """¿Hubo una orden hace poco? Solo entonces vale la pena gastar vistazos en la nube."""
    if actividad is None:
        return False
    return time.time() - actividad() < float(_conf().get("platicando_min", 5)) * 60


def _vistazo():
    instruccion = ("En UNA frase neutral: qué está haciendo la persona frente a la cámara y cómo se "
                   "le ve (expresión, postura, qué tiene en las manos o muestra). Si no hay nadie, "
                   "di 'no hay nadie'.")
    try:
        texto = vision.ver(_cfg(), _foto(), instruccion, "Eres los ojos de un asistente. Respondes "
                           "en español, una frase.", max_tokens=80, lado=int(_conf().get("lado", 640)),
                           reglas=vision.REGLAS_USUARIO)
        if texto:
            _estado["vista"] = (time.time(), texto.strip())
    except Exception as e:
        print(f"[Presencia: el vistazo falló: {type(e).__name__}: {str(e)[:80]}]")


def _bucle():
    global _presencia
    try:
        detector = _detector()
    except Exception as e:
        _estado["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"[Presencia: no pude cargar MediaPipe ({_estado['error']})]")
        return
    _presencia = Presencia(_conf(), time.time())
    cada = float(_conf().get("revisar_cada_seg", 0.5))
    while activo():
        if camara.EXTERNO is not None:
            # Realidad aumentada activa: la cámara es suya (y te está viendo de frente). Revisar
            # la cara y mandar vistazos a la IA ahí solo le quitaba tiempo a las manos.
            time.sleep(1)
            continue
        try:
            cuadro, _ = camara.cuadro_usuario(_cfg())
        except camara.CamaraError as e:
            print(f"[Presencia: {e} Reintento en 15 s.]")
            time.sleep(15)
            continue
        if cuadro is None:
            time.sleep(0.2)
            continue
        ahora = time.time()
        try:
            cara = hay_cara(detector, cuadro, float(_conf().get("tamano_cara", 0.08)))
        except Exception as e:
            print(f"[Presencia: falló la detección: {type(e).__name__}: {str(e)[:80]}]")
            time.sleep(2)
            continue
        evento = _presencia.actualizar(ahora, cara)
        if evento and evento[0] == "llego":
            print("[Presencia: te veo frente a la computadora]")
            if debe_saludar(evento[1], ahora, _estado["ultimo_saludo"], _conf()):
                threading.Thread(target=_saludar, args=(evento[1],), daemon=True,
                                 name="presencia-saludo").start()
        elif evento and evento[0] == "se_fue":
            print("[Presencia: ya no te veo frente a la computadora]")
            _estado["vista"] = None
        cada_vistazo = float(_conf().get("describir_cada_seg", 120))
        if (cada_vistazo > 0 and _presencia.presente and _platicando()
                and ahora - _estado["ultimo_vistazo"] >= cada_vistazo):
            _estado["ultimo_vistazo"] = ahora
            threading.Thread(target=_vistazo, daemon=True, name="presencia-vistazo").start()
        time.sleep(cada)


def iniciar():
    hilo = _estado["hilo"]
    if not activo() or (hilo is not None and hilo.is_alive()):
        return False
    _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="presencia")
    _estado["hilo"].start()
    return True


def contexto():
    """Para el prompt de genesis.py: lo que Jarvis ve de ti ahora mismo (vacío si nada)."""
    if not activo() or _presencia is None:
        return ""
    nombre = _nombre_usuario() or "tu compañero"
    if not _presencia.presente:
        return (f"\n\nCÁMARA: ahora no ves a {nombre} frente a la computadora (puede estar "
                "hablándote desde otro lado del cuarto).")
    vista = _estado["vista"]
    if vista and time.time() - vista[0] < float(_conf().get("vista_vale_seg", 300)):
        return (f"\n\nLO QUE VES DE {nombre.upper()} POR LA CÁMARA (hace {int(time.time() - vista[0])} s): "
                f"{vista[1]} Úsalo solo si viene al caso, como lo notaría un amigo que está a su "
                "lado; no lo describas en cada respuesta ni digas 'veo por la cámara'.")
    return f"\n\nCÁMARA: {nombre} está frente a la computadora."


@skill("mirar_usuario",
       "Mira al usuario por la cámara de la COMPUTADORA (la que lo ve a él, no los lentes) para "
       "responder algo visual sobre él o lo que te muestra: 'mírame', '¿me ves?', '¿cómo me veo?', "
       "'¿qué tengo en la mano?', '¿qué te parece esto?' (mostrando algo), '¿me veo cansado?', "
       "'¿qué estoy haciendo?'.",
       {"pregunta": {"type": "string", "description": "Lo que quiere saber, con sus palabras"}},
       requeridos=["pregunta"], externo=True)
def mirar_usuario(pregunta):
    import hud
    hud.estado("mirando")
    try:
        img = _foto()
    except camara.CamaraError as e:
        return str(e)
    hud.mostrar_imagen(camara.ULTIMA_PATH)
    _estado["vista"] = None  # lo que diga ahora es más fresco que el último vistazo
    instruccion = (f"{pregunta}\n\nResponde en voz alta, en 1 a 3 frases naturales, como alguien "
                   "que lo está viendo en persona y con opinión propia (si te pregunta qué te "
                   "parece algo, dilo con honestidad y tacto). No digas 'en la imagen' ni 'en la foto'.")
    try:
        return vision.ver(_cfg(), img, instruccion, _sistema(), reglas=vision.REGLAS_USUARIO)
    except RuntimeError as e:
        return str(e)


@skill("vista_usuario",
       "Activa o desactiva que Jarvis te vea por la cámara de la computadora (te saluda al "
       "llegar, nota cómo estás, ve tus gestos): 'deja de verme', 'apaga la cámara', 'ya puedes "
       "verme', 'enciende la cámara'.",
       {"activar": {"type": "boolean", "description": "true para que te vea, false para dejar de verte"}},
       requeridos=["activar"])
def vista_usuario(activar=True):
    import gestos
    if isinstance(activar, str):
        activar = activar.strip().lower() in ("true", "1", "si", "sí", "activar")
    _estado["manual"] = bool(activar)
    gestos._estado["manual"] = bool(activar)
    if activar:
        iniciar()
        gestos.iniciar()
        return Callado("Ya te veo.")
    _estado["vista"] = None
    gestos._estado["raton"] = False
    # sin hilos que la usen, la cámara se apaga sola en unos minutos; se apaga ya
    threading.Timer(1.5, lambda: camara.lector(camara.indice_usuario(_cfg())).parar()).start()
    return Callado("Listo, ya no te veo.")
